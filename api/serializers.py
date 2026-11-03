"""Request contracts shared by DRF validation and OPTIONS metadata.

Domain services retain validation of catalog state, ownership, money semantics,
OTP use, date availability and encrypted booking capabilities.
"""
from rest_framework import serializers
import booking

CONTRAST = ('unknown', 'without', 'with', 'both')


class ObjectSerializer(serializers.Serializer):
    pass


class CenterEmailSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)


class CenterVerifySerializer(serializers.Serializer):
    verification_id = serializers.CharField(max_length=100)
    code = serializers.RegexField(r'^[0-9]{6}$', max_length=6)


class CenterRequestSerializer(serializers.Serializer):
    id = serializers.CharField(max_length=100)
    status = serializers.ChoiceField(choices=('scheduled', 'voicemail', 'unable'), required=False)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)


class RegistrationSerializer(LoginSerializer):
    name = serializers.CharField(max_length=100)
    phone = serializers.CharField(max_length=30, allow_blank=True, default='')
    zip_code = serializers.RegexField(r'^[0-9]{5}$', max_length=5, allow_blank=True, default='', error_messages={'invalid': 'Enter a five-digit ZIP code.'})

    def validate_password(self, value):
        if len(value) < 12:
            raise serializers.ValidationError('Use a password of 12–128 characters.')
        return value


class BudgetField(serializers.IntegerField):
    def to_internal_value(self, data):
        import re
        value = '0' if data == '' or data is None else str(data).strip()
        if not re.fullmatch(r'[0-9]{1,6}', value):
            raise serializers.ValidationError('Enter up to 6 digits for the budget, or leave it blank.')
        return super().to_internal_value(value)


class SearchSerializer(serializers.Serializer):
    location = serializers.CharField(max_length=120, allow_blank=True, default='')
    scope = serializers.ChoiceField(choices=('state', 'radius', 'city'), default='radius')
    radius = serializers.ChoiceField(choices=(25, 50, 100, 250), default=50)
    mod = serializers.ChoiceField(choices=('', 'MRI', 'CT'), default='')
    body = serializers.CharField(max_length=100, allow_blank=True, default='')
    contrast = serializers.ChoiceField(choices=CONTRAST, default='unknown')
    budget = BudgetField(min_value=0, max_value=999999, default=0)
    type = serializers.ChoiceField(choices=('all', 'fixed', 'starting', 'range', 'maximum', 'average', 'quote', 'conflict'), default='all')
    sort = serializers.ChoiceField(choices=('distance', 'price', 'scope'), default='distance')
    selected = serializers.ListField(child=serializers.CharField(max_length=200), max_length=3, default=list)


class LocationSearchSerializer(SearchSerializer):
    location = serializers.CharField(max_length=120, allow_blank=False)


class AgentSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=2000)
    search = SearchSerializer(default=dict)
    conversation_id = serializers.CharField(max_length=100, allow_null=True, required=False)

    def validate_message(self, value):
        from chat_questions import canonical_question
        question = canonical_question(value)
        if question is None:
            raise serializers.ValidationError('Choose one of the predefined questions. Free-text questions are not supported.')
        return question


class MessageSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=('user', 'assistant'))
    text = serializers.CharField(max_length=4000, allow_blank=True)


class ConversationSerializer(serializers.Serializer):
    id = serializers.CharField(max_length=100, required=False)
    messages = MessageSerializer(many=True, max_length=200, default=list)
    search = SearchSerializer(default=dict)
    private = serializers.BooleanField(default=False)


class PreferenceSerializer(serializers.Serializer):
    location = serializers.CharField(max_length=120, allow_blank=True)
    private = serializers.BooleanField(default=False)


class CorrectionSerializer(serializers.Serializer):
    offer_id = serializers.CharField(max_length=200)
    category = serializers.CharField(max_length=80, default='Other')
    description = serializers.CharField(max_length=1000)
    private = serializers.BooleanField(default=False)


class BookingOptionsSerializer(serializers.Serializer):
    clinic_id = serializers.CharField(max_length=200)
    offer_id = serializers.CharField(max_length=200, allow_blank=True, default='')


class PatientEmailSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)


class PatientPhoneSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=30)


class PatientCodeSerializer(serializers.Serializer):
    verification_id = serializers.CharField(max_length=100)
    code = serializers.RegexField(r'^\d{6}$', write_only=True)


class SlotSerializer(serializers.Serializer):
    date = serializers.RegexField(r'^\d{4}-\d{2}-\d{2}$')
    period = serializers.ChoiceField(choices=tuple(booking.PERIODS))


class BookingCreateSerializer(BookingOptionsSerializer):
    phone_verification_id = serializers.CharField(max_length=100, required=False)
    phone_verification_token = serializers.CharField(max_length=200, write_only=True, required=False)
    idempotency_key = serializers.CharField(max_length=80)
    verification_id = serializers.CharField(max_length=100, required=False)
    verification_token = serializers.CharField(max_length=200, write_only=True, required=False)
    email = serializers.EmailField(max_length=254, required=False)
    first_name = serializers.CharField(max_length=80)
    phone = serializers.CharField(max_length=30, allow_blank=True, default='')
    timezone = serializers.ChoiceField(choices=tuple(booking.ZONES), required=False)
    service = serializers.ChoiceField(choices=('MRI', 'CT', 'Other imaging', 'Please contact me'))
    exam = serializers.CharField(max_length=100, allow_blank=True, default='')
    contrast = serializers.ChoiceField(choices=CONTRAST, default='unknown')
    consent = serializers.BooleanField()
    slots = SlotSerializer(many=True, min_length=1, max_length=3)


class BookingCapabilitySerializer(serializers.Serializer):
    id = serializers.CharField(max_length=100)
    token = serializers.CharField(max_length=200, write_only=True)


class ClinicCodeSerializer(BookingCapabilitySerializer):
    code = serializers.RegexField(r'^\d{6}$', write_only=True)


class ClinicSessionSerializer(serializers.Serializer):
    id = serializers.CharField(max_length=100)
    session = serializers.CharField(max_length=200, write_only=True)


class ClinicStatusSerializer(ClinicSessionSerializer):
    status = serializers.ChoiceField(choices=('scheduled', 'voicemail', 'unable'))
