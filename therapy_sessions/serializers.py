from rest_framework import serializers
from .models import Speaker, Pairing, Session, Report
from django.contrib.auth import get_user_model

User = get_user_model()


class SpeakerSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Speaker
        fields = ["id", "username", "phone", "email", "is_assigned"]


class PairingSerializer(serializers.ModelSerializer):
    speaker = SpeakerSerializer(read_only=True)
    # user is read-only (comes from request.user)
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Pairing
        fields = ["id", "user", "speaker", "created_at", "active", "notes"]
        read_only_fields = ["created_at", "active", "user"]


class SessionSerializer(serializers.ModelSerializer):
    speaker = SpeakerSerializer(read_only=True)
    pairing = serializers.PrimaryKeyRelatedField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Session
        fields = [
            "id",
            "pairing",
            "user",
            "speaker",
            "scheduled_at",
            "started_at",
            "ended_at",
            "recording",
            "report_generated",
            "approved_by_speaker",
            "webrtc_offer",
            "webrtc_answer",
        ]
        read_only_fields = [
            "user",
            "speaker",
            "pairing",
            "started_at",
            "ended_at",
            "report_generated",
            "approved_by_speaker",
            "webrtc_answer",  
        ]


class ReportSerializer(serializers.ModelSerializer):
    session_id = serializers.IntegerField(source="session.id", read_only=True)

    class Meta:
        model = Report
        fields = [
            "id",
            "session_id",
            "created_at",
            "stammer_rate",
            "severity",
            "recommendations",
            "raw_output",
        ]
