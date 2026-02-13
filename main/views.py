from strawberry.django.views import GraphQLView

class CustomGraphQLView(GraphQLView):

    def get_context(self, request, response):

        return {
            "request": request,
            "response": response
        }

    def report_errors(self, errors, result):
        filtered_errors = []
        
        for error in errors:
            if isinstance(error.original_error, GraphQLError):
                logger.info(f"GraphQL Validation Error: {error.message}")
            else:
                filtered_errors.append(error)
        
        if filtered_errors:
            super().report_errors(filtered_errors, result)
