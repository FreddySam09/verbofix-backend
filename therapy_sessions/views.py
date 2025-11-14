from rest_framework import viewsets, permissions, status, serializers
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from django.utils import timezone
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.conf import settings
from django.core.mail import send_mail
from datetime import timedelta
from .models import Speaker, Pairing, Session, Report
from .serializers import SpeakerSerializer, PairingSerializer, SessionSerializer, ReportSerializer
try:
    from twilio.rest import Client as TwilioClient
except Exception:
    TwilioClient = None

# -----------------------
# Notification helpers
# -----------------------
def send_email(subject, message, recipient_list):
    try:
        if not getattr(settings, "EMAIL_HOST", None):
            print("Email not configured. Skipping email send:", subject, recipient_list)
            return
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipient_list, fail_silently=True)
    except Exception as e:
        print("Email send failed:", e)


def send_sms(body, to_number):
    tw_sid = getattr(settings, "TWILIO_ACCOUNT_SID", None)
    tw_token = getattr(settings, "TWILIO_AUTH_TOKEN", None)
    tw_from = getattr(settings, "TWILIO_FROM_NUMBER", None)
    if not tw_sid or not tw_token or not tw_from or TwilioClient is None:
        print("Twilio not configured or client missing. Skipping SMS to", to_number)
        return
    client = TwilioClient(tw_sid, tw_token)
    client.messages.create(body=body, from_=tw_from, to=to_number)

def notify_speaker_on_pairing(speaker: Speaker, user):
    # Email
    subject = "New pairing assigned"
    message = f"You have been paired with user {user.username}. Log into the speaker dashboard to view details."
    if speaker.email:
        send_email(subject, message, [speaker.email])
    # SMS
    if speaker.phone:
        send_sms(f"{message}", speaker.phone)

def notify_speaker_on_session_request(speaker: Speaker, session: Session):
    subject = "New session request"
    message = f"User {session.user.username} requested a session at {session.scheduled_at}. Approve in dashboard."
    if speaker.email:
        send_email(subject, message, [speaker.email])
    if speaker.phone:
        send_sms(message, speaker.phone)

def notify_user_on_approval(user, session: Session):
    subject = "Your session was approved — join within 10 minutes"
    join_msg = (
        f"Your speaker approved the session. Please join within 10 minutes: session id {session.id}.\n"
        f"Start: {session.started_at}. It will end by {session.ended_at} (10 minute max)."
    )
    try:
        user_email = user.email
    except Exception:
        user_email = None

    if user_email:
        send_email(subject, join_msg, [user_email])
    user_phone = getattr(user, "phone", None)
    if user_phone:
        send_sms(join_msg, user_phone)

class SpeakerViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Speaker.objects.all()
    serializer_class = SpeakerSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=["get"], url_path="with-status")
    def speakers_with_status(self, request):
        user = request.user
        speakers = Speaker.objects.all()
        result = []
        for sp in speakers:
            pairing = Pairing.objects.filter(speaker=sp, active=True).first()
            if pairing and pairing.user == user:
                status_label = "paired_with_you"
            elif pairing:
                status_label = "paired_with_other"
            else:
                status_label = "free"
            data = SpeakerSerializer(sp).data
            data["status"] = status_label
            result.append(data)
        return Response(result)


