from .auth import get_user_from_token

class JWTAuthMiddleware:
    def resolve(self, next, root, info, **kwargs):
        request = info.context["request"]
        auth_header = request.headers.get("Authorization")
        
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            user = get_user_from_token(token)
            if user:
                info.context["user"] = user
        
        return next(root, info, **kwargs)
