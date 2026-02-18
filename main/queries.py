import strawberry
from .types import MeResponse, LovedOneType, SiteSettingType
from graphql import GraphQLError
from voice.models import LovedOne
from typing import Optional

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
    def loved_ones(self, info, limit: int=10, offset: int=20, id: Optional[int] = None ) -> list[LovedOneType]:
        user = info.context.get("request").user
        if user is None or user.is_anonymous:
           raise GraphQLError("Authentication failed", extensions={"code": "UNAUTHENTICATED"})
        if id is not None:
            try:
                loved_one = LovedOne.objects.get(id=id, user=user)
                return [loved_one]
            except LovedOne.DoesNotExist:
                raise GraphQLError("Loved one not found", extensions={"code": "NOT_FOUND"})
        return LovedOne.objects.filter(user=user).order_by("-created_at")[offset:offset+limit]

    @strawberry.field
    def site_settings(self) -> Optional['SiteSettingType']:
        from accounts.models import SiteSetting
        try:
            return SiteSetting.objects.first()
        except SiteSetting.DoesNotExist:
            return None
    
