import strawberry 
from accounts.models import User, SiteSetting, Notification
from typing import Optional
from voice.models import LovedOne
from strawberry.scalars import JSON
from typing import List

@strawberry.type
class ImageType:
    url: str
    width: Optional[int]
    height: Optional[int]

@strawberry.django.type(User)
class UserType:
    id: strawberry.auto
    full_name: strawberry.auto
    email: strawberry.auto
    avatar: Optional[ImageType]
    is_active: strawberry.auto
    push_notifications_enabled : strawberry.auto

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
    access_token: Optional[str]
    refresh_token: Optional[str]
    
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

@strawberry.type
class LovedOnePagination:
    total_count: int
    items: List[LovedOneType]


@strawberry.django.type(SiteSetting)
class SiteSettingType:
    privacy_policy: Optional[JSON]
    terms_of_service: Optional[JSON]
    support_email: Optional[str]

@strawberry.django.type(Notification)
class NotificationType:
    id: strawberry.auto
    title: strawberry.auto
    message: strawberry.auto
    created_at: strawberry.auto
    read: strawberry.auto

@strawberry.type
class MarkNotificationReadPayload:
    success: bool
    
