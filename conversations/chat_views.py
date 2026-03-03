# conversations/chat_views.py
from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .serializers import ChatRequestSerializer
from .chat_service import run_chat_turn


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def chat(request):
    ser = ChatRequestSerializer(data=request.data)
    ser.is_valid(raise_exception=True)

    loved_one_id = ser.validated_data["loved_one_id"]
    message = ser.validated_data["message"]
    session_id = ser.validated_data.get("session_id")
    save_to_rag = ser.validated_data.get("save_to_rag", True)

    session, assistant_msg, rag_used = run_chat_turn(
        user=request.user,
        loved_one_id=loved_one_id,
        message=message,
        session_id=session_id,
        save_to_rag=bool(save_to_rag),
    )

    return Response(
        {
            "ok": True,
            "session_id": session.id,
            "assistant_message_id": assistant_msg.id,
            "assistant": assistant_msg.content,
            "rag_used": rag_used,
        }
    )