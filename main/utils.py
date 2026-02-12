import jwt
from django.conf import settings
from datetime import datetime, timedelta

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
