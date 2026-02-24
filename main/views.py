from strawberry.django.views import GraphQLView
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from .auth import get_user_from_refresh_token
from .utils import generate_access_token, generate_refresh_token
from voice.models import LovedOne
from .serializers import UserAvatarSerializer, LovedOneVoiceFileSerializer
import logging

logger = logging.getLogger(__name__)

class CustomGraphQLView(GraphQLView):
    multipart_uploads_enabled = True

    def get_context(self, request, response):

        return {
            "request": request,
            "response": response
        }
class UserAvatarUpdateView(GenericAPIView):
    serializer_class = UserAvatarSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            # Process the uploaded avatar file here
            avatar_file = serializer.validated_data['avatar']
            user = request.user
            user.avatar = avatar_file
            user.save()
            return Response({"message": "Avatar uploaded successfully"}, status=200)
        else:
            logger.error(f"Avatar upload failed: {serializer.errors}")
            return Response(serializer.errors, status=400)

class LovedOneVoiceUploadAPIView(GenericAPIView):
    serializer_class = LovedOneVoiceFileSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            # Process the uploaded voice file here
            voice_file = serializer.validated_data['voice_file']
            loved_one_id = serializer.validated_data.get('id')
            if not LovedOne.objects.filter(id=loved_one_id).exists():
                loved_one = LovedOne.objects.create(id=loved_one_id, voice_file=voice_file)
            else:
                loved_one = LovedOne.objects.get(id=loved_one_id)
                loved_one.voice_file = voice_file
                loved_one.save()
            data = {
                "id": loved_one.id,
                "name": loved_one.name,
                "voice_file": loved_one.voice_file.url if loved_one.voice_file else None,
            }
            return Response({"data": data, "message": "Voice file uploaded successfully"}, status=200)
        else:
            logger.error(f"Voice file upload failed: {serializer.errors}")
            return Response(serializer.errors, status=400)

class TokenRefreshView(GenericAPIView):
    def post(self, request, *args, **kwargs):
        refresh_token = request.data.get("refresh_token")
        if not refresh_token:
            return Response({"error": "Refresh token is required"}, status=400)

        user = get_user_from_refresh_token(refresh_token)
        if user is None:
            return Response({"error": "Invalid refresh token"}, status=401)

        new_access_token = generate_access_token(user)
        new_refresh_token = generate_refresh_token(user)
        return Response({"access_token": new_access_token, "refresh_token": new_refresh_token}, status=200)
