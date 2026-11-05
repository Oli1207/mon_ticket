import hashlib
import hmac
import logging

import requests
from django.conf import settings

from .exceptions import PaymentUnavailable

logger = logging.getLogger(__name__)


def _headers():
    if not settings.PAYSTACK_SECRET_KEY:
        raise PaymentUnavailable()
    return {
        'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
        'Content-Type': 'application/json',
    }


def _call(method, path, **kwargs):
    url = f'{settings.PAYSTACK_BASE_URL}{path}'
    try:
        response = requests.request(method, url, headers=_headers(), timeout=settings.PAYSTACK_TIMEOUT_SECONDS, **kwargs)
        payload = response.json()
    except (requests.RequestException, ValueError):
        logger.exception('Appel Paystack en echec : %s %s', method, path)
        raise PaymentUnavailable()
    if response.status_code >= 400 or not payload.get('status'):
        # Le message Paystack n'est pas renvoye au client : il peut contenir des details internes.
        logger.error('Paystack a refuse %s %s (HTTP %s)', method, path, response.status_code)
        raise PaymentUnavailable()
    return payload['data']


def to_subunits(amount):
    return int(amount) * 100


def initialize_transaction(order, reference, callback_url):
    body = {
        'email': order.email,
        'amount': to_subunits(order.total),
        'currency': settings.PAYSTACK_CURRENCY,
        'reference': reference,
        'callback_url': callback_url,
        'metadata': {'order_public_id': order.public_id},
        'subaccount': order.event.organization.paystack_subaccount_code,
        # Part plateforme fixe : tout le reste est reverse au sous-compte de l'organisateur.
        'transaction_charge': to_subunits(order.total - order.organizer_net),
        'bearer': 'account',
    }
    return _call('POST', '/transaction/initialize', json=body)


def verify_transaction(reference):
    return _call('GET', f'/transaction/verify/{requests.utils.quote(reference, safe="")}')


def is_valid_signature(raw_body, signature):
    secret = settings.PAYSTACK_WEBHOOK_SECRET
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)
