from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView
from django.views.decorators.csrf import csrf_exempt
from main.schema import schema
from main.views import CustomGraphQLView, UserAvatarUpdateView, LovedOneVoiceUploadAPIView, TokenRefreshView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/voice/", include("voice.urls")),
    path("api/v1/accounts/", include("accounts.urls")),
    path("test/", TemplateView.as_view(template_name="index.html")),
    path("graphql/", csrf_exempt(CustomGraphQLView.as_view(schema=schema))),
    path("api/v1/conversations/", include("conversations.urls")),
    path("history/", TemplateView.as_view(template_name="conversations_history.html")),
    path("api/v1/user/avatar/", UserAvatarUpdateView.as_view(), name="user-avatar-upload"),
    path("api/v1/loved-one/voice-upload/", LovedOneVoiceUploadAPIView.as_view(), name="loved-one-voice-upload"),
    path("api/v1/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),


]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
