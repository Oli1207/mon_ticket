CAPABILITIES = frozenset({
    'org.manage', 'team.manage', 'audit.view',
    'events.view', 'events.manage',
    'staff.manage',
    'attendees.view', 'attendees.contact', 'attendees.export',
    'sales.view',
    'finance.view', 'finance.export',
    'checkin',
    'marketing.view', 'marketing.manage',
    'analytics.view',
})

ROLE_OWNER = 'owner'
ROLE_ADMIN = 'admin'
ROLE_PRODUCER = 'producer'
ROLE_FINANCE = 'finance'
ROLE_DOOR_MANAGER = 'door_manager'

ORG_ROLES = (
    (ROLE_OWNER, 'Proprietaire'),
    (ROLE_ADMIN, 'Administrateur'),
    (ROLE_PRODUCER, 'Producteur'),
    (ROLE_FINANCE, 'Finance'),
    (ROLE_DOOR_MANAGER, 'Responsable acces'),
)

# Roles attribuables via l'API : la propriete ne se transmet pas par une invitation.
ASSIGNABLE_ROLES = frozenset({ROLE_ADMIN, ROLE_PRODUCER, ROLE_FINANCE, ROLE_DOOR_MANAGER})

ROLE_DEFAULTS = {
    ROLE_OWNER: CAPABILITIES,
    ROLE_ADMIN: CAPABILITIES - {'org.manage'},
    ROLE_PRODUCER: frozenset({
        'events.view', 'events.manage', 'staff.manage',
        'attendees.view', 'attendees.contact', 'attendees.export',
        'marketing.view', 'marketing.manage', 'analytics.view',
    }),
    ROLE_FINANCE: frozenset({'sales.view', 'finance.view', 'finance.export'}),
    ROLE_DOOR_MANAGER: frozenset({'checkin', 'staff.manage', 'attendees.view'}),
}

STAFF_CAPABILITIES = frozenset({'checkin', 'attendees.view'})

STAFF_ROLE_DEFAULTS = {
    'manager': frozenset({'checkin', 'attendees.view'}),
    'scanner': frozenset({'checkin'}),
    'vip_host': frozenset({'checkin', 'attendees.view'}),
    'security': frozenset({'checkin'}),
}


def resolve_capabilities(defaults, custom_caps):
    caps = set(defaults)
    for cap, enabled in (custom_caps or {}).items():
        if enabled:
            caps.add(cap)
        else:
            caps.discard(cap)
    return frozenset(caps)


def validate_caps_overrides(value, allowed=CAPABILITIES):
    """Renvoie une liste d'erreurs (vide si valide). Liste fermee de cles, valeurs booleennes."""
    if not isinstance(value, dict):
        return ['Format invalide : un objet {"capability": true|false} est attendu.']
    errors = []
    for cap, enabled in value.items():
        if cap not in allowed:
            errors.append(f'Capability inconnue : {cap[:50]}.')
        elif not isinstance(enabled, bool):
            errors.append(f'La valeur de {cap} doit etre true ou false.')
    return errors
