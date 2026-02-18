import strawberry 
from accounts.models import User, SiteSetting
from typing import Optional
from voice.models import LovedOne
from strawberry.scalars import JSON

@strawberry.django.type(User)
class UserType:
    id: strawberry.auto
    full_name: strawberry.auto
    email: strawberry.auto
    is_active: strawberry.auto

@strawberry.type
class MeResponse:
    user: Optional['UserType']

@strawberry.type
class AuthPayload:
    access_token: Optional[str]
    refresh_token: Optional[str]
    user: Optional[UserType]

@strawberry.type
class RegisterPayload:
    success: bool

@strawberry.type
class VerifyOTPPayload:
    success: bool
    user: Optional[UserType]
    
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

@strawberry.django.type(LovedOne)
class LovedOneType:
    id: strawberry.auto
    name: strawberry.auto
    relationship: strawberry.auto
    nickname_for_user: strawberry.auto
    description: strawberry.auto
    core_memories: strawberry.auto
    last_conversation_at: strawberry.auto
    speaking_style: strawberry.auto
    catch_phrase: strawberry.auto
    voice_file: strawberry.auto
    created_at: strawberry.auto

@strawberry.django.type(SiteSetting)
class SiteSettingType:
    privacy_policy: Optional[JSON]
    terms_of_service: Optional[JSON]
    support_email: Optional[str]


