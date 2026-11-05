from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from .models import CustomUser


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def send_verification_email(self, user_id, raw_token):
    user = CustomUser.objects.filter(pk=user_id, email_verified_at__isnull=True, is_active=True).first()
    if user is None:
        return
    link = f'{settings.FRONTEND_URL}/verify-email#token={raw_token}'
    try:
        send_mail(
            subject='Confirme ton adresse email - Mon Ticket',
            message=(
                f'Bonjour {user.first_name or ""},\n\n'
                'Bienvenue sur Mon Ticket ! Confirme ton adresse email en ouvrant ce lien :\n\n'
                f'{link}\n\n'
                'Ce lien expire dans 48 heures.\n'
                "Si tu n'as pas cree de compte, ignore simplement cet email."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
        )
    except Exception as exc:
        raise self.retry(exc=exc)
