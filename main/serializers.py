from accounts.models import User
from voice.models import LovedOne
from rest_framework import serializers


class UserAvatarSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['avatar']

class LovedOneVoiceFileSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False)

    class Meta:
        model = LovedOne
        fields = ['id', 'voice_file']


class LovedOneAvatarSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False)

    class Meta:
        model = LovedOne
        fields = ['id', 'avatar']

