import jwt
from django.conf import settings
from accounts.models import User

def get_user_from_token(token: str) -> User:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("user_id")
        print("Decoded JWT Payload:", payload)
        print("Extracted User ID:", user_id)
        if user_id is None:
            return None
        return User.objects.get(id=user_id)

    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, User.DoesNotExist) as e:
        print("Error:", e)
        return None

def get_user_from_refresh_token(token: str):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])

        if payload.get("type") != "refresh":
            return None
        return User.objects.filter(id=payload.get("user_id")).first()

    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
