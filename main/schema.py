import strawberry
from .queries import Query
from .mutations import Mutation
from .middleware import JWTAuthMiddleware

schema = strawberry.Schema(query=Query, mutation=Mutation) 
