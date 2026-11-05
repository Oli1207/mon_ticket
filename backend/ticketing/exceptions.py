from rest_framework import status
from rest_framework.exceptions import APIException


class OrderError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = 'Impossible de creer la commande.'
    default_code = 'order_error'


class SoldOut(OrderError):
    status_code = status.HTTP_409_CONFLICT
    default_code = 'sold_out'


class TooManyPendingOrders(OrderError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = 'too_many_pending'


class PaymentUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = 'Le paiement est momentanement indisponible. Reessaie dans quelques instants.'
    default_code = 'payment_unavailable'
