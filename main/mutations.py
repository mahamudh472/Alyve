import strawberry
from .types import (AuthPayload, RefreshPayload, RegisterPayload, VerifyOTPPayload,
    SentOTPPayload, CheckOTPPayload, ChangePasswordPayload, LovedOneType, MarkNotificationReadPayload, UserType
)
from accounts.models import User, OTP, Notification
from django.contrib.auth import authenticate
from .utils import generate_access_token, generate_refresh_token, send_otp_email
from .auth import get_user_from_refresh_token
from django.utils import timezone
from graphql import GraphQLError
from typing import Optional
from strawberry.file_uploads import Upload
from voice.models import LovedOne

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
            return AuthPayload(
                access_token=access_token, 
                refresh_token=refresh_token,
                user=user
            )
        elif user is not None and user.is_active == False:
            raise GraphQLError("Account not activated. Please verify your email.", extensions={"code": "UNAUTHORIZED"})
        else:
            raise GraphQLError("Invalid email or password.", extensions={"code": "UNAUTHORIZED"}) 

    @strawberry.field
    def refresh_token(self, refresh_token: str) -> RefreshPayload:
        user = get_user_from_refresh_token(refresh_token)
        if user is not None:
            new_access_token = generate_access_token(user)
            new_refresh_token = generate_refresh_token(user)
            return RefreshPayload(access_token=new_access_token, refresh_token=new_refresh_token)
        else:
            raise GraphQLError("Invalid refresh token.", extensions={"code": "UNAUTHORIZED"})

    @strawberry.field
    def register(self, name: str, email: str, password: str) -> RegisterPayload:
        # Check email format
        if '@' not in email or '.' not in email.split('@')[-1]:
            raise GraphQLError("Invalid email format.", extensions={"code": "BAD_USER_INPUT"})
        if User.objects.filter(email=email).exists():
            raise GraphQLError("Email already in use.", extensions={"code": "BAD_USER_INPUT"})
        
        user = User.objects.create_user(full_name=name, email=email, password=password, is_active=False)
        user.save()
        try:
            send_otp_email(user)
        except Exception as e:
            print("Error sending OTP email:", e)
            raise GraphQLError("Failed to send OTP email. Please try again later.", extensions={"code": "INTERNAL_SERVER_ERROR"})
        return RegisterPayload(success=True)
    
    @strawberry.field
    def verify_email(self, email: str, otp: int) -> VerifyOTPPayload:
        try:
            user = User.objects.get(email=email)
            if user.is_active:
                raise GraphQLError("Account already activated.", extensions={"code": "BAD_USER_INPUT"})
            otp_record = OTP.objects.filter(user=user, code=otp, is_used=False, expires_at__gt=timezone.now()).first()
            if otp_record is None:
                raise GraphQLError("Invalid or expired OTP.", extensions={"code": "UNAUTHORIZED"})
            user.is_active = True
            user.save()
            otp_record.is_used = True
            otp_record.save()
            return VerifyOTPPayload(success=True, user=user, access_token=generate_access_token(user), refresh_token=generate_refresh_token(user))
        except User.DoesNotExist:
            raise GraphQLError("User not found.", extensions={"code": "NOT_FOUND"})

    @strawberry.field
    def sent_otp(self, email:str) -> SentOTPPayload:
        try:
            user = User.objects.get(email=email)
            send_otp_email(user)
            return SentOTPPayload(success=True)
        except User.DoesNotExist:
            raise GraphQLError("User not found.", extensions={"code": "NOT_FOUND"})
            
    @strawberry.field
    def check_otp(self, email:str, otp:int) -> CheckOTPPayload:
        try:
            user = User.objects.get(email=email)
            otp_record = OTP.objects.filter(user=user, code=otp, is_used=False, expires_at__gt=timezone.now()).first()
            if otp_record is None:
                return CheckOTPPayload(valid=False)
            return CheckOTPPayload(valid=True)
        except User.DoesNotExist:
            raise GraphQLError("User not found.", extensions={"code": "NOT_FOUND"})

    @strawberry.field
    def change_password(self, email:str, otp:int, new_password:str) -> ChangePasswordPayload:
        try:
            user = User.objects.get(email=email)
            otp_record = OTP.objects.filter(user=user, code=otp, is_used=False, expires_at__gt=timezone.now()).first()
            if otp_record is None:
                raise GraphQLError("Invalid or expired OTP.", extensions={"code": "UNAUTHORIZED"})
            user.set_password(new_password)
            user.save()
            otp_record.is_used = True
            otp_record.save()
            return ChangePasswordPayload(success=True)
        except User.DoesNotExist:
            raise GraphQLError("User not found.", extensions={"code": "NOT_FOUND"})
    @strawberry.field
    def create_or_update_loved_one(self, info, id: Optional[int] = None, name: Optional[str] = "", relationship: Optional[str] = "", nickname_for_user: Optional[str] = "", description: Optional[str] = "", speaking_style: Optional[str] = "", catch_phrase: Optional[str]="", core_memories: Optional[str]="", voice_file: Optional[Upload] = None ) -> LovedOneType:
        user = info.context.get("request").user
        if user is None or user.is_anonymous:
           raise GraphQLError("Authentication failed", extensions={"code": "UNAUTHENTICATED"})
        if id is not None:
            try:
                loved_one = LovedOne.objects.get(id=id, user=user)
                loved_one.name = name
                loved_one.relationship = relationship
                loved_one.nickname_for_user = nickname_for_user
                loved_one.description = description
                loved_one.core_memories = core_memories
                loved_one.speaking_style = speaking_style
                loved_one.catch_phrase = catch_phrase
                loved_one.save()
                return loved_one
            except LovedOne.DoesNotExist:
                raise GraphQLError("Loved one not found", extensions={"code": "NOT_FOUND"})
        else:
            loved_one = LovedOne.objects.create(
                user=user,
                name=name,
                relationship=relationship,
                nickname_for_user=nickname_for_user,
                description=description,
                speaking_style=speaking_style,
                catch_phrase=catch_phrase,
                core_memories=core_memories
            )
            if voice_file is not None:
                loved_one.voice_file.save(voice_file.name, voice_file)
                loved_one.save()
            return loved_one
    @strawberry.field
    def mark_notification_read(self, info, id: int) -> MarkNotificationReadPayload:
        user = info.context.get("request").user
        if user is None or user.is_anonymous:
           raise GraphQLError("Authentication failed", extensions={"code": "UNAUTHENTICATED"})
        try:
            notification = Notification.objects.get(id=id, user=user)
            notification.read = True
            notification.save()
            return MarkNotificationReadPayload(success=True)
        except Notification.DoesNotExist:
            raise GraphQLError("Notification not found", extensions={"code": "NOT_FOUND"})

    # Update user fullname, avatar, password, push notification preference
    @strawberry.field
    def update_profile(self, info, full_name: Optional[str] = None, avatar: Optional[Upload] = None, password: Optional[str] = None, push_notifications_enabled: Optional[bool] = None) -> UserType:
        user = info.context.get("request").user
        if user is None or user.is_anonymous:
           raise GraphQLError("Authentication failed", extensions={"code": "UNAUTHENTICATED"})
        if full_name is not None:
            user.full_name = full_name
        if avatar is not None:
            user.avatar.save(avatar.name, avatar)
        if password is not None:
            user.set_password(password)
        if push_notifications_enabled is not None:
            user.push_notifications_enabled = push_notifications_enabled
        user.save()
        return user
