import strawberry
from .types import AuthPayload
from accounts.models import User
from django.contrib.auth import authenticate
from .utils import generate_access_token, generate_refresh_token

@strawberry.type
class Mutation:
    @strawberry.field
    def ping(self) -> str:
        return "pong"
    
    @strawberry.field
    def login(self, email: str, password: str) -> AuthPayload:
        user = authenticate(email=email, password=password)
        if user is not None:
            # Generate tokens
            access_token = generate_access_token(user)
            refresh_token = generate_refresh_token(user)
            return AuthPayload(access_token=access_token, refresh_token=refresh_token)
        else:
            raise Exception("Invalid credentials")
