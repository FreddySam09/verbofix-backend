# therapy_sessions/views.py
import os
import logging
from django.conf import settings
from django.utils import timezone
from django.db import transaction
import tempfile
import shutil
from rest_framework import viewsets, permissions, status, parsers
from rest_framework.decorators import action, api_view, permission_classes, parser_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import ensure_csrf_cookie
from django.http import JsonResponse

from .models import Speaker, Pairing, Session, Report, SessionFeedback
from .serializers import (
    SpeakerSerializer, PairingSerializer, SessionSerializer, ReportSerializer,
    SessionFeedbackSerializer
)

logger = logging.getLogger(__name__)

from . import ml_model  # our ml_model wrapper

# at top of views.py
from pydub import AudioSegment
import tempfile
import subprocess

def convert_to_wav(input_path, target_sr=16000):
    # prefer pydub if ffmpeg available
    try:
        audio = AudioSegment.from_file(input_path)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        audio = audio.set_frame_rate(target_sr).set_channels(1)
        audio.export(tmp.name, format="wav")
        return tmp.name
    except Exception:
        # fallback to ffmpeg CLI
        outtmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        cmd = ["ffmpeg", "-y", "-i", input_path, "-ar", str(target_sr), "-ac", "1", outtmp.name]
        subprocess.run(cmd, check=True)
        return outtmp.name


class SpeakerViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Speaker.objects.all()
    serializer_class = SpeakerSerializer
    permission_classes = [permissions.IsAuthenticated]


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

        # end old pairings
        Pairing.objects.filter(user=user, active=True).update(active=False)
        Pairing.objects.filter(speaker=speaker, active=True).update(active=False)

        pairing = serializer.save(user=user, speaker=speaker, active=True)
        speaker.is_assigned = True
        speaker.save()
        return pairing


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
        session = serializer.save(user=user, speaker=pairing.speaker, pairing=pairing, scheduled_at=scheduled_at)
        return session

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

    # ---------- robust upload_audio endpoint ----------
    @action(detail=True, methods=["post"], url_path="upload-audio", parser_classes=[parsers.MultiPartParser, parsers.FormParser])
    def upload_audio(self, request, pk=None):
        session = self.get_object()
        if session.user != request.user:
            return Response({"detail": "Only session user can upload audio."}, status=403)

        audio_file = request.FILES.get("audio")
        if not audio_file:
            return Response({"detail": "No audio file provided. Use form key 'audio'."}, status=400)

        # Save original file to disk
        try:
            orig_name = f"session_{session.id}_orig_{int(timezone.now().timestamp())}_{audio_file.name}"
            session.recording.save(orig_name, audio_file, save=True)
            # after session.recording.save(...)
            audio_path = session.recording.path
            # convert if extension not wav/mp3
            ext = os.path.splitext(audio_path)[1].lower()
            if ext not in [".wav", ".mp3"]:
                converted = convert_to_wav(audio_path)
                result = ml_model.analyze_audio(converted)
                # remove converted file after use
                try: os.remove(converted)
                except: pass
            else:
                result = ml_model.analyze_audio(audio_path)

            session.save()
        except Exception:
            logger.exception("failed save original")
            return Response({"detail": "Failed saving original audio."}, status=500)

        # Convert to WAV (16k mono) for analysis
        try:
            # use tempfile to avoid clutter
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                tmp_wav_path = tmp_wav.name

            # pydub will read many formats; ensure ffmpeg is installed
            orig_path = session.recording.path
            audio_seg = AudioSegment.from_file(orig_path)
            # convert to mono 16k
            audio_seg = audio_seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
            audio_seg.export(tmp_wav_path, format="wav")
        except Exception:
            logger.exception("Conversion to wav failed")
            return Response({"detail": "Audio conversion failed (ensure ffmpeg is available)."}, status=500)

        # Call analysis
        try:
            result = ml_model.analyze_audio(tmp_wav_path)
        except Exception:
            logger.exception("ML analysis failed")
            return Response({"detail": "ML processing failed"}, status=500)
        finally:
            try:
                os.remove(tmp_wav_path)
            except Exception:
                pass

        # Save Report
        try:
            stammer_rate_str = result.get("stammerRate") or f"{result.get('stammer_rate', 0)}%"
            try:
                stammer_rate_val = float(str(stammer_rate_str).replace("%", ""))
            except Exception:
                stammer_rate_val = None

            report, _ = Report.objects.get_or_create(session=session)
            report.stammer_rate = stammer_rate_val
            report.severity = result.get("severity")
            report.recommendations = result.get("recommendations")
            report.raw_output = result
            report.transcription = result.get("transcription")
            report.save()
            session.report_generated = True
            session.save()
        except Exception:
            logger.exception("Failed saving report")
            return Response({"detail": "Saving report failed"}, status=500)

        return Response({"message": "Audio processed", "report": result}, status=200)
    # inside class SessionViewSet(viewsets.ModelViewSet):
    @action(detail=True, methods=["post"], url_path="end")
    def post_end(self, request, pk=None):
        """
        POST /api/sessions/sessions/<pk>/end/
        Allowed: session.user OR the assigned speaker (speaker_profile)
        Marks session.ended_at = now()
        """
        session = self.get_object()  # will 404 if not in user's queryset

        # permission: user or assigned speaker
        user = request.user
        is_speaker = hasattr(user, "speaker_profile") and user.speaker_profile == session.speaker
        if not (user == session.user or is_speaker):
            return Response({"detail": "Only the session user or assigned speaker can end this session."},
                            status=status.HTTP_403_FORBIDDEN)

        # mark ended
        session.ended_at = timezone.now()
        # if it was never started, leave started_at as-is (optionally set started_at=now)
        session.save()

        return Response({"detail": "Session ended."}, status=status.HTTP_200_OK)


class ReportViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ReportSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Report.objects.all()

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, "speaker_profile"):
            return Report.objects.filter(session__speaker=user.speaker_profile)
        return Report.objects.filter(session__user=user)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_report_for_session(request, session_id):
    """
    GET /api/sessions/sessions/<session_id>/report/
    """
    try:
        report = Report.objects.get(session__id=session_id)
        serializer = ReportSerializer(report)
        return Response(serializer.data)
    except Report.DoesNotExist:
        return Response({"detail": "Report not found"}, status=status.HTTP_404_NOT_FOUND)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def submit_feedback(request):
    session_id = request.data.get("session_id")
    rating = request.data.get("rating")
    comment = request.data.get("comment", "")
    target = request.data.get("target")

    session = get_object_or_404(Session, id=session_id)

    feedback = SessionFeedback.objects.create(
        session=session,
        given_by=request.user,
        rating=rating or 0,
        comment=comment,
        target=target or "speaker"
    )
    return Response({"success": True, "feedback_id": feedback.id})

@api_view(["POST"])
@permission_classes([IsAuthenticated])  # <-- change to AllowAny only for quick dev/testing if you need unauthenticated access
@parser_classes([parsers.MultiPartParser, parsers.FormParser])
def analyze_uploaded_audio(request):
    """
    POST /api/sessions/analyze-audio/
    Accepts form-data field 'audio'.
    Saves temporarily, converts to wav if needed, calls ml_model.analyze_audio(path), and returns result.
    """
    audio_file = request.FILES.get("audio")
    if not audio_file:
        return Response({"error": "No audio file provided"}, status=400)

    # Save uploaded file to a temp file
    suffix = os.path.splitext(audio_file.name)[1] or ".tmp"
    tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        for chunk in audio_file.chunks():
            tmp_in.write(chunk)
        tmp_in.flush()
        tmp_in.close()

        # If it's not wav or mp3, try to convert.
        ext = os.path.splitext(tmp_in.name)[1].lower()
        tmp_to_analyze = tmp_in.name

        # Try pydub ffmpeg-based conversion if available (safer). If not available, and file is wav/mp3, keep.
        try:
            from pydub import AudioSegment
            need_convert = ext not in [".wav", ".mp3"]
        except Exception:
            AudioSegment = None
            need_convert = ext not in [".wav", ".mp3"]

        if need_convert and AudioSegment is not None:
            tmp_conv = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            try:
                audio_seg = AudioSegment.from_file(tmp_in.name)
                audio_seg = audio_seg.set_frame_rate(16000).set_channels(1)
                audio_seg.export(tmp_conv.name, format="wav")
                tmp_conv.flush(); tmp_conv.close()
                tmp_to_analyze = tmp_conv.name
            except Exception:
                # fall back: use original file (might fail in analyze)
                try:
                    tmp_conv.close()
                    os.unlink(tmp_conv.name)
                except Exception:
                    pass
                tmp_to_analyze = tmp_in.name
        else:
            # if not converting, but it's mp3 -> keep; if wav -> keep
            tmp_to_analyze = tmp_in.name

        # Call ML analyzer (it expects a path readable by librosa)
        try:
            result = ml_model.analyze_audio(tmp_to_analyze)
        except Exception as e:
            logger.exception("ML analyze call failed: %s", e)
            return Response({"error": "ML analysis failed"}, status=500)

        return Response(result, status=200)

    finally:
        # cleanup temp files
        try:
            if os.path.exists(tmp_in.name):
                os.unlink(tmp_in.name)
        except Exception:
            pass
        # if we created converted file, remove it (tmp_to_analyze may be same as tmp_in)
        try:
            if tmp_to_analyze != tmp_in.name and os.path.exists(tmp_to_analyze):
                os.unlink(tmp_to_analyze)
        except Exception:
            pass

@ensure_csrf_cookie
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ensure_csrf(request):
    # This returns 200 and sets csrftoken cookie in browser
    return JsonResponse({"ok": True})

# Proper approve endpoint as a plain API view (works with your router path)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def approve(request, pk=None):
    """
    POST /api/sessions/sessions/<pk>/approve/
    Only the assigned speaker can approve the session.
    """
    # fetch session (respect permissions)
    session = get_object_or_404(Session, pk=pk)

    # ensure only assigned speaker can approve
    if not hasattr(request.user, "speaker_profile") or request.user.speaker_profile != session.speaker:
        return Response({"detail": "Only the assigned speaker can approve this session."}, status=status.HTTP_403_FORBIDDEN)

    session.approved_by_speaker = True
    # Optionally set started_at:
    # session.started_at = timezone.now()
    session.save()

    return Response({"detail": "Session approved by speaker."}, status=status.HTTP_200_OK)