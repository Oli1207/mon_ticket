# Design & Architecture — Mon Ticket

Document de reference complet. Voir aussi le doc interactif Claude Docs.

## Identite visuelle

Inspirations : dice.fm (editorial brut, typo condensee geante), shotgun.live (dark mode vivant, neons), luma.com (elegance minimale, whitespace).

Notre ADN : typographie-first, max contraste (noir/blanc/or), images comme affiches de cinema, zero border-radius, espace negatif genereux.

## Architecture technique

### Backend (Django 6)

```
backend/
  config/           # settings, urls, wsgi, asgi, celery
  users/             # CustomUser (AbstractUser) + auth
  organizations/     # Organization, Membership, Invitation, AuditLog
  events/            # Event, EventStaff
  ticketing/         # PricingSettings, PartnerCode, EventTicketing, TicketType,
                     # PromoCode, Order, OrderItem, Ticket, ScanLog, PricingChange
```

### Frontend (React 19 + Vite 8)

```
frontend/src/
  styles/
    tokens.css       # CSS custom properties (couleurs, typo, espacement)
    reset.css        # Normalisation
    animations.css   # Keyframes reutilisables
  components/        # Composants reutilisables (Button, Card, Modal, etc.)
  layouts/           # PublicLayout, OrganizerLayout, AdminLayout
  pages/
    public/          # Home, EventDetail, Checkout, OrderConfirmation, OrganizerProfile
    organizer/       # Dashboard, EventEditor (8 onglets), Finance, Audience, Settings
    admin/           # AdminDashboard, Users, Organizations, Pricing, AuditLog
    auth/            # Login, Register, ForgotPassword
  hooks/             # useFetch, useAuth, useOrg
  stores/            # Zustand stores (auth, cart, ui)
  api/               # Axios client + interceptors
  lib/               # Utilitaires (format, money, date)
```

## Modeles de donnees

### Users
```python
class CustomUser(AbstractUser):
    phone_number = CharField(max_length=20, blank=True)
    city = CharField(max_length=100, blank=True)
    avatar = ImageField(upload_to='avatars/', null=True, blank=True)
    role = CharField(choices=[('user','User'), ('organizer','Organizer'), ('admin','Admin')], default='user')
```

### Organizations
```python
class Organization(models.Model):
    # Kind: promoter, venue, label, collective, association, company
    # Status: pending, approved, rejected, suspended
    name = CharField(max_length=200)
    slug = SlugField(unique=True)
    kind = CharField(choices=ORG_KINDS)
    status = CharField(choices=ORG_STATUSES, default='pending')
    owner = ForeignKey(User, on_delete=CASCADE)
    logo = ImageField(null=True, blank=True)
    bio = TextField(blank=True)
    website = URLField(blank=True)
    social_links = JSONField(default=dict)  # {"instagram": "...", "twitter": "..."}
    paystack_subaccount_code = CharField(max_length=100, blank=True)
    verified_at = DateTimeField(null=True)
    created_at = DateTimeField(auto_now_add=True)

class Membership(models.Model):
    organization = ForeignKey(Organization, on_delete=CASCADE)
    user = ForeignKey(User, on_delete=CASCADE)
    role = CharField(choices=ORG_ROLES)  # owner, admin, producer, finance, door_manager
    custom_caps = JSONField(default=dict)  # {"finance.view": true, "events.manage": false}
    is_active = BooleanField(default=True)
    invited_by = ForeignKey(User, null=True, blank=True)
    created_at = DateTimeField(auto_now_add=True)

    @property
    def capabilities(self):
        caps = set(ROLE_DEFAULTS[self.role])
        for cap, enabled in self.custom_caps.items():
            caps.add(cap) if enabled else caps.discard(cap)
        return caps

class Invitation(models.Model):
    organization = ForeignKey(Organization, on_delete=CASCADE)
    email = EmailField()
    role = CharField(choices=ORG_ROLES)
    custom_caps = JSONField(default=dict)
    token_hash = CharField(max_length=64)
    status = CharField(choices=['pending','accepted','revoked'], default='pending')
    invited_by = ForeignKey(User, on_delete=CASCADE)
    expires_at = DateTimeField()
    created_at = DateTimeField(auto_now_add=True)
```

