from rest_framework import serializers
from .models import BookingRequest, Slot

class SlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = Slot
        fields = ['id', 'resource', 'starts_at', 'ends_at']

class BookingSerializer(serializers.ModelSerializer):
    slot = SlotSerializer(read_only=True)
    class Meta:
        model = BookingRequest
        fields = ['id', 'reference', 'patient_name', 'exam', 'source', 'preferred_windows', 'preferred_slot_ids', 'price_snapshot', 'status', 'slot', 'confirmed_at', 'created_at']

class ConfirmationSerializer(serializers.Serializer):
    slot_id = serializers.IntegerField(min_value=1)
