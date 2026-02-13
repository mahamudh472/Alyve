import strawberry 
from accounts.models import User
from typing import Optional

@strawberry.django.type(User)
class UserType:
    id: strawberry.auto
    full_name: strawberry.auto
    email: strawberry.auto
    is_active: strawberry.auto

# @strawberry.type
# class ErrorType:
#     message: str
#     code: Optional[str] = None
#
@strawberry.type
class MeResponse:
    user: Optional['UserType']
    # error: Optional['ErrorType']

@strawberry.type
class AuthPayload:
    access_token: Optional[str]
    refresh_token: Optional[str]
    # error: Optional[ErrorType]

@strawberry.type
class RegisterPayload:
    success: bool
    # error: Optional[ErrorType]

@strawberry.type
class VerifyOTPPayload:
    success: bool
    # error: Optional[ErrorType]
    
@strawberry.type
class RefreshPayload:
    access_token: Optional[str]
    refresh_token: Optional[str]

@strawberry.type
class SentOTPPayload:
    success: bool

@strawberry.type
class CheckOTPPayload:
    valid: bool

@strawberry.type
class ChangePasswordPayload:
    success: bool
