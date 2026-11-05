from .models import PricingChange


def log_pricing_changes(user, target, changes):
    """changes : {champ: {'old': ..., 'new': ...}} tel que produit par organizations.audit.diff_fields."""
    PricingChange.objects.bulk_create([
        PricingChange(
            changed_by=user, target=target, field=field,
            old_value='' if diff['old'] is None else str(diff['old'])[:200],
            new_value='' if diff['new'] is None else str(diff['new'])[:200],
        )
        for field, diff in changes.items()
    ])
