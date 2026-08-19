from django.utils.deprecation import MiddlewareMixin
from .auth import get_user_from_token

class JWTAuthenticationMiddleware(MiddlewareMixin):
    def process_request(self, request):

        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.replace("Bearer ", "")

            user = get_user_from_token(token)


            if user:
                request.user = user
