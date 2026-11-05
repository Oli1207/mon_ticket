from config.net import get_client_ip

from .models import AuditLog


def _stringify(value):
    if value is None or isinstance(value, (bool, int, float, str, list, dict)):
        return value
    return str(value)


def diff_fields(instance, new_values):
    """{champ: {'old': ..., 'new': ...}} pour les seules valeurs qui changent."""
    changes = {}
    for field, new in new_values.items():
        old = getattr(instance, field, None)
        if hasattr(old, 'name') and not isinstance(old, str):
            old = old.name or None
        if hasattr(new, 'name') and not isinstance(new, str):
            new = getattr(new, 'name', None)
        if old != new:
            changes[field] = {'old': _stringify(old), 'new': _stringify(new)}
    return changes


def log_action(request, action, organization=None, target=None, changes=None):
    actor = request.user if request is not None and request.user.is_authenticated else None
    return AuditLog.objects.create(
        organization=organization,
        actor=actor,
        action=action,
        target_type=target.__class__.__name__ if target is not None else '',
        target_id=str(target.pk) if target is not None else '',
        target_label=str(target)[:200] if target is not None else '',
        changes=changes or {},
        ip_address=get_client_ip(request) if request is not None else None,
    )
