import strawberry 
from accounts.models import User

@strawberry.django.type(User)
class UserType:
    id: strawberry.auto
    email: strawberry.auto
    is_active: strawberry.auto

@strawberry.type
class AuthPayload:
    access_token: str
    refresh_token: str
    

