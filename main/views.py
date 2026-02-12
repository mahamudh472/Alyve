from strawberry.django.views import GraphQLView
from .auth import get_user_from_token


class CustomGraphQLView(GraphQLView):

    def get_context(self, request, response):

        user = None

        auth_header = request.headers.get("Authorization")

        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.replace("Bearer ", "")
            user = get_user_from_token(token)

            if user:
                print("Authenticated User:", user.email)

        return {
            "request": request,
            "response": response,
            "user": user,   # ✅ IMPORTANT
        }
