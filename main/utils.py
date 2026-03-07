import jwt, random, datetime
from django.conf import settings
from datetime import datetime, timedelta
from accounts.models import OTP
from django.utils import timezone
from django.core.mail import send_mail
from accounts.models import Notification
from firebase_admin.messaging import Message, Notification as FCMNotification, UnregisteredError
from fcm_django.models import FCMDevice

def generate_access_token(user):
    payload = {
        'user_id': str(user.id),
        'exp': datetime.utcnow() + timedelta(days=7),  # Access token valid for 15 minutes
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

def add_notification(user, title, message):
    """Utility function to add a notification for a user."""
    Notification.objects.create(
        user=user,
        title=title,
        message=message,
    )
    devices = FCMDevice.objects.filter(user=user)
    for device in devices:
        try:
            response = device.send_message(
                Message(
                    notification=FCMNotification(
                        title=title,
                        body=message
                    )
                )
            )
            print(f"Success: device={device.id}, msg_id={response}")

        except UnregisteredError:
            print(f"❌ Device {device.id} is no longer registered. Removing...")
            device.delete()  # recommended

        except Exception as e:
            print(f"❌ Failed sending to device {device.id}: {e}")