### Events
```python
class Event(models.Model):
    organization = ForeignKey(Organization, on_delete=CASCADE)
    title = CharField(max_length=300)
    slug = SlugField()
    description = TextField(blank=True)
    event_type = CharField(choices=[
        ('concert','Concert'), ('festival','Festival'), ('comedy','Comedy'),
        ('theatre','Theatre'), ('party','Party'), ('conference','Conference'),
        ('workshop','Workshop'), ('exhibition','Exhibition'), ('other','Other')
    ])
    poster = ImageField(upload_to='posters/')
    start_at = DateTimeField()
    end_at = DateTimeField(null=True, blank=True)
    venue_name = CharField(max_length=200)
    venue_address = CharField(max_length=500, blank=True)
    city = CharField(max_length=100)
    latitude = DecimalField(max_digits=9, decimal_places=6, null=True)
    longitude = DecimalField(max_digits=9, decimal_places=6, null=True)
    capacity = PositiveIntegerField(null=True, blank=True)
    min_age = PositiveIntegerField(null=True, blank=True)
    is_published = BooleanField(default=False)
    is_featured = BooleanField(default=False)
    tags = JSONField(default=list)
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)

class EventStaff(models.Model):
    event = ForeignKey(Event, on_delete=CASCADE)
    user = ForeignKey(User, on_delete=CASCADE, null=True, blank=True)
    email = EmailField()
    role = CharField(choices=[
        ('manager','Manager'), ('scanner','Scanner'),
        ('vip_host','VIP Host'), ('security','Security')
    ])
    gate = CharField(max_length=50, blank=True)
    ticket_types = ManyToManyField('ticketing.TicketType', blank=True)
    custom_caps = JSONField(default=dict)
    is_active = BooleanField(default=True)
```

