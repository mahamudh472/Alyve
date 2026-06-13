#from os import wait
import strawberry
from django.db.models import Sum
from .types import (
    MeResponse, LovedOneType, SiteSettingType,
    NotificationType, LovedOnePagination, ConversationSessionType, ConversationMessageType,
    QuoteType, PlanInfoType,
)
from graphql import GraphQLError
from voice.models import LovedOne, Quote
from typing import Optional
from accounts.models import SiteSetting, Notification, UserSubscription
from conversations.models import ConversationSession, ConversationMessage

@strawberry.type
class Query:
    @strawberry.field
    def me(self, info) -> MeResponse:
        user = info.context.get("request").user
        print(user)
        if user is None or user.is_anonymous:
           raise GraphQLError("Authentication failed", extensions={"code": "UNAUTHENTICATED"})
        return MeResponse(
            user = user,
        )
    
    @strawberry.field
    def loved_ones(
        self,
        info,
        limit: int = 10,
        offset: int = 0,
        id: Optional[int] = None
    ) -> LovedOnePagination:

        user = info.context.get("request").user
        if user is None or user.is_anonymous:
            raise GraphQLError(
                "Authentication failed",
                extensions={"code": "UNAUTHENTICATED"}
            )

        qs = LovedOne.objects.filter(user=user, name__isnull=False).order_by("-created_at")

        if id is not None:
            try:
                loved_one = qs.get(id=id)
                return LovedOnePagination(
                    total_count=1,
                    items=[loved_one]
                )
            except LovedOne.DoesNotExist:
                raise GraphQLError(
                    "Loved one not found",
                    extensions={"code": "NOT_FOUND"}
                )

        total_count = qs.count()
        items = qs[offset:offset + limit]

        return LovedOnePagination(
            total_count=total_count,
            items=items
        )   

    @strawberry.field
    def conversation_sessions(self, info, limit: int=10, offset: int=0) -> list[ConversationSessionType]:
        user = info.context.get("request").user
        if user is None or user.is_anonymous:
           raise GraphQLError("Authentication failed", extensions={"code": "UNAUTHENTICATED"})
        return ConversationSession.objects.prefetch_related(
            "loved_one",
            "user"
        ).filter(user=user, channel="chat").order_by("-last_activity_at")[offset:offset+limit]

    @strawberry.field
    def conversation_messages(
        self,
        info,
        session_id: Optional[int] = None,
        loved_one_id: Optional[int] = None,
        limit: int = 20,
        cursor: Optional[int] = None,
    ) -> list[ConversationMessageType]:
        user = info.context.get("request").user
        if user is None or user.is_anonymous:
           raise GraphQLError("Authentication failed", extensions={"code": "UNAUTHENTICATED"})

        if session_id is not None:
            try:
                session = ConversationSession.objects.get(id=session_id, user=user)
            except ConversationSession.DoesNotExist:
                raise GraphQLError("Conversation session not found", extensions={"code": "NOT_FOUND"})
        else:
            if loved_one_id is None:
                raise GraphQLError(
                    "loved_one_id is required when session_id is not provided",
                    extensions={"code": "BAD_USER_INPUT"},
                )

            try:
                loved_one = LovedOne.objects.get(id=loved_one_id, user=user)
            except LovedOne.DoesNotExist:
                raise GraphQLError("Loved one not found", extensions={"code": "NOT_FOUND"})

            session = (
                ConversationSession.objects.filter(
                    user=user,
                    loved_one=loved_one,
                    channel=ConversationSession.CHANNEL_CHAT,
                )
                .order_by("-last_activity_at")
                .first()
            )
            if session is None:
                session = ConversationSession.objects.create(
                    user=user,
                    loved_one=loved_one,
                    channel=ConversationSession.CHANNEL_CHAT,
                )

        qs = ConversationMessage.objects.filter(session=session)
        if cursor is not None:
            qs = qs.filter(id__lt=cursor)
        # Take the latest `limit` messages before the cursor, then reverse to chronological order
        messages = list(qs.order_by("-created_at")[:limit])
        return messages

    @strawberry.field
    def site_settings(self) -> Optional['SiteSettingType']:
        try:
            return SiteSetting.objects.first()
        except SiteSetting.DoesNotExist:
            return None

    @strawberry.field
    def quote(self, info) -> Optional[QuoteType]:
        user = info.context.get("request").user

        if user is not None and not user.is_anonymous:
            personal_quote = (
                Quote.objects.filter(quote_type=Quote.QuoteScope.PERSONAL, user=user)
                .order_by("?")
                .first()
            )
            if personal_quote is not None:
                return personal_quote

        return (
            Quote.objects.filter(quote_type=Quote.QuoteScope.GLOBAL)
            .order_by("?")
            .first()
        )
    
    @strawberry.field
    def notifications(self, info, limit: int=10, offset: int=0) -> list[NotificationType]:
        user = info.context.get("request").user
        if user is None or user.is_anonymous:
           raise GraphQLError("Authentication failed", extensions={"code": "UNAUTHENTICATED"})
        return Notification.objects.filter(user=user).order_by("-created_at")[offset:offset+limit]

    @strawberry.field
    def plan_info(self, info) -> Optional[PlanInfoType]:
        user = info.context.get("request").user
        if user is None or user.is_anonymous:
            raise GraphQLError("Authentication failed", extensions={"code": "UNAUTHENTICATED"})

        subscription = UserSubscription.objects.filter(user=user).select_related('plan').first()
        if not subscription:
            return None

        plan = subscription.plan
        clone_usage = subscription.clone_usages.count()
        talk_time_usage = subscription.talk_time_usages.aggregate(total=Sum('duration'))['total'] or 0
        total_loved_ones = LovedOne.objects.filter(user=user).count()

        return PlanInfoType(
            plan_name=plan.name,
            clone_limit=plan.clone_limit,
            clone_usage=clone_usage,
            talk_time_limit=plan.talk_time_limit,
            talk_time_usage=talk_time_usage,
            total_loved_ones=total_loved_ones,
        )

