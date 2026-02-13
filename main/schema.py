import strawberry
from .queries import Query
from .mutations import Mutation
from strawberry.extensions import SchemaExtension
from graphql import GraphQLError

class CustomErrorHandlingExtension(SchemaExtension):
    def on_operation(self):
        yield
        result = self.execution_context.result
        # print("Execution result:", result)
        # print("Execution errors:", result.errors if result else "No result")
        if result and result.errors:
            for error in result.errors:
                if not isinstance(error.original_error, GraphQLError):
                    error.message = "An unexpected error occurred. Please try again later."
                    error.extensions = {"code": "INTERNAL_SERVER_ERROR"}

schema = strawberry.Schema(query=Query, mutation=Mutation, extensions=[CustomErrorHandlingExtension]) 
