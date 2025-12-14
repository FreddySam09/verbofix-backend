# therapy_sessions/serializers.py
from rest_framework import serializers
from .models import Speaker, Pairing, Session, Report, SessionFeedback
from django.conf import settings
User = settings.AUTH_USER_MODEL

class SpeakerSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField()
    class Meta:
        model = Speaker
        fields = ["id", "user", "is_assigned", "phone", "email"]

class PairingSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)
    speaker = SpeakerSerializer(read_only=True)
    class Meta:
        model = Pairing
        fields = ["id", "user", "speaker", "created_at", "active", "notes"]

class SessionSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)
    speaker = SpeakerSerializer(read_only=True)
    recording_url = serializers.SerializerMethodField()

    class Meta:
        model = Session
        fields = [
            "id", "pairing", "user", "speaker", "scheduled_at",
            "started_at", "ended_at", "recording_url", "report_generated",
            "approved_by_speaker", "webrtc_offer", "webrtc_answer"
        ]

    def get_recording_url(self, obj):
        if obj.recording:
            try:
                return obj.recording.url
            except Exception:
                return None
        return None

class ReportSerializer(serializers.ModelSerializer):
    session = SessionSerializer(read_only=True)
    class Meta:
        model = Report
        fields = [
            "id", "session", "created_at", "stammer_rate", "severity",
            "recommendations", "raw_output", "transcription"
        ]

class SessionFeedbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = SessionFeedback
        fields = ["id", "session", "given_by", "rating", "comment", "target"]
