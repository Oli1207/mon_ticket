import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from config.tokens import hash_token

from .models import EmailVerificationToken
from .tasks import send_verification_email

TOKEN_TTL = timedelta(hours=48)
RESEND_COOLDOWN = timedelta(minutes=1)

NOT_VERIFIED_MESSAGE = (
    "Confirme d'abord ton adresse email : clique sur le lien que nous t'avons envoye "
    "(tu peux en demander un nouveau depuis ton profil)."
)


def issue_verification(user):
    """Remplace les jetons precedents et planifie l'envoi apres commit. Renvoie False si trop tot pour renvoyer."""
    latest = user.email_verification_tokens.order_by('-created_at').first()
    if latest is not None and latest.created_at > timezone.now() - RESEND_COOLDOWN:
        return False
    raw_token = secrets.token_urlsafe(32)
    with transaction.atomic():
        user.email_verification_tokens.all().delete()
        EmailVerificationToken.objects.create(
            user=user, token_hash=hash_token(raw_token), expires_at=timezone.now() + TOKEN_TTL,
        )
    user_id = user.pk
    transaction.on_commit(lambda: send_verification_email.delay(user_id, raw_token))
    return True


def confirm_verification(raw_token):
    """Renvoie l'utilisateur verifie, ou None si le jeton est inconnu ou expire. Usage unique."""
    with transaction.atomic():
        token = (
            EmailVerificationToken.objects.select_for_update(of=('self',))
            .select_related('user')
            .filter(token_hash=hash_token(raw_token))
            .first()
        )
        if token is None or token.expires_at <= timezone.now():
            return None
        user = token.user
        if user.email_verified_at is None:
            user.email_verified_at = timezone.now()
            user.save(update_fields=['email_verified_at'])
        user.email_verification_tokens.all().delete()
    return user


def require_verified_email(user):
    if not user.is_email_verified:
        raise PermissionDenied(NOT_VERIFIED_MESSAGE)
