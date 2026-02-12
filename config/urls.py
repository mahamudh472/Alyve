from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView
from django.views.decorators.csrf import csrf_exempt
from strawberry.django.views import GraphQLView
from main.schema import schema
from main.views import CustomGraphQLView

urlpatterns = [
    path("admin/", admin.site.urls),
    # path("api/v1/voice/", include("voice.urls")),
    path("api/v1/accounts/", include("accounts.urls")),
    path("test/", TemplateView.as_view(template_name="index.html")),
    path("graphql/", csrf_exempt(CustomGraphQLView.as_view(schema=schema))),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