### Ticketing
```python
class PricingSettings(models.Model):
    # Singleton pk=1, cached 5min
    commission_percent = DecimalField(default=Decimal('6.00'), max_digits=5, decimal_places=2)
    mobile_money_fee_percent = DecimalField(default=Decimal('1.50'), max_digits=5, decimal_places=2)
    card_fee_percent = DecimalField(default=Decimal('1.50'), max_digits=5, decimal_places=2)
    reservation_minutes = PositiveIntegerField(default=20)
    max_tickets_per_order = PositiveIntegerField(default=10)
    max_pending_per_email = PositiveIntegerField(default=2)
    max_pending_per_ip = PositiveIntegerField(default=20)

class PartnerCode(models.Model):
    code = CharField(max_length=50, unique=True)
    commission_percent = DecimalField(max_digits=5, decimal_places=2)
    expires_at = DateTimeField(null=True, blank=True)
    max_uses = PositiveIntegerField(null=True, blank=True)
    uses = PositiveIntegerField(default=0)
    is_active = BooleanField(default=True)
    created_by = ForeignKey(User, on_delete=CASCADE)
    created_at = DateTimeField(auto_now_add=True)

class EventTicketing(models.Model):
    event = OneToOneField(Event, on_delete=CASCADE)
    fees_mode = CharField(choices=[('buyer','Buyer'), ('organizer','Organizer')], default='buyer')
    commission_override = DecimalField(null=True, blank=True, max_digits=5, decimal_places=2)
    partner_code = ForeignKey(PartnerCode, null=True, blank=True, on_delete=SET_NULL)
    sales_open = BooleanField(default=False)

class TicketType(models.Model):
    event = ForeignKey(Event, on_delete=CASCADE)
    name = CharField(max_length=100)
    description = TextField(blank=True)
    price = DecimalField(max_digits=10, decimal_places=0)  # FCFA, pas de decimales
    quota = PositiveIntegerField()
    sold_count = PositiveIntegerField(default=0)
    reserved_count = PositiveIntegerField(default=0)
    commission_override = DecimalField(null=True, blank=True, max_digits=5, decimal_places=2)
    sales_start = DateTimeField(null=True, blank=True)
    sales_end = DateTimeField(null=True, blank=True)
    max_per_order = PositiveIntegerField(default=10)
    sort_order = PositiveIntegerField(default=0)
    is_active = BooleanField(default=True)

class PromoCode(models.Model):
    event = ForeignKey(Event, on_delete=CASCADE)
    code = CharField(max_length=30)
    kind = CharField(choices=[('percent','Percent'), ('amount','Amount')])
    value = DecimalField(max_digits=10, decimal_places=2)
    max_uses = PositiveIntegerField(null=True, blank=True)
    uses = PositiveIntegerField(default=0)
    expires_at = DateTimeField(null=True, blank=True)
    is_active = BooleanField(default=True)

class Order(models.Model):
    public_id = CharField(max_length=20, unique=True)
    access_token = CharField(max_length=64)
    event = ForeignKey(Event, on_delete=CASCADE)
    email = EmailField()
    phone = CharField(max_length=20)
    full_name = CharField(max_length=200)
    client_ip = GenericIPAddressField()
    status = CharField(choices=[
        ('pending','Pending'), ('paid','Paid'), ('expired','Expired'),
        ('cancelled','Cancelled'), ('refunded','Refunded'), ('needs_refund','Needs Refund')
    ], default='pending')
    channel = CharField(max_length=20, default='web')
    promo_code = ForeignKey(PromoCode, null=True, blank=True, on_delete=SET_NULL)
    subtotal = DecimalField(max_digits=10, decimal_places=0)
    discount = DecimalField(max_digits=10, decimal_places=0, default=0)
    service_fee = DecimalField(max_digits=10, decimal_places=0)
    total = DecimalField(max_digits=10, decimal_places=0)
    organizer_net = DecimalField(max_digits=10, decimal_places=0)
    commission_amount = DecimalField(max_digits=10, decimal_places=0)
    commission_percent = DecimalField(max_digits=5, decimal_places=2)
    fees_mode = CharField(max_length=10)
    paystack_reference = CharField(max_length=100, blank=True)
    expires_at = DateTimeField()
    paid_at = DateTimeField(null=True, blank=True)
    created_at = DateTimeField(auto_now_add=True)

class OrderItem(models.Model):
    order = ForeignKey(Order, on_delete=CASCADE, related_name='items')
    ticket_type = ForeignKey(TicketType, on_delete=CASCADE)
    quantity = PositiveIntegerField()
    unit_price = DecimalField(max_digits=10, decimal_places=0)

class Ticket(models.Model):
    order = ForeignKey(Order, on_delete=CASCADE, related_name='tickets')
    ticket_type = ForeignKey(TicketType, on_delete=CASCADE)
    public_id = CharField(max_length=20, unique=True)
    status = CharField(choices=[
        ('valid','Valid'), ('used','Used'), ('cancelled','Cancelled')
    ], default='valid')
    holder_name = CharField(max_length=200, blank=True)
    holder_email = EmailField(blank=True)
    used_at = DateTimeField(null=True, blank=True)
    checked_in_by = ForeignKey(User, null=True, blank=True, on_delete=SET_NULL)
    gate = CharField(max_length=50, blank=True)
    transferred_from = ForeignKey('self', null=True, blank=True, on_delete=SET_NULL)
    created_at = DateTimeField(auto_now_add=True)

    @property
    def qr_payload(self):
        import hmac, hashlib
        from django.conf import settings
        sign = hmac.new(
            settings.QR_SECRET_KEY.encode(),
            self.public_id.encode(),
            hashlib.sha256
        ).hexdigest()[:12]
        return f"SPT1.{self.public_id}.{sign}"

class ScanLog(models.Model):
    ticket = ForeignKey(Ticket, on_delete=CASCADE)
    event = ForeignKey(Event, on_delete=CASCADE)
    scanned_by = ForeignKey(User, on_delete=CASCADE)
    result = CharField(choices=[
        ('accepted','Accepted'), ('already_used','Already Used'),
        ('invalid','Invalid'), ('wrong_event','Wrong Event'),
        ('not_allowed','Not Allowed'), ('cancelled','Cancelled')
    ])
    gate = CharField(max_length=50, blank=True)
    created_at = DateTimeField(auto_now_add=True)

class PricingChange(models.Model):
    changed_by = ForeignKey(User, on_delete=CASCADE)
    target = CharField(max_length=200)
    field = CharField(max_length=100)
    old_value = CharField(max_length=200)
    new_value = CharField(max_length=200)
    created_at = DateTimeField(auto_now_add=True)
    # Immutable: no update, no delete
```

