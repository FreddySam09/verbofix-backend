from django.urls import path
from .views import RegisterView, MeView
from .views import RegisterView, MeView, RoleBasedLoginView
from django.conf import settings
from django.conf.urls.static import static


urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", RoleBasedLoginView.as_view(), name="login"),
    path("me/", MeView.as_view(), name="me"),
]+ static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

