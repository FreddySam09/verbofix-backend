from django.db import models
from django.conf import settings
from django.db.models.signals import post_delete
from django.dispatch import receiver

User = settings.AUTH_USER_MODEL


class Speaker(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="speaker_profile")
    is_assigned = models.BooleanField(default=False)
    phone = models.CharField(max_length=15, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)

    def __str__(self):
        return self.user.username


class Pairing(models.Model):
    """
    Represents an ongoing assignment between a user and a speaker.
    Pairings are created instantly (no speaker approval required).
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="pairings")
    speaker = models.ForeignKey(Speaker, on_delete=models.CASCADE, related_name="pairings")
    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "speaker", "active"]),
        ]

    def end(self):
        """Convenience: end this pairing and free the speaker."""
        if self.active:
            self.active = False
            self.save()
            if self.speaker:
                self.speaker.is_assigned = False
                self.speaker.save()

    def __str__(self):
        status = "active" if self.active else "ended"
        return f"{self.user} ↔ {self.speaker.user} ({status})"


@receiver(post_delete, sender=Pairing)
def release_speaker_on_delete(sender, instance, **kwargs):
    """When a pairing is deleted, free the speaker."""
    if instance.speaker:
        instance.speaker.is_assigned = False
        instance.speaker.save()


class Session(models.Model):
    """
    A session = a scheduled call between a user and their paired speaker.
    We add simple fields to hold webRTC offer/answer for quick signaling.
    For production use, integrate a WebSocket/Signalling server (Channels).
    """
    pairing = models.ForeignKey(Pairing, on_delete=models.CASCADE, related_name="sessions", null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    speaker = models.ForeignKey(Speaker, on_delete=models.CASCADE, related_name="sessions")
    scheduled_at = models.DateTimeField()
    started_at = models.DateTimeField(blank=True, null=True)
    ended_at = models.DateTimeField(blank=True, null=True)
    recording = models.FileField(upload_to="recordings/", blank=True, null=True)
    report_generated = models.BooleanField(default=False)

    # Speaker must approve session before call actually starts
    approved_by_speaker = models.BooleanField(default=False)

    # Simple signaling storage (SDP/ICE) for prototyping
    webrtc_offer = models.JSONField(blank=True, null=True)
    webrtc_answer = models.JSONField(blank=True, null=True)

    def __str__(self):
        return f"Session {self.id} ({self.user} - {self.speaker.user})"


class Report(models.Model):
    session = models.OneToOneField(Session, on_delete=models.CASCADE, related_name="report")
    created_at = models.DateTimeField(auto_now_add=True)
    stammer_rate = models.FloatField(blank=True, null=True)
    severity = models.CharField(max_length=32, blank=True, null=True)
    recommendations = models.JSONField(blank=True, null=True)
    raw_output = models.JSONField(blank=True, null=True)
    transcription = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Report for session {self.session_id}"

class SessionFeedback(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="feedbacks")
    given_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="given_feedbacks")
    rating = models.PositiveSmallIntegerField(default=0)
    comment = models.TextField(blank=True)
    target = models.CharField(max_length=10, choices=[('user','User'), ('speaker','Speaker')])
