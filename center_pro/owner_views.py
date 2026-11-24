from decimal import Decimal
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from .models import Offer, PriceChange, OutreachTarget, OutreachActivity, AuditEvent
from .views import CenterView, Pagination
from .services import Conflict


class OwnerView(CenterView):
    def owner(self, request, center_id):
        membership = self.membership(request, center_id)
        if membership.role != 'owner':
            raise PermissionDenied('Owner access is required for this section.')
        return membership


class OfferSerializer(serializers.ModelSerializer):
    class Meta:
        model = Offer
        fields = ['id', 'exam', 'price', 'benchmark_price', 'benchmark_source', 'benchmark_checked_at', 'version', 'updated_at']


class PriceInput(serializers.Serializer):
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))
    expected_version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=500, allow_blank=False, trim_whitespace=True)


class ActivityInput(serializers.Serializer):
    kind = serializers.ChoiceField(choices=OutreachActivity._meta.get_field('kind').choices)
    note = serializers.CharField(max_length=2000, allow_blank=False, trim_whitespace=True)


class TargetSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachTarget
        fields = ['id', 'name', 'category', 'distance_miles', 'status', 'updated_at']


def paginated(request, rows, serializer):
    paginator = Pagination()
    page = paginator.paginate_queryset(rows, request)
    return paginator.get_paginated_response(serializer(page, many=True).data)


class OffersView(OwnerView):
    def get(self, request, center_id):
        self.owner(request, center_id)
        rows = Offer.objects.filter(center_id=center_id)
        query = request.query_params.get('search', '').strip()[:120]
        if query:
            rows = rows.filter(exam__icontains=query)
        return paginated(request, rows.order_by('exam', 'id'), OfferSerializer)


class OfferView(OwnerView):
    def get(self, request, center_id, offer_id):
        self.owner(request, center_id)
        return Response(OfferSerializer(get_object_or_404(Offer, pk=offer_id, center_id=center_id)).data)

    @transaction.atomic
    def post(self, request, center_id, offer_id):
        self.owner(request, center_id)
        data = PriceInput(data=request.data)
        data.is_valid(raise_exception=True)
        offer = get_object_or_404(Offer.objects.select_for_update(), pk=offer_id, center_id=center_id)
        values = data.validated_data
        if offer.version != values['expected_version']:
            raise Conflict('Another owner updated this price. Reload the offer and review the latest price before saving.')
        if offer.price == values['price']:
            raise ValidationError({'price': 'Enter a different price.'})
        PriceChange.objects.create(offer=offer, actor=request.user, old_price=offer.price, new_price=values['price'], reason=values['reason'])
        offer.price = values['price']
        offer.version += 1
        offer.save(update_fields=['price', 'version', 'updated_at'])
        AuditEvent.objects.create(center_id=center_id, actor=request.user, action='offer.price_changed', reference=f'OFFER-{offer.id}')
        from .integration import queue_price_review
        queue_price_review(offer,request.user,values['reason'])
        # BookingRequest.price_snapshot and issued-document snapshots are never updated here.
        return Response(OfferSerializer(offer).data)


class PriceHistorySerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField()
    def get_actor(self, obj):
        return obj.actor.get_full_name() or obj.actor.username
    class Meta:
        model = PriceChange
        fields = ['id', 'actor', 'old_price', 'new_price', 'reason', 'created_at']


class PriceHistoryView(OwnerView):
    def get(self, request, center_id, offer_id):
        self.owner(request, center_id)
        offer = get_object_or_404(Offer, pk=offer_id, center_id=center_id)
        return paginated(request, offer.changes.select_related('actor').order_by('-created_at', '-id'), PriceHistorySerializer)


class TargetsView(OwnerView):
    def get(self, request, center_id):
        self.owner(request, center_id)
        rows = OutreachTarget.objects.filter(center_id=center_id)
        query = request.query_params.get('search', '').strip()[:120]
        status = request.query_params.get('status', '')
        if query:
            rows = rows.filter(Q(name__icontains=query) | Q(category__icontains=query))
        if status:
            if status not in ('not_contacted', 'visited', 'follow_up', 'rate_sheet_shared'):
                raise ValidationError({'status': 'Invalid outreach status.'})
            rows = rows.filter(status=status)
        return paginated(request, rows.order_by('distance_miles', 'id'), TargetSerializer)


