"""Django REST Framework resources over the existing SelfPay domain services."""
import hashlib
import secrets
import time
from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from api.authentication import AccountMemberOnly
from api import serializers as inputs
from api.throttles import GuestThrottle, BookingThrottle, BookingEmailThrottle, BookingCreateThrottle
import accounts
import booking
import core
import chat_questions
from search_results import apply_filters


def database():
    return settings.DOMAIN_DATABASE


def session_token(request):
    return request.COOKIES.get(settings.SESSION_COOKIE_NAME, '')


def client_ip(request):
    return request.META.get('REMOTE_ADDR', 'unknown')


def session_response(data, status=200, token=None):
    response = Response(data, status=status)
    if token is not None:
        if token:
            response.set_cookie(settings.SESSION_COOKIE_NAME, token, max_age=28800,
                                secure=not settings.DEBUG, httponly=True,
                                samesite=settings.SELFPAY_COOKIE_SAMESITE, path='/')
        else:
            response.delete_cookie(settings.SESSION_COOKIE_NAME, path='/', samesite=settings.SELFPAY_COOKIE_SAMESITE)
    return response


def session_details(member):
    return {'member': {key: value for key, value in member.items() if key != 'csrf'}, 'csrf': member['csrf']}


class ResourceView(APIView):
    serializer_class = inputs.ObjectSerializer

    def get_serializer(self, *args, **kwargs):
        return self.serializer_class(*args, **kwargs)

    def validated(self, request):
        # Read the body once before parsing, enforcing size even when a proxy
        # omits Content-Length. JSONParser handles malformed JSON/media types.
        if len(request.body) > settings.DATA_UPLOAD_MAX_MEMORY_SIZE:
            raise RequestDataTooBig()
        if not request.body:
            raise ValueError('Request must contain 1–100000 bytes.')
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class HealthView(ResourceView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            with core.connect(database()) as con:
                con.execute('SELECT member_id FROM user_accounts LIMIT 0')
                con.execute('SELECT id FROM booking_requests LIMIT 0')
                con.execute('SELECT request_id FROM booking_outbox LIMIT 0')
            return Response({'status': 'ok'})
        except Exception:
            return Response({'status': 'unavailable'}, status=503)


class DirectorySummaryView(ResourceView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(core.directory_summary(database()))


class AuthenticationView(ResourceView):
    permission_classes = [AllowAny]
    action = 'login'
    serializer_class = inputs.LoginSerializer

    def post(self, request):
        data = accounts.authenticate(database(), self.validated(request), self.action,
                                     client_ip(request), session_token(request))
        token = data.pop('token')
        return session_response(data, 201 if self.action == 'register' else 200, token)


class RegistrationView(AuthenticationView):
    action = 'register'
    serializer_class = inputs.RegistrationSerializer


class GuestView(ResourceView):
    permission_classes = [AllowAny]
    throttle_classes = [GuestThrottle]

    def post(self, request):
        self.validated(request)
        if request.auth and request.auth['tenant'] == 'guest':
            return Response(session_details(request.auth))
        data = core.guest_login(database())
        token = data.pop('token')
        return session_response(data, token=token)


class PasswordView(ResourceView):
    permission_classes = [AllowAny]

    def post(self, request):
        return Response({'error': 'Password recovery is not available yet.'}, status=410)


class MeView(ResourceView):
    def get(self, request):
        return Response(session_details(request.auth))


class LogoutView(ResourceView):
    def post(self, request):
        with core.connect(database()) as con:
            key = hashlib.sha256(session_token(request).encode()).hexdigest()
            con.execute('DELETE FROM guest_usage WHERE session_hash=?', (key,))
            con.execute('DELETE FROM sessions WHERE token_hash=?', (key,))
        return session_response({'ok': True}, token='')


class CatalogView(ResourceView):
    def get(self, request):
        return Response(core.catalog(database()))


class SearchView(ResourceView):
    serializer_class = inputs.LocationSearchSerializer

    def post(self, request):
        data = self.validated(request)
        return Response(apply_filters(core.search(data, database()), database(), data['sort']))


class GuestUsageView(ResourceView):
    def get(self, request):
        return Response({'usage': core.guest_usage(session_token(request), database())})


class AgentView(ResourceView):
    serializer_class = inputs.AgentSerializer

    def post(self, request):
        data = self.validated(request)
        question = data['message']
        search = chat_questions.preset_search(question)
        query = chat_questions.QUESTIONS[question]['query']
        if request.auth['tenant'] == 'guest':
            result = core.guest_agent(session_token(request), client_ip(request), query,
                            search, data.get('conversation_id'), (3, 5, 500, 30, 2000), database())
        else:
            result = core.agent(query, search, database())
        return Response(chat_questions.answer(apply_filters(result, database(), search['sort']), question))


class ConversationsView(ResourceView):
    serializer_class = inputs.ConversationSerializer

    def get_permissions(self):
        classes = [IsAuthenticated] if self.request.method in ('GET', 'HEAD', 'OPTIONS') else [IsAuthenticated, AccountMemberOnly]
        return [permission() for permission in classes]

    def get(self, request):
        return Response({'conversations': core.history(request.auth, database())})

    def post(self, request):
        return Response(core.save_conversation(request.auth, self.validated(request), database()))


class ConversationDetailView(ResourceView):
    permission_classes = [IsAuthenticated, AccountMemberOnly]

    def delete(self, request, ident):
        core.delete_conversation(request.auth, ident, database())
        return Response({'ok': True})


class PreferencesView(ResourceView):
    serializer_class = inputs.PreferenceSerializer

    def get_permissions(self):
        classes = [IsAuthenticated, AccountMemberOnly] if self.request.method == 'POST' else [IsAuthenticated]
        return [permission() for permission in classes]

    def get(self, request):
        return Response(core.preference(request.auth, path=database()))

    def post(self, request):
        data = self.validated(request)
        if data['private']:
            raise ValueError('Private preferences cannot be saved.')
        return Response(core.preference(request.auth, data, path=database()))

    def delete(self, request):
        return Response(core.preference(request.auth, delete=True, path=database()))


class CorrectionsView(ResourceView):
    permission_classes = [IsAuthenticated, AccountMemberOnly]
    serializer_class = inputs.CorrectionSerializer

    def post(self, request):
        data = self.validated(request)
        if data['private']:
            raise ValueError('Exit private mode before saving a correction report.')
        catalog = core.catalog(database())
        offer = next((o for o in catalog['offers'] if o['id'] == data['offer_id']), None)
        if not offer:
            raise ValueError('Offer unavailable.')
        ident = secrets.token_hex(12)
        with core.connect(database()) as con:
            con.execute('INSERT INTO corrections VALUES(?,?,?,?,?,?,?,?,?)',
                        (ident, request.auth['tenant'], request.auth['id'], offer['id'], catalog['version'],
                         data['category'], data['description'], time.time(), 'pending review'))
        return Response({'id': ident, 'status': 'pending review'}, status=201)


class BookingAccountOnly(AccountMemberOnly):
    message = 'Please log in or create a user account to book an appointment.'


class BookingView(ResourceView):
    throttle_classes = [BookingThrottle]
    serializers_by_action = {
        'options': inputs.BookingOptionsSerializer,
        'email-code': inputs.PatientEmailSerializer,
        'email-verify': inputs.PatientCodeSerializer,
        'create': inputs.BookingCreateSerializer,
        'patient': inputs.BookingCapabilitySerializer,
        'code': inputs.BookingCapabilitySerializer,
        'verify': inputs.ClinicCodeSerializer,
        'details': inputs.ClinicSessionSerializer,
        'status': inputs.ClinicStatusSerializer,
        'lock': inputs.ClinicSessionSerializer,
    }

    def get_permissions(self):
        authenticated_actions = ('options', 'email-code', 'email-verify', 'create')
        return [BookingAccountOnly() if self.kwargs['action'] in authenticated_actions else AllowAny()]

    def get_throttles(self):
        classes = [BookingThrottle]
        if self.kwargs['action'] == 'email-code':
            classes.append(BookingEmailThrottle)
        elif self.kwargs['action'] == 'create':
            classes.append(BookingCreateThrottle)
        return [throttle() for throttle in classes]

    def get_serializer(self, *args, **kwargs):
        return self.serializers_by_action[self.kwargs['action']](*args, **kwargs)

    def post(self, request, action):
        db, data = database(), self.validated(request)
        if action == 'options':
            result = booking.options(db, data['clinic_id'], data['offer_id'])
        elif action == 'email-code':
            result = booking.request_patient_code(db, request.auth['id'], data['email'])
        elif action == 'email-verify':
            result = booking.verify_patient_code(db, request.auth['id'], data['verification_id'], data['code'])
        elif action == 'create':
            result = booking.create(db, request.auth['id'], data, settings.FRONTEND_ORIGIN)
        elif action == 'patient':
            result = booking.patient(db, data['id'], data['token'])
        elif action == 'code':
            result = booking.issue_code(db, data['id'], data['token'])
        elif action == 'verify':
            result = booking.verify_code(db, data['id'], data['token'], data['code'])
        elif action in ('details', 'status'):
            result = booking.clinic_details(db, data['id'], data['session'], data.get('status'))
        else:
            result = booking.lock_session(db, data['id'], data['session'])
        return Response(result, status=201 if action == 'create' else 200)


class NotFoundView(ResourceView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, **kwargs):
        raise NotFound('Not found.')

    post = delete = put = patch = get
