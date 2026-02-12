from typing import List
import strawberry
from .types import UserType
from accounts.models import User

@strawberry.type
class Query:
    @strawberry.field
    def users(self) -> List[UserType]:
        return User.objects.all()
    
    @strawberry.field
    def me(self, info) -> UserType:
        user = info.context.get("user")
        if user is None or user.is_anonymous:
            raise Exception("Not authenticated")
        return user
