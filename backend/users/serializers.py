import re

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()

PHONE_RE = re.compile(r'^\+?[0-9 ]{6,20}$')


def validate_phone_value(value):
    if value and not PHONE_RE.match(value):
        raise serializers.ValidationError('Numero de telephone invalide. Exemple : +225 07 00 00 00 00.')
    return value


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            'id', 'email', 'first_name', 'last_name', 'phone', 'city',
            'avatar', 'is_organizer', 'is_staff', 'date_joined',
        )
        read_only_fields = ('id', 'email', 'is_organizer', 'is_staff', 'date_joined')
        extra_kwargs = {
            'first_name': {'max_length': 150},
            'last_name': {'max_length': 150},
        }

    def validate_phone(self, value):
        return validate_phone_value(value)


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8, max_length=128, style={'input_type': 'password'})

    class Meta:
        model = User
        fields = ('email', 'password', 'first_name', 'last_name', 'phone', 'city')
        extra_kwargs = {
            'first_name': {'required': True, 'allow_blank': False, 'max_length': 150},
            'last_name': {'required': True, 'allow_blank': False, 'max_length': 150},
            # Le validateur unique est retire : il revelerait l'existence d'un compte.
            'email': {'validators': []},
        }

    def validate_email(self, value):
        return value.strip().lower()

    def validate_phone(self, value):
        return validate_phone_value(value)

    def validate(self, attrs):
        candidate = User(email=attrs['email'], first_name=attrs['first_name'], last_name=attrs['last_name'])
        validate_password(attrs['password'], user=candidate)
        return attrs


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    default_error_messages = {
        'no_active_account': 'Email ou mot de passe incorrect.',
    }

    def validate(self, attrs):
        attrs[self.username_field] = attrs.get(self.username_field, '').strip().lower()
        data = super().validate(attrs)
        data['user'] = UserSerializer(self.user, context=self.context).data
        return data


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, max_length=128)
    new_password = serializers.CharField(write_only=True, min_length=8, max_length=128)

    def validate_current_password(self, value):
        if not self.context['request'].user.check_password(value):
            raise serializers.ValidationError('Mot de passe actuel incorrect.')
        return value

    def validate_new_password(self, value):
        validate_password(value, user=self.context['request'].user)
        return value