class PairingViewSet(viewsets.ModelViewSet):
    serializer_class = PairingSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Pairing.objects.all()

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, "speaker_profile"):
            return Pairing.objects.filter(speaker=user.speaker_profile).order_by("-created_at")
        return Pairing.objects.filter(user=user).order_by("-created_at")

    @transaction.atomic
    def perform_create(self, serializer):
        user = self.request.user
        speaker_id = self.request.data.get("speaker_id")
        if not speaker_id:
            raise serializers.ValidationError({"speaker_id": "This field is required."})

        speaker = get_object_or_404(Speaker, id=speaker_id)

        # End user's existing active pairing (if any)
        old_user_pair = Pairing.objects.filter(user=user, active=True).first()
        if old_user_pair:
            old_user_pair.active = False
            old_user_pair.save()

        # End speaker's existing active pairing (if any)
        old_speaker_pair = Pairing.objects.filter(speaker=speaker, active=True).first()
        if old_speaker_pair:
            old_speaker_pair.active = False
            old_speaker_pair.save()

        pairing = serializer.save(user=user, speaker=speaker, active=True)
        speaker.is_assigned = True
        speaker.save()

        # notify speaker about pairing (email + sms)
        notify_speaker_on_pairing(speaker, user)

        return pairing

    @action(detail=True, methods=["post"], url_path="end")
    def end_pairing(self, request, pk=None):
        pairing = self.get_object()
        if pairing.user != request.user and not hasattr(request.user, "speaker_profile"):
            return Response({"detail": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)

        pairing.active = False
        pairing.speaker.is_assigned = False
        pairing.speaker.save()
        pairing.save()
        return Response({"detail": "Pairing ended"}, status=status.HTTP_200_OK)


class SessionViewSet(viewsets.ModelViewSet):
    serializer_class = SessionSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Session.objects.all()

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, "speaker_profile"):
            return Session.objects.filter(speaker=user.speaker_profile).order_by("-scheduled_at")
        return Session.objects.filter(user=user).order_by("-scheduled_at")

    def perform_create(self, serializer):
        user = self.request.user
        pairing = Pairing.objects.filter(user=user, active=True).first()
        if not pairing:
            raise serializers.ValidationError({"pairing": "You must have an active pairing to start a session."})

        scheduled_at = serializer.validated_data.get("scheduled_at", timezone.now())
        session = serializer.save(
            user=user,
            speaker=pairing.speaker,
            pairing=pairing,
            scheduled_at=scheduled_at,
        )

        # notify speaker about session request
        notify_speaker_on_session_request(pairing.speaker, session)

        return session

    @action(detail=True, methods=["post"], url_path="approve")
    def approve_session(self, request, pk=None):
        session = self.get_object()
        # only the assigned speaker can approve
        if not hasattr(request.user, "speaker_profile") or request.user.speaker_profile != session.speaker:
            return Response({"detail": "Only the assigned speaker can approve."}, status=status.HTTP_403_FORBIDDEN)

        # mark started_at now and set ended_at 10 minutes later (server enforced)
        session.approved_by_speaker = True
        session.started_at = timezone.now()
        session.ended_at = session.started_at + timedelta(minutes=10)
        session.save()

        # notify user (sms/email) to join within next 10 minutes
        notify_user_on_approval(session.user, session)

        return Response(SessionSerializer(session).data)

    # basic signaling endpoints (offer/answer stored on session)
    @action(detail=True, methods=["post"], url_path="offer")
    def post_offer(self, request, pk=None):
        session = self.get_object()
        if session.user != request.user:
            return Response({"detail": "Only the session user can post offer."}, status=status.HTTP_403_FORBIDDEN)

        offer = request.data.get("offer")
        if not offer:
            return Response({"detail": "offer required"}, status=status.HTTP_400_BAD_REQUEST)

        session.webrtc_offer = offer
        session.save()
        return Response({"detail": "offer saved"}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="answer")
    def post_answer(self, request, pk=None):
        session = self.get_object()
        if not hasattr(request.user, "speaker_profile") or request.user.speaker_profile != session.speaker:
            return Response({"detail": "Only the assigned speaker can post answer."}, status=status.HTTP_403_FORBIDDEN)

        answer = request.data.get("answer")
        if not answer:
            return Response({"detail": "answer required"}, status=status.HTTP_400_BAD_REQUEST)

        session.webrtc_answer = answer
        session.save()
        return Response({"detail": "answer saved"}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="signaling")
    def get_signaling(self, request, pk=None):
        session = self.get_object()
        return Response({
            "offer": session.webrtc_offer,
            "answer": session.webrtc_answer,
            "approved_by_speaker": session.approved_by_speaker,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
        })

    @action(detail=True, methods=["post"], url_path="upload_audio")
    def upload_audio(self, request, pk=None):
        """
        Upload user's recorded audio blob. Field name: 'audio'
        """
        session = self.get_object()
        # only session owner uploads the user audio
        if session.user != request.user:
            return Response({"detail": "Only session user can upload audio."}, status=status.HTTP_403_FORBIDDEN)

        audio_file = request.FILES.get("audio")
        if not audio_file:
            return Response({"detail": "No audio file provided."}, status=status.HTTP_400_BAD_REQUEST)

        # save to session.recording (overwrites any previous)
        session.recording.save(audio_file.name, audio_file, save=True)
        return Response({"detail": "audio uploaded", "recording_url": session.recording.url}, status=status.HTTP_200_OK)

class ReportViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ReportSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, "speaker_profile"):
            return Report.objects.filter(session__speaker=user.speaker_profile)
        return Report.objects.filter(session__user=user)


# Fake analyze endpoint (keeps your original flow)
@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def analyze_audio(request):
    session_id = request.data.get("session_id")
    if not session_id:
        return Response({"error": "session_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        session = Session.objects.get(id=session_id, user=request.user)
    except Session.DoesNotExist:
        return Response({"error": "Session not found or access denied."}, status=status.HTTP_404_NOT_FOUND)

    fake_result = {
        "session_id": session.id,
        "stammer_rate": 8.3,
        "severity": "Mild",
        "recommendations": ["Practice breathing", "Mirror exercises"],
        "audioDuration": "32.1s",
    }

    report = Report.objects.create(
        session=session,
        stammer_rate=fake_result["stammer_rate"],
        severity=fake_result["severity"],
        recommendations=fake_result["recommendations"],
        raw_output=fake_result,
    )

    session.report_generated = True
    session.save()
    return Response({"report": ReportSerializer(report).data}, status=status.HTTP_201_CREATED)