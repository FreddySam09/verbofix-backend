# therapy_sessions/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SpeakerViewSet, PairingViewSet, SessionViewSet, ReportViewSet
from .views import get_report_for_session, submit_feedback, analyze_uploaded_audio, approve, ensure_csrf

router = DefaultRouter()
router.register(r'speakers', SpeakerViewSet, basename='speaker')
router.register(r'pairings', PairingViewSet, basename='pairing')
router.register(r'sessions', SessionViewSet, basename='session')
router.register(r'reports', ReportViewSet, basename='report')

urlpatterns = [
    path("", include(router.urls)),
    # report endpoint (module-level function)
    path("sessions/<int:session_id>/report/", get_report_for_session, name="get_report_for_session"),
    # feedback endpoint
    path("feedback/", submit_feedback, name="submit_feedback"),
    path("analyze-audio/", analyze_uploaded_audio, name="analyze_uploaded_audio"),
    path("sessions/<int:pk>/approve/", approve, name="session-approve"),
    path("csrf/", ensure_csrf, name="ensure_csrf"),
]
