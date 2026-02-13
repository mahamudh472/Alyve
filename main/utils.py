import jwt, random, datetime
from django.conf import settings
from datetime import datetime, timedelta
from accounts.models import User, OTP
from django.utils import timezone
from django.core.mail import send_mail

def generate_access_token(user):
    payload = {
        'user_id': str(user.id),
        'exp': datetime.utcnow() + timedelta(minutes=15),  # Access token valid for 15 minutes
        "type": "access"
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

def generate_refresh_token(user):
    payload = {
        'user_id': str(user.id),
        'exp': datetime.utcnow() + timedelta(days=7),  # Refresh token valid for 7 days
        "type": "refresh"
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

def send_otp_email(user):
    otp = random.randint(1000, 9999)  # Generate a 4-digit OTP
    expires_at = timezone.now() + timedelta(minutes=10)  # OTP valid for 10 minutes
    send_mail(
        subject="Your OTP Code",
        message=f"Your OTP code is {otp}. It will expire in 10 minutes.",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    OTP.objects.create(user=user, code=otp, expires_at=expires_at)  # Save OTP to the database
    print(f"Sending OTP {otp} to {user.email}")