## Capabilities system

```python
CAPABILITIES = {
    'org.manage', 'team.manage', 'audit.view',
    'events.view', 'events.manage',
    'staff.manage',
    'attendees.view', 'attendees.contact', 'attendees.export',
    'sales.view',
    'finance.view', 'finance.export',
    'checkin',
    'marketing.view', 'marketing.manage',
    'analytics.view',
}

ROLE_DEFAULTS = {
    'owner': CAPABILITIES,  # all
    'admin': CAPABILITIES - {'org.manage'},
    'producer': {
        'events.view', 'events.manage', 'staff.manage',
        'attendees.view', 'attendees.contact', 'attendees.export',
        'marketing.view', 'marketing.manage', 'analytics.view',
    },
    'finance': {'sales.view', 'finance.view', 'finance.export'},
    'door_manager': {'checkin', 'staff.manage', 'attendees.view'},
}
```

Roles are templates. When adding a member, the organizer sees toggles for each capability.
Default caps are pre-checked, they can add/remove individually.
Stored in `Membership.custom_caps` as JSON overrides.

## Paystack integration

1. Each org creates a SubAccount (bank account or MoMo)
2. Transaction initialize with split params
3. Webhook verification: HMAC SHA-512 of body with secret key
4. Split: organizer_net -> subaccount, commission -> platform
5. Settlement: automatic by Paystack (T+1 bank, instant MoMo)

## Pages

### Public
- **Home** : hero carousel (featured events) + grid events par categorie + recherche/filtres
- **Event detail** : poster plein ecran + infos + selection billets + bouton acheter
- **Checkout** : recap commande + Paystack payment
- **Order confirmation** : recapitulatif + billets PDF + QR codes
- **Organizer profile** : page publique de l'organisateur avec ses evenements

### Organizer space (8 onglets par evenement)
1. Infos (titre, description, dates, lieu avec MapLibre, poster, categorie)
2. Billets (types, prix, quotas, drag-and-drop)
3. Vente & promos (ouverture/fermeture, fees_mode, promo codes)
4. Staff (invitation, roles avec caps personnalisables, portes)
5. Participants (liste, filtres, recherche, export)
6. Ventes (stats, graphes, detail par type de billet)
7. Analytics (velocite ventes, sources UTM, funnel conversion, carte geographique)
8. Check-in live (compteur temps reel, timeline scans, activite par porte)

Plus: Dashboard org, Audience CRM, Finance/Settlements, Audit log

### Admin
- Dashboard global (revenus, evenements, organisations)
- Gestion utilisateurs
- Validation organisations (pending -> approved/rejected)
- PricingSettings + PartnerCodes
- Audit log global

## Design tokens (CSS custom properties)

```css
:root {
  --color-bg: #000;
  --color-text: #fff;
  --color-accent: #D4A017;
  --color-accent-hover: #C49315;
  --color-muted: #666;
  --color-surface: #111;
  --color-surface-hover: #1a1a1a;
  --color-border: #222;
  --color-danger: #E53E3E;
  --color-success: #38A169;
  --font-display: 'Inter Tight', sans-serif;
  --font-body: 'Inter', sans-serif;
  --radius-none: 0px;
  --radius-sm: 2px;
  --radius-pill: 999px;
  --space-section: 80px;
  --transition-fast: 150ms ease;
  --transition-base: 300ms ease-out;
}
```
