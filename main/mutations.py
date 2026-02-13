import strawberry
from .types import (AuthPayload, RefreshPayload, RegisterPayload, VerifyOTPPayload,
    SentOTPPayload, CheckOTPPayload, ChangePasswordPayload
)
from accounts.models import User, OTP
from django.contrib.auth import authenticate
from .utils import generate_access_token, generate_refresh_token, send_otp_email
from .auth import get_user_from_refresh_token
from django.utils import timezone
from graphql import GraphQLError

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
            return VerifyOTPPayload(success=True, error=None)
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