class TargetView(OwnerView):
    def get(self, request, center_id, target_id):
        self.owner(request, center_id)
        return Response(TargetSerializer(get_object_or_404(OutreachTarget, pk=target_id, center_id=center_id)).data)


class ActivitySerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField()
    def get_actor(self, obj):
        return obj.actor.get_full_name() or obj.actor.username
    class Meta:
        model = OutreachActivity
        fields = ['id', 'actor', 'kind', 'note', 'created_at']


class ActivitiesView(OwnerView):
    def get(self, request, center_id, target_id):
        self.owner(request, center_id)
        target = get_object_or_404(OutreachTarget, pk=target_id, center_id=center_id)
        return paginated(request, target.activities.select_related('actor').order_by('-created_at', '-id'), ActivitySerializer)

    @transaction.atomic
    def post(self, request, center_id, target_id):
        self.owner(request, center_id)
        data = ActivityInput(data=request.data)
        data.is_valid(raise_exception=True)
        target = get_object_or_404(OutreachTarget.objects.select_for_update(), pk=target_id, center_id=center_id)
        activity = OutreachActivity.objects.create(target=target, actor=request.user, **data.validated_data)
        target.status = activity.kind
        target.save(update_fields=['status', 'updated_at'])
        AuditEvent.objects.create(center_id=center_id, actor=request.user, action='outreach.'+activity.kind, reference=f'TARGET-{target.id}')
        return Response(ActivitySerializer(activity).data, status=201)


class DirectoryPricesView(OwnerView):
    """Public directory prices only; never expose other centers' private offers."""
    def get(self, request, center_id):
        import json
        import core
        from directory_policy import visible_sql
        from .integration import DomainConnection
        membership = self.owner(request, center_id)
        db = DomainConnection()
        batch = core.active_batch(db)
        predicate, values = visible_sql()
        published = {}
        for row in db.execute('SELECT entry_key,payload,amount_cents FROM offers WHERE batch_id=? AND '+predicate, (batch,*values)).fetchall():
            item = json.loads(row['payload'])
            published.setdefault(row['entry_key'], []).append({
                'exam': item.get('mod', 'Imaging'), 'scope': item.get('scope', ''),
                'contrast': item.get('contrast', 'unknown'),
                'price': row['amount_cents'] / 100 if row['amount_cents'] is not None else None,
                'type': item.get('type', 'quote'),
            })
        rows = []
        for row in db.execute('SELECT entry_key,payload FROM entries WHERE batch_id=? AND '+predicate, (batch,*values)).fetchall():
            entry = json.loads(row['payload'])
            rows.append({'id': row['entry_key'], 'name': entry.get('name', ''),
                'location': ', '.join(filter(None, [entry.get('area'), entry.get('states')])),
                'mri': entry.get('mri') or 'Not listed', 'ct': entry.get('ct') or 'Not listed',
                'other': entry.get('other') or 'Not listed',
                'offers': published.get(row['entry_key'], []),
                'checked': entry.get('checked') or '',
                'current': row['entry_key'] == membership.center.directory_key})
        current = next((r for r in rows if r['current']), None)
        # Compare only identical published exam, scope and contrast; no guessed medians.
        current_offers = (current or {}).get('offers', [])
        for row in rows:
            row['comparisons'] = []
            if row['current']: continue
            for offer in row['offers']:
                for own in current_offers:
                    if offer['price'] is not None and own['price'] is not None and all(offer[k] == own[k] for k in ('exam','scope','contrast','type')):
                        row['comparisons'].append({'exam': offer['exam'], 'scope': offer['scope'], 'contrast': offer['contrast'], 'difference': round(offer['price']-own['price'],2)})
        import re
        currency=re.compile(r'(?:\$\s*|\bUSD\s*)[0-9]|[0-9][0-9,.]*\s*USD\b',re.I)
        rows=[r for r in rows if any(o['price'] is not None and o['price']>0 for o in r['offers']) or any(currency.search(str(r[k])) for k in ('mri','ct','other'))]
        query = request.query_params.get('search', '').strip().casefold()[:120]
        rows = [r for r in rows if not query or query in ' '.join(str(r[k]) for k in ('name','location','mri','ct','other')).casefold()]
        rows.sort(key=lambda r: (not r['current'], r['name'].casefold(), r['id']))
        paginator = Pagination()
        page = paginator.paginate_queryset(rows, request)
        response = paginator.get_paginated_response(page)
        response.data['current_center'] = current
        return response
