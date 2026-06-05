from django.shortcuts import render
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import ValidationError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from datetime import datetime, timezone as datetime_timezone
import logging

from .models import User, Plan, UserSubscription

logger = logging.getLogger(__name__)


class RevenueCatWebhookView(APIView):
    """
    Webhook endpoint to handle notifications from RevenueCat.
    Updates UserSubscription state based on incoming purchase, renewal, and expiration events.
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request, *args, **kwargs):
        # 1. Authorization check (optional, if REVENUECAT_WEBHOOK_AUTH_TOKEN is set)
        expected_token = getattr(settings, "REVENUECAT_WEBHOOK_AUTH_TOKEN", None)
        if expected_token:
            auth_header = request.headers.get("Authorization")
            if not auth_header:
                return Response({"error": "Missing authorization header"}, status=status.HTTP_401_UNAUTHORIZED)
            
            # Support both "Bearer <token>" and raw token format
            token = auth_header.split(" ")[-1]
            if token != expected_token:
                return Response({"error": "Invalid authorization token"}, status=status.HTTP_401_UNAUTHORIZED)

        # 2. Extract event payload
        payload = request.data
        if not payload or "event" not in payload:
            return Response({"error": "Invalid payload format"}, status=status.HTTP_400_BAD_REQUEST)

        event = payload["event"]
        event_type = event.get("type")
        app_user_id = event.get("app_user_id")
        product_id = event.get("product_id")

        logger.info(f"Processing RevenueCat event: {event_type} for app_user_id: {app_user_id}, product: {product_id}")

        if not app_user_id or not product_id:
            return Response({"error": "app_user_id and product_id are required"}, status=status.HTTP_400_BAD_REQUEST)

        # 3. Locate user
        user = None
        try:
            user = User.objects.filter(id=app_user_id).first()
        except (ValidationError, ValueError):
            pass  # Handle case where app_user_id is not a valid UUID format

        if not user:
            # Fallback to look up by email
            user = User.objects.filter(email=app_user_id).first()

        if not user:
            logger.warning(f"RevenueCat webhook user not found: {app_user_id}")
            return Response({"error": f"User {app_user_id} not found"}, status=status.HTTP_404_NOT_FOUND)

        # 4. Locate Plan
        plan = Plan.objects.filter(revenuecat_product_id=product_id).first()
        if not plan:
            # Fallback: look up by plan name (case-insensitive)
            plan = Plan.objects.filter(name__iexact=product_id).first()

        if not plan:
            logger.warning(f"RevenueCat webhook plan not found for product: {product_id}")
            return Response({"error": f"Plan for product {product_id} not found"}, status=status.HTTP_404_NOT_FOUND)

        # 5. Determine start and end dates
        purchased_at_ms = event.get("purchased_at_ms")
        expiration_at_ms = event.get("expiration_at_ms")

        start_date = timezone.now()
        if purchased_at_ms:
            try:
                start_date = datetime.fromtimestamp(purchased_at_ms / 1000.0, tz=datetime_timezone.utc)
            except (ValueError, OSError, TypeError):
                pass

        end_date = None
        if expiration_at_ms:
            try:
                end_date = datetime.fromtimestamp(expiration_at_ms / 1000.0, tz=datetime_timezone.utc)
            except (ValueError, OSError, TypeError):
                pass

        # 6. Determine if the subscription is active
        is_active = True
        if event_type in {"EXPIRATION", "REVOCATION"}:
            is_active = False
        elif end_date and end_date < timezone.now():
            is_active = False

        # 7. Update or create the subscription
        subscription, created = UserSubscription.objects.update_or_create(
            user=user,
            defaults={
                "plan": plan,
                "start_date": start_date,
                "end_date": end_date,
                "is_active": is_active,
            }
        )

        logger.info(f"Updated UserSubscription for {user.email}: plan={plan.name}, is_active={is_active}")

        return Response({
            "status": "success",
            "subscription_id": subscription.id,
            "is_active": subscription.is_active,
            "plan": plan.name
        }, status=status.HTTP_200_OK)
