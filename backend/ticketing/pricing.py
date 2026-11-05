from decimal import ROUND_HALF_UP, Decimal

from .models import EventTicketing, PromoCode

HUNDRED = Decimal('100')


def round_fcfa(value):
    return Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP)


def resolve_commission_percent(pricing, event_ticketing=None, ticket_type=None, now=None):
    """Hierarchie global > code partenaire > evenement > type de billet : le plus specifique non nul gagne."""
    if ticket_type is not None and ticket_type.commission_override is not None:
        return ticket_type.commission_override
    if event_ticketing is not None:
        if event_ticketing.commission_override is not None:
            return event_ticketing.commission_override
        partner = event_ticketing.partner_code
        if partner is not None and (now is None or partner.is_usable(now)):
            return partner.commission_percent
    return pricing.commission_percent


def buyer_fee_for(price, commission_percent, fees_mode):
    if fees_mode != EventTicketing.FEES_BUYER:
        return Decimal('0')
    return round_fcfa(price * commission_percent / HUNDRED)


def promo_discount(promo, subtotal):
    if promo is None or subtotal <= 0:
        return Decimal('0')
    if promo.kind == PromoCode.KIND_PERCENT:
        discount = round_fcfa(subtotal * min(promo.value, HUNDRED) / HUNDRED)
    else:
        discount = round_fcfa(promo.value)
    return min(discount, subtotal)


def compute_order_amounts(lines, fees_mode, promo=None):
    """
    lines : iterable de (unit_price, quantity, commission_percent).
    La commission porte sur le montant apres remise, reparti au prorata des lignes.
    """
    subtotal = sum((price * qty for price, qty, _ in lines), Decimal('0'))
    gross_commission = sum((price * qty * pct / HUNDRED for price, qty, pct in lines), Decimal('0'))
    discount = promo_discount(promo, subtotal)
    net_sales = subtotal - discount
    commission = round_fcfa(gross_commission * net_sales / subtotal) if subtotal > 0 else Decimal('0')
    effective_percent = (
        (gross_commission * HUNDRED / subtotal).quantize(Decimal('0.01')) if subtotal > 0 else Decimal('0')
    )

    if fees_mode == EventTicketing.FEES_BUYER:
        service_fee = commission
        total = net_sales + commission
        organizer_net = net_sales
    else:
        service_fee = Decimal('0')
        total = net_sales
        organizer_net = net_sales - commission

    return {
        'subtotal': round_fcfa(subtotal),
        'discount': discount,
        'service_fee': service_fee,
        'total': round_fcfa(total),
        'organizer_net': round_fcfa(organizer_net),
        'commission_amount': commission,
        'commission_percent': effective_percent,
    }
