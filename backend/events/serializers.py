from decimal import Decimal

from rest_framework import serializers

from organizations.capabilities import STAFF_CAPABILITIES, validate_caps_overrides
from organizations.serializers import PublicOrganizationSerializer
from ticketing.serializers import FcfaField, PublicTicketTypeSerializer

from .models import Event, EventStaff

DESCRIPTION_MAX_LENGTH = 20000
MAX_TAGS = 10
TAG_MAX_LENGTH = 30


class OrganizationRefSerializer(serializers.Serializer):
    name = serializers.CharField()
    slug = serializers.CharField()


class EventListSerializer(serializers.ModelSerializer):
    organization = OrganizationRefSerializer(read_only=True)
    min_price = FcfaField(read_only=True, allow_null=True)

    class Meta:
        model = Event
        fields = (
            'slug', 'title', 'event_type', 'poster', 'start_at', 'end_at', 'venue_name', 'city',
            'is_featured', 'min_price', 'organization',
        )
        read_only_fields = fields


class EventDetailSerializer(serializers.ModelSerializer):
    organization = PublicOrganizationSerializer(read_only=True)
    ticket_types = serializers.SerializerMethodField()
    sales_open = serializers.SerializerMethodField()
    fees_mode = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = (
            'slug', 'title', 'description', 'event_type', 'poster', 'start_at', 'end_at',
            'venue_name', 'venue_address', 'city', 'latitude', 'longitude', 'capacity', 'min_age',
            'tags', 'is_featured', 'status', 'organization', 'sales_open', 'fees_mode', 'ticket_types',
        )
        read_only_fields = fields

    def _ticketing(self, obj):
        return getattr(obj, 'ticketing', None)

    def get_sales_open(self, obj):
        ticketing = self._ticketing(obj)
        return bool(ticketing and ticketing.sales_open and obj.status == Event.STATUS_PUBLISHED)

    def get_fees_mode(self, obj):
        ticketing = self._ticketing(obj)
        return ticketing.fees_mode if ticketing else None

    def get_ticket_types(self, obj):
        ctx = {**self.context, 'ticketing': self._ticketing(obj)}
        return PublicTicketTypeSerializer(obj.active_ticket_types, many=True, context=ctx).data


def validate_tags(value):
    if not isinstance(value, list) or len(value) > MAX_TAGS:
        raise serializers.ValidationError(f'Maximum {MAX_TAGS} tags.')
    cleaned = []
    for tag in value:
        if not isinstance(tag, str) or not tag.strip() or len(tag) > TAG_MAX_LENGTH:
            raise serializers.ValidationError(f'Chaque tag doit faire entre 1 et {TAG_MAX_LENGTH} caracteres.')
        tag = tag.strip().lower()
        if tag not in cleaned:
            cleaned.append(tag)
    return cleaned


class OrganizerEventSerializer(serializers.ModelSerializer):
    latitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, min_value=Decimal('-90'), max_value=Decimal('90'),
        required=False, allow_null=True,
    )
    longitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, min_value=Decimal('-180'), max_value=Decimal('180'),
        required=False, allow_null=True,
    )
    sales_open = serializers.BooleanField(source='ticketing.sales_open', read_only=True, default=False)

    class Meta:
        model = Event
        fields = (
            'id', 'slug', 'title', 'description', 'event_type', 'poster', 'start_at', 'end_at',
            'venue_name', 'venue_address', 'city', 'latitude', 'longitude', 'capacity', 'min_age',
            'tags', 'status', 'is_featured', 'sales_open', 'published_at', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'slug', 'is_featured', 'published_at', 'created_at', 'updated_at')
        extra_kwargs = {
            'description': {'max_length': DESCRIPTION_MAX_LENGTH},
            'capacity': {'min_value': 1, 'max_value': 1_000_000},
            'min_age': {'max_value': 99},
        }

    def validate_tags(self, value):
        return validate_tags(value)

    def validate_city(self, value):
        return ' '.join(value.split()).title()

    def validate(self, attrs):
        start = attrs.get('start_at', getattr(self.instance, 'start_at', None))
        end = attrs.get('end_at', getattr(self.instance, 'end_at', None))
        if start and end and end <= start:
            raise serializers.ValidationError({'end_at': ['La fin doit etre apres le debut.']})
        lat = attrs.get('latitude', getattr(self.instance, 'latitude', None))
        lng = attrs.get('longitude', getattr(self.instance, 'longitude', None))
        if (lat is None) != (lng is None):
            raise serializers.ValidationError({'latitude': ['Renseigne a la fois la latitude et la longitude.']})
        return attrs


class EventStaffSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    capabilities = serializers.SerializerMethodField()

    class Meta:
        model = EventStaff
        fields = (
            'id', 'email', 'user_name', 'role', 'gate', 'ticket_types', 'custom_caps', 'capabilities',
            'is_active', 'created_at',
        )
        read_only_fields = ('id', 'user_name', 'created_at')
        extra_kwargs = {'ticket_types': {'required': False}}

    def get_capabilities(self, obj):
        return sorted(obj.capabilities)

    def get_fields(self):
        fields = super().get_fields()
        event = self.context.get('event')
        if event is not None:
            fields['ticket_types'].child_relation.queryset = event.ticket_types.all()
        if self.instance is not None:
            fields['email'].read_only = True
        return fields

    def validate_email(self, value):
        return value.strip().lower()

    def validate_custom_caps(self, value):
        errors = validate_caps_overrides(value, allowed=STAFF_CAPABILITIES)
        if errors:
            raise serializers.ValidationError(errors)
        return value

    def validate_ticket_types(self, value):
        if len(value) > 50:
            raise serializers.ValidationError('Trop de types de billets selectionnes.')
        return value
