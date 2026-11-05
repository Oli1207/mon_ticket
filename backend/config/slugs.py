import secrets

from django.utils.text import slugify


def build_slug(text, max_length=60, suffix=False):
    base = slugify(text)[:max_length].strip('-') or 'item'
    if suffix:
        return f'{base}-{secrets.token_hex(3)}'
    return base


def unique_slug(model, text, max_length=60, field='slug'):
    """Slug lisible, suffixe aleatoire en cas de collision. La contrainte unique reste l'arbitre final."""
    slug = build_slug(text, max_length)
    if not model.objects.filter(**{field: slug}).exists():
        return slug
    for _ in range(5):
        slug = build_slug(text, max_length, suffix=True)
        if not model.objects.filter(**{field: slug}).exists():
            return slug
    return f'{build_slug(text, max_length - 13)}-{secrets.token_hex(6)}'
