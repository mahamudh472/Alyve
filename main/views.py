from strawberry.django.views import GraphQLView
import logging

logger = logging.getLogger(__name__)

class CustomGraphQLView(GraphQLView):
    multipart_uploads_enabled = True

    def get_context(self, request, response):

        return {
            "request": request,
            "response": response
        }

