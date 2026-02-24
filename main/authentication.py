from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from .auth import get_user_from_token


class CustomJWTAuthentication(BaseAuthentication):
    """
    DRF authentication class that validates tokens produced by
    main.utils.generate_access_token / generate_refresh_token.

    Replaces rest_framework_simplejwt so the entire project uses
    a single token format.
    """

    keyword = "Bearer"

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith(f"{self.keyword} "):
            return None  # No credentials — let other authenticators try

        token = auth_header.split(" ", 1)[1]
        user = get_user_from_token(token)

        if user is None:
            raise AuthenticationFailed("Invalid or expired token.")

        return (user, token)
