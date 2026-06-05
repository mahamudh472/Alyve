from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.urls import reverse
from .models import Plan, UserSubscription, SubscriptionCloneUsage, SubscriptionTalkTimeUsage
from voice.models import LovedOne
from conversations.models import ConversationSession

User = get_user_model()


class SubscriptionModelsTestCase(TestCase):
    def setUp(self):
        # Create a test user
        self.user = User.objects.create_user(
            email="testuser@example.com",
            password="testpassword123",
            full_name="Test User"
        )
        
        # Create a test plan
        self.plan = Plan.objects.create(
            name="Premium Plan",
            description="Premium features and voice cloning",
            price=19.99,
            clone_limit=5,
            talk_time_limit=3600  # 1 hour
        )

        # Create a loved one for usage reference
        self.loved_one = LovedOne.objects.create(
            user=self.user,
            name="Grandpa",
            relationship="Grandfather"
        )

        # Create a session for usage reference
        self.session = ConversationSession.objects.create(
            user=self.user,
            loved_one=self.loved_one,
            channel=ConversationSession.CHANNEL_VOICE
        )

    def test_plan_creation(self):
        from decimal import Decimal
        plan = Plan.objects.get(name="Premium Plan")
        self.assertEqual(plan.price, Decimal("19.99"))
        self.assertEqual(plan.clone_limit, 5)
        self.assertEqual(plan.talk_time_limit, 3600)
        self.assertTrue(plan.is_active)
        self.assertEqual(str(plan), "Premium Plan ($19.99)")

    def test_user_subscription_creation(self):
        # Create a subscription
        subscription = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan
        )
        self.assertEqual(subscription.user, self.user)
        self.assertEqual(subscription.plan, self.plan)
        self.assertTrue(subscription.is_active)
        self.assertIsNotNone(subscription.start_date)
        self.assertIsNone(subscription.end_date)
        self.assertEqual(str(subscription), f"{self.user.email} - {self.plan.name}")

    def test_subscription_clone_usage_creation(self):
        subscription = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan
        )
        clone_usage = SubscriptionCloneUsage.objects.create(
            subscription=subscription,
            loved_one=self.loved_one
        )
        self.assertEqual(clone_usage.subscription, subscription)
        self.assertEqual(clone_usage.loved_one, self.loved_one)
        self.assertIsNotNone(clone_usage.created_at)
        self.assertTrue(str(clone_usage).startswith(f"Clone usage for {self.user.email}"))

    def test_subscription_talk_time_usage_creation(self):
        subscription = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan
        )
        talk_time_usage = SubscriptionTalkTimeUsage.objects.create(
            subscription=subscription,
            session=self.session,
            duration=300
        )
        self.assertEqual(talk_time_usage.subscription, subscription)
        self.assertEqual(talk_time_usage.session, self.session)
        self.assertEqual(talk_time_usage.duration, 300)
        self.assertIsNotNone(talk_time_usage.created_at)
        self.assertTrue(str(talk_time_usage).startswith(f"Talk time usage (300s) for {self.user.email}"))


class RevenueCatWebhookTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="subscriber@example.com",
            password="password123",
            full_name="Premium Subscriber"
        )
        
        self.plan = Plan.objects.create(
            name="Premium Plan",
            revenuecat_product_id="com.alyve.premium.monthly",
            price=19.99,
            clone_limit=5,
            talk_time_limit=3600
        )
        
        self.webhook_url = reverse("revenuecat-webhook")

    @override_settings(REVENUECAT_WEBHOOK_AUTH_TOKEN="test_secret_token")
    def test_webhook_unauthorized_missing_token(self):
        payload = {"event": {"type": "INITIAL_PURCHASE"}}
        response = self.client.post(
            self.webhook_url,
            data=payload,
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(REVENUECAT_WEBHOOK_AUTH_TOKEN="test_secret_token")
    def test_webhook_unauthorized_invalid_token(self):
        payload = {"event": {"type": "INITIAL_PURCHASE"}}
        response = self.client.post(
            self.webhook_url,
            data=payload,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer wrong_token"
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(REVENUECAT_WEBHOOK_AUTH_TOKEN="test_secret_token")
    def test_webhook_initial_purchase_creates_subscription(self):
        payload = {
            "event": {
                "id": "event_id_123",
                "type": "INITIAL_PURCHASE",
                "app_user_id": str(self.user.id),
                "product_id": "com.alyve.premium.monthly",
                "purchased_at_ms": 1618520286000,
                "expiration_at_ms": 1621112286000,
            }
        }
        response = self.client.post(
            self.webhook_url,
            data=payload,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test_secret_token"
        )
        self.assertEqual(response.status_code, 200)
        
        # Verify subscription is created
        subscription = UserSubscription.objects.get(user=self.user)
        self.assertEqual(subscription.plan, self.plan)
        self.assertTrue(subscription.is_active)
        self.assertIsNotNone(subscription.start_date)
        self.assertIsNotNone(subscription.end_date)

    @override_settings(REVENUECAT_WEBHOOK_AUTH_TOKEN="test_secret_token")
    def test_webhook_expiration_deactivates_subscription(self):
        # Create an existing active subscription
        UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True
        )
        
        payload = {
            "event": {
                "id": "event_id_456",
                "type": "EXPIRATION",
                "app_user_id": str(self.user.id),
                "product_id": "com.alyve.premium.monthly",
                "purchased_at_ms": 1618520286000,
                "expiration_at_ms": 1618523886000,  # Expired timestamp
            }
        }
        response = self.client.post(
            self.webhook_url,
            data=payload,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test_secret_token"
        )
        self.assertEqual(response.status_code, 200)
        
        # Verify subscription is inactive
        subscription = UserSubscription.objects.get(user=self.user)
        self.assertFalse(subscription.is_active)

    @override_settings(REVENUECAT_WEBHOOK_AUTH_TOKEN="test_secret_token")
    def test_webhook_fallback_plan_name_mapping(self):
        payload = {
            "event": {
                "id": "event_id_789",
                "type": "INITIAL_PURCHASE",
                "app_user_id": str(self.user.id),
                "product_id": "Premium Plan",  # Product ID matches plan name
                "purchased_at_ms": 1618520286000,
                "expiration_at_ms": 1621112286000,
            }
        }
        response = self.client.post(
            self.webhook_url,
            data=payload,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test_secret_token"
        )
        self.assertEqual(response.status_code, 200)
        
        # Verify subscription is created using name fallback
        subscription = UserSubscription.objects.get(user=self.user)
        self.assertEqual(subscription.plan, self.plan)



