from strawberry.django.views import GraphQLView
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from .auth import get_user_from_refresh_token
from .utils import generate_access_token, generate_refresh_token
from voice.models import LovedOne
from .serializers import UserAvatarSerializer, LovedOneVoiceFileSerializer
import logging
from voice.views import _maybe_clone_eleven_voice

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
            avatar_url = user.avatar.url if user.avatar else None
            return Response({"message": "Avatar uploaded successfully", "avatar_url": avatar_url}, status=200)
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
            is_new = not LovedOne.objects.filter(id=loved_one_id).exists()
            if is_new:
                loved_one = LovedOne.objects.create(id=loved_one_id, voice_file=voice_file, user=request.user)
            else:
                loved_one = LovedOne.objects.get(id=loved_one_id)
                loved_one.voice_file = voice_file
                loved_one.save()
            # get the file path of the uploaded voice file
            file_path = loved_one.voice_file.path
            
            from django.conf import settings
            import os
            import json
            api_key = settings.VOICE_APP.get("ELEVENLABS_API_KEY") or os.getenv("ELEVENLABS_API_KEY", "")
            
            if api_key:
                from accounts.models import UserSubscription, SubscriptionCloneUsage
                subscription = UserSubscription.objects.filter(user=request.user, is_active=True).select_related('plan').first()
                if not subscription:
                    if is_new:
                        loved_one.delete()
                    return Response({"error": "You do not have an active subscription. Please subscribe to clone a voice."}, status=403)
                
                has_usage = SubscriptionCloneUsage.objects.filter(subscription=subscription, loved_one=loved_one).exists()
                if not has_usage:
                    clone_usage = subscription.clone_usages.count()
                    if clone_usage >= subscription.plan.clone_limit:
                        if is_new:
                            loved_one.delete()
                        return Response({"error": "Voice clone limit reached for your subscription plan."}, status=403)
                
                try:
                    voice_id = _maybe_clone_eleven_voice(loved_one, [file_path])
                    if not voice_id:
                        raise RuntimeError("ElevenLabs voice cloning returned empty voice ID.")
                    loved_one.eleven_voice_id = voice_id
                    loved_one.save()
                    
                    if not has_usage:
                        SubscriptionCloneUsage.objects.create(
                            subscription=subscription,
                            loved_one=loved_one
                        )
                except Exception as e:
                    if is_new:
                        loved_one.delete()
                    else:
                        loved_one.eleven_voice_id = ""
                        loved_one.save()
                    
                    error_msg = str(e)
                    if "ElevenLabs clone failed" in error_msg:
                        try:
                            json_part = error_msg.split(None, 4)[4]
                            err_data = json.loads(json_part)
                            detail = err_data.get("detail", {})
                            if isinstance(detail, dict):
                                message = detail.get("message") or detail.get("status")
                            else:
                                message = detail
                            if message:
                                return Response({"error": f"Voice cloning failed: {message}"}, status=400)
                        except Exception:
                            pass
                    return Response({"error": f"Voice cloning failed: {error_msg}"}, status=400)
            
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
