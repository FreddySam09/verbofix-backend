# accounts/models.py
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from therapy_sessions.models import Speaker

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_speaker_profile(sender, instance, created, **kwargs):
    if created and instance.role == "Speaker":
        Speaker.objects.create(user=instance, email=instance.email, phone=instance.phone)

class CustomUser(AbstractUser):
    ROLE_CHOICES = (
        ("User", "User"),
        ("Speaker", "Speaker"),
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="User")
    place = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    age = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.username} ({self.role})"
