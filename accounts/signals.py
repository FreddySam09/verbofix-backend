from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from therapy_sessions.models import Speaker

User = settings.AUTH_USER_MODEL

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_speaker_profile(sender, instance, created, **kwargs):
    """
    Automatically create a Speaker profile for users with role='Speaker'
    right after registration.
    """
    if created and getattr(instance, "role", None) == "Speaker":
        # Avoid duplicate creation if already exists
        Speaker.objects.get_or_create(
            user=instance,
            defaults={
                "email": instance.email,
                "phone": getattr(instance, "phone", ""),
                "is_assigned": False,
            }
        )
