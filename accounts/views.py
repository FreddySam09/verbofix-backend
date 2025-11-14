from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from .serializers import RegisterSerializer, UserSerializer, CustomTokenObtainPairSerializer
from django.contrib.auth import get_user_model

User = get_user_model()


# ---------- Register ----------
class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]


# ---------- Me ----------
class MeView(generics.RetrieveAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


# ---------- Login (with role check) ----------
class RoleBasedLoginView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        requested_role = request.data.get("role")
        username = request.data.get("username")

        if not requested_role or not username:
            return Response({"detail": "Username and role are required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        if user.role != requested_role:
            return Response(
                {"detail": f"This account is registered as a {user.role}. Please switch to {user.role} login."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # If role matches, continue normal login
        return super().post(request, *args, **kwargs)
