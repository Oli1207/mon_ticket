from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from .models import Invitation


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def send_invitation_email(self, invitation_id, raw_token):
    invitation = (
        Invitation.objects.select_related('organization', 'invited_by')
        .filter(pk=invitation_id, status=Invitation.STATUS_PENDING)
        .first()
    )
    if invitation is None:
        return
    link = f'{settings.FRONTEND_URL}/invitations/accept#token={raw_token}'
    inviter = invitation.invited_by.full_name or invitation.invited_by.email
    try:
        send_mail(
            subject=f'Invitation a rejoindre {invitation.organization.name} sur Mon Ticket',
            message=(
                f'Bonjour,\n\n{inviter} t\'invite a rejoindre {invitation.organization.name} sur Mon Ticket.\n\n'
                f'Accepte l\'invitation ici : {link}\n\n'
                f'Ce lien expire le {invitation.expires_at:%d/%m/%Y a %H:%M}.\n'
                'Si tu ne t\'attendais pas a cette invitation, ignore simplement cet email.'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[invitation.email],
        )
    except Exception as exc:
        raise self.retry(exc=exc)
