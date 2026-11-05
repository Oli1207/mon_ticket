from datetime import datetime, time

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Prefetch, Q, Subquery
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from config.slugs import build_slug
from organizations.audit import diff_fields, log_action
from organizations.permissions import HasOrgCapability, OrgScopedMixin
from ticketing.models import EventTicketing, Order, PricingSettings, TicketType
from ticketing.services import event_detail_cache_key, invalidate_event_caches
from ticketing.tasks import cancel_event_orders

from .mixins import OrgEventMixin
from .models import Event, EventStaff
from .serializers import EventDetailSerializer, EventListSerializer, EventStaffSerializer, OrganizerEventSerializer

User = get_user_model()

EVENT_DETAIL_CACHE_TTL = 10
SEARCH_MAX_LENGTH = 100


def _min_price_subquery():
    return Subquery(
        TicketType.objects.filter(event=OuterRef('pk'), is_active=True).order_by('price').values('price')[:1]
    )


class PublicEventListView(generics.ListAPIView):
    """Evenements publies a venir. Filtres : city, type, featured, q, organization, date_from, date_to."""

    permission_classes = (permissions.AllowAny,)
    serializer_class = EventListSerializer

    def get_queryset(self):
        params = self.request.query_params
        now = timezone.now()
        qs = Event.objects.public().filter(Q(start_at__gte=now) | Q(end_at__gte=now))

        if city := params.get('city', '').strip():
            qs = qs.filter(city=' '.join(city.split()).title()[:100])
        if event_type := params.get('type'):
            if event_type not in dict(Event.TYPE_CHOICES):
                raise ValidationError({'type': ["Type d'evenement inconnu."]})
            qs = qs.filter(event_type=event_type)
        if params.get('featured') in ('1', 'true'):
            qs = qs.filter(is_featured=True)
        if org := params.get('organization'):
            qs = qs.filter(organization__slug=org[:80])
        if q := params.get('q', '').strip():
            if len(q) > SEARCH_MAX_LENGTH:
                raise ValidationError({'q': [f'Recherche limitee a {SEARCH_MAX_LENGTH} caracteres.']})
            qs = qs.filter(Q(title__icontains=q) | Q(venue_name__icontains=q))
        for param, lookup, at in (('date_from', 'start_at__gte', time.min), ('date_to', 'start_at__lte', time.max)):
            if raw := params.get(param):
                day = parse_date(raw[:10])
                if day is None:
                    raise ValidationError({param: ['Date invalide, format attendu : AAAA-MM-JJ.']})
                qs = qs.filter(**{lookup: timezone.make_aware(datetime.combine(day, at))})

        return (
            qs.select_related('organization')
            .annotate(min_price=_min_price_subquery())
            .only(
                'slug', 'title', 'event_type', 'poster', 'start_at', 'end_at', 'venue_name', 'city',
                'is_featured', 'organization__name', 'organization__slug',
            )
            .order_by('start_at', 'pk')
        )


