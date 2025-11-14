from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SpeakerViewSet, PairingViewSet, SessionViewSet, ReportViewSet, analyze_audio

router = DefaultRouter()
router.register(r'speakers', SpeakerViewSet, basename='speaker')
router.register(r'pairings', PairingViewSet, basename='pairing')
router.register(r'sessions', SessionViewSet, basename='session')
router.register(r'reports', ReportViewSet, basename='report')

urlpatterns = router.urls


urlpatterns = [
    path("", include(router.urls)),
    path("analyze/", analyze_audio, name="analyze-audio"),
]
