from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from rest_framework import generics, permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .serializers import (
    ChangePasswordSerializer,
    EmailTokenObtainPairSerializer,
    RegisterSerializer,
    UserSerializer,
)

User = get_user_model()

REGISTER_GENERIC_MESSAGE = (
    "Si cette adresse n'est pas deja utilisee, ton compte est cree. "
    'Connecte-toi pour continuer.'
)


class RegisterView(APIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()
    throttle_scope = 'register'

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # Reponse identique que le compte existe ou non : pas d'enumeration des emails.
        if not User.objects.filter(email=data['email']).exists():
            try:
                with transaction.atomic():
                    User.objects.create_user(**data)
            except IntegrityError:
                pass
        else:
            # Meme cout de hachage dans les deux branches : pas d'enumeration par le temps de reponse.
            make_password(data['password'])
        return Response({'detail': REGISTER_GENERIC_MESSAGE}, status=status.HTTP_201_CREATED)


class LoginView(TokenObtainPairView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = EmailTokenObtainPairSerializer
    throttle_scope = 'login'


class RefreshView(TokenRefreshView):
    permission_classes = (permissions.AllowAny,)
    throttle_scope = 'login'


class ProfileView(generics.RetrieveUpdateAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = UserSerializer
    parser_classes = (JSONParser, MultiPartParser, FormParser)
    http_method_names = ('get', 'patch')

    def get_object(self):
        return self.request.user


class ChangePasswordView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    throttle_scope = 'login'

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data['new_password'])
        request.user.save(update_fields=['password'])
        return Response({'detail': 'Mot de passe mis a jour.'})