class PublicEventDetailView(generics.RetrieveAPIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = EventDetailSerializer

    def get_object(self):
        return get_object_or_404(
            Event.objects.public()
            .select_related('organization', 'ticketing', 'ticketing__partner_code')
            .prefetch_related(
                Prefetch(
                    'ticket_types',
                    queryset=TicketType.objects.filter(is_active=True).order_by('sort_order', 'id'),
                    to_attr='active_ticket_types',
                )
            ),
            slug=self.kwargs['slug'],
        )

    def retrieve(self, request, *args, **kwargs):
        # Cache tres court : absorbe les rushs sans afficher un stock trop perime.
        key = event_detail_cache_key(self.kwargs['slug'][:80])
        data = cache.get(key)
        if data is None:
            event = self.get_object()
            context = {
                'request': request,
                'pricing': PricingSettings.load(),
                'now': timezone.now(),
            }
            data = EventDetailSerializer(event, context=context).data
            cache.set(key, data, EVENT_DETAIL_CACHE_TTL)
        return Response(data)


class OrganizerEventListCreateView(OrgScopedMixin, generics.ListCreateAPIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    serializer_class = OrganizerEventSerializer
    capability_map = {'GET': 'events.view', 'POST': 'events.manage'}
    parser_classes = (JSONParser, MultiPartParser, FormParser)

    def get_queryset(self):
        qs = Event.objects.filter(organization=self.get_organization()).select_related('ticketing')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            if status_filter not in dict(Event.STATUS_CHOICES):
                raise ValidationError({'status': ['Statut inconnu.']})
            qs = qs.filter(status=status_filter)
        return qs.order_by('-start_at', '-pk')

    def perform_create(self, serializer):
        if serializer.validated_data.get('status', Event.STATUS_DRAFT) != Event.STATUS_DRAFT:
            raise ValidationError({'status': ['Un evenement est cree en brouillon. Publie-le ensuite.']})
        org = self.get_organization()
        for _ in range(3):
            try:
                with transaction.atomic():
                    event = serializer.save(
                        organization=org,
                        slug=build_slug(serializer.validated_data['title'], suffix=True),
                        status=Event.STATUS_DRAFT,
                    )
                    EventTicketing.objects.create(event=event)
                break
            except IntegrityError:
                continue
        else:
            raise ValidationError({'detail': "Impossible de creer l'evenement, reessaie."})
        log_action(self.request, 'event.created', organization=org, target=event)


class OrganizerEventDetailView(OrgEventMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    serializer_class = OrganizerEventSerializer
    capability_map = {'GET': 'events.view', 'PATCH': 'events.manage', 'DELETE': 'events.manage'}
    parser_classes = (JSONParser, MultiPartParser, FormParser)
    http_method_names = ('get', 'patch', 'delete')

    def get_object(self):
        return self.get_event()

    def _validate_transition(self, event, new_status, attrs):
        if new_status == event.status:
            return
        if event.status == Event.STATUS_CANCELLED:
            raise ValidationError({'status': ['Un evenement annule ne peut plus changer de statut.']})
        if new_status == Event.STATUS_PUBLISHED:
            if not event.organization.is_approved:
                raise ValidationError({'status': ["Ton organisation doit etre validee par l'equipe Mon Ticket avant de publier."]})
            poster = attrs.get('poster', event.poster)
            if not poster:
                raise ValidationError({'poster': ['Ajoute une affiche avant de publier.']})
            if attrs.get('start_at', event.start_at) <= timezone.now():
                raise ValidationError({'start_at': ['La date de debut doit etre dans le futur.']})
        if new_status == Event.STATUS_DRAFT and Order.objects.filter(event=event).exists():
            raise ValidationError({'status': ['Des commandes existent : annule plutot l\'evenement.']})

    def perform_update(self, serializer):
        event = serializer.instance
        attrs = serializer.validated_data
        new_status = attrs.get('status', event.status)
        self._validate_transition(event, new_status, attrs)
        if event.status == Event.STATUS_CANCELLED:
            raise ValidationError({'detail': 'Un evenement annule ne peut plus etre modifie.'})
        changes = diff_fields(event, attrs)
        extra = {}
        if new_status == Event.STATUS_PUBLISHED and event.published_at is None:
            extra['published_at'] = timezone.now()
        with transaction.atomic():
            event = serializer.save(**extra)
            if new_status == Event.STATUS_CANCELLED:
                EventTicketing.objects.filter(event=event).update(sales_open=False)
                transaction.on_commit(lambda: cancel_event_orders.delay(event.pk))
            if changes:
                log_action(self.request, f'event.{new_status}' if 'status' in changes else 'event.updated',
                           organization=event.organization, target=event, changes=changes)
        invalidate_event_caches(event)

    def perform_destroy(self, event):
        if event.status != Event.STATUS_DRAFT or Order.objects.filter(event=event).exists():
            raise ValidationError({'detail': 'Seul un brouillon sans commande peut etre supprime. Sinon, annule l\'evenement.'})
        log_action(self.request, 'event.deleted', organization=event.organization, target=event)
        event.delete()

    def destroy(self, request, *args, **kwargs):
        self.perform_destroy(self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)


class EventStaffListCreateView(OrgEventMixin, generics.ListCreateAPIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    serializer_class = EventStaffSerializer
    capability_map = {'*': 'staff.manage'}

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'event': self.get_event()}

    def get_queryset(self):
        return (
            EventStaff.objects.filter(event=self.get_event())
            .select_related('user')
            .prefetch_related('ticket_types')
            .order_by('created_at')
        )

    def perform_create(self, serializer):
        event = self.get_event()
        email = serializer.validated_data['email']
        user = User.objects.filter(email=email, is_active=True).first()
        if user is None:
            raise ValidationError({'email': ["Aucun compte Mon Ticket avec cet email. Demande-lui d'en creer un, puis reessaie."]})
        try:
            with transaction.atomic():
                staff = serializer.save(event=event, user=user, added_by=self.request.user)
        except IntegrityError:
            raise ValidationError({'email': ['Cette personne fait deja partie du staff de cet evenement.']})
        log_action(self.request, 'staff.added', organization=event.organization, target=staff,
                   changes={'role': staff.role, 'gate': staff.gate})


class EventStaffDetailView(OrgEventMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    serializer_class = EventStaffSerializer
    capability_map = {'*': 'staff.manage'}
    http_method_names = ('get', 'patch', 'delete')

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'event': self.get_event()}

    def get_queryset(self):
        return EventStaff.objects.filter(event=self.get_event()).select_related('user').prefetch_related('ticket_types')

    def perform_update(self, serializer):
        changes = diff_fields(serializer.instance, {k: v for k, v in serializer.validated_data.items() if k != 'ticket_types'})
        staff = serializer.save()
        event = self.get_event()
        log_action(self.request, 'staff.updated', organization=event.organization, target=staff, changes=changes)

    def perform_destroy(self, staff):
        event = self.get_event()
        log_action(self.request, 'staff.removed', organization=event.organization, target=staff)
        staff.delete()
