#from os import wait
import strawberry
from .types import (
    MeResponse, LovedOneType, SiteSettingType, 
    NotificationType, LovedOnePagination, ConversationSessionType, ConversationMessageType
)
from graphql import GraphQLError
from voice.models import LovedOne
from typing import Optional
from accounts.models import SiteSetting, Notification
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

        qs = LovedOne.objects.filter(user=user).order_by("-created_at")

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
        ).filter(user=user).order_by("-last_activity_at")[offset:offset+limit]

    @strawberry.field
    def conversation_messages(self, info, session_id: int, limit: int=20, offset: int=0) -> list[ConversationMessageType]:
        user = info.context.get("request").user
        if user is None or user.is_anonymous:
           raise GraphQLError("Authentication failed", extensions={"code": "UNAUTHENTICATED"})
        try:
            session = ConversationSession.objects.get(id=session_id, user=user)
        except ConversationSession.DoesNotExist:
            raise GraphQLError("Conversation session not found", extensions={"code": "NOT_FOUND"})
        return ConversationMessage.objects.filter(session=session).order_by("seq")[offset:offset+limit]

    @strawberry.field
    def site_settings(self) -> Optional['SiteSettingType']:
        try:
            return SiteSetting.objects.first()
        except SiteSetting.DoesNotExist:
            return None
    
    @strawberry.field
    def notifications(self, info, limit: int=10, offset: int=0) -> list[NotificationType]:
        user = info.context.get("request").user
        if user is None or user.is_anonymous:
           raise GraphQLError("Authentication failed", extensions={"code": "UNAUTHENTICATED"})
        return Notification.objects.filter(user=user).order_by("-created_at")[offset:offset+limit]
