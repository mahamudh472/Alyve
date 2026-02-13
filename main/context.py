from .auth import get_user_from_token


def get_context(request, response):
    auth_header = request.headers.get("Authorization")

    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        user = get_user_from_token(token)
        if user:
            request.user = user
        return {request: request, response: response}
