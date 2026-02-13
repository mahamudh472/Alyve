from os import wait
from django.utils.deprecation import MiddlewareMixin
from .auth import get_user_from_token

class JWTAuthenticationMiddleware(MiddlewareMixin):
    def process_request(self, request):

        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.replace("Bearer ", "")
            print('Token:', token)
            user = get_user_from_token(token)
            print("user:", user)

            if user:
                request.user = user
