from typing import List
import strawberry
from .types import MeResponse, ErrorType, UserType
from accounts.models import User

@strawberry.type
class Query:
    @strawberry.field
    def users(self) -> List[UserType]:
        return User.objects.all()
    
    @strawberry.field
    def me(self, info) -> MeResponse:
        user = info.context.get("request").user
        print(user)
        if user is None or user.is_anonymous:
            return MeResponse(
                user = None,
                error = ErrorType(message="Authentication failed")
            )
        return MeResponse(
            user = user,
            error = None
        )
