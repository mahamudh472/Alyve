from strawberry.django.views import GraphQLView
from .auth import get_user_from_token


class CustomGraphQLView(GraphQLView):

    def get_context(self, request, response):

        return {
            "request": request,
            "response": response
        }

