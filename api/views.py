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
import mobile_accounts
import booking
import core
import chat_questions
from search_results import apply_filters


def database():
    return settings.DOMAIN_DATABASE


def session_token(request):
    return request.COOKIES.get(settings.SELFPAY_SESSION_COOKIE_NAME, '')


def client_ip(request):
    return request.META.get('REMOTE_ADDR', 'unknown')


def session_response(data, status=200, token=None):
    response = Response(data, status=status)
    if token is not None:
        if token:
            response.set_cookie(settings.SELFPAY_SESSION_COOKIE_NAME, token, max_age=28800,
                                secure=not settings.DEBUG, httponly=True,
                                samesite=settings.SELFPAY_COOKIE_SAMESITE, path='/')
        else:
            response.delete_cookie(settings.SELFPAY_SESSION_COOKIE_NAME, path='/', samesite=settings.SELFPAY_COOKIE_SAMESITE)
    return response


def session_details(member):
    return {'member': {key: value for key, value in member.items() if key not in ('csrf', 'email')}, 'csrf': member['csrf']}


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
                con.execute('SELECT id FROM spatial_locations LIMIT 0')
                con.execute('SELECT member_id FROM account_zip_codes LIMIT 0')
                con.execute('SELECT request_id FROM booking_outbox LIMIT 0')
                con.execute('SELECT id FROM booking_phone_checks LIMIT 0')
                con.execute('SELECT member_id FROM mobile_accounts LIMIT 0')
            return Response({'status': 'ok'})
        except Exception:
            return Response({'status': 'unavailable'}, status=503)


class CenterPortalView(ResourceView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get_serializer(self, *args, **kwargs):
        serializer = {'code': inputs.CenterEmailSerializer, 'verify': inputs.CenterVerifySerializer,
                      'request': inputs.CenterRequestSerializer}.get(self.kwargs['action'], inputs.ObjectSerializer)
        return serializer(*args, **kwargs)

    def cookie_name(self):
        return 'selfpay_center' if settings.DEBUG else '__Host-selfpay_center'

    def get(self, request, action):
        import center_portal
        if action != 'dashboard':
            raise NotFound()
        return Response(center_portal.dashboard(database(), request.COOKIES.get(self.cookie_name(), '')))

    def post(self, request, action):
        import center_portal
        data = self.validated(request)
        token = request.COOKIES.get(self.cookie_name(), '')
        csrf = request.headers.get('X-Center-CSRF', '')
        if action == 'code':
            return Response(center_portal.issue(database(), data.get('email', '')))
        if action == 'verify':
            token, csrf = center_portal.verify(database(), str(data.get('verification_id', '')), data.get('code', ''))
            response = Response({'verified': True})
            response.set_cookie(self.cookie_name(), token, max_age=28800, secure=not settings.DEBUG,
                                httponly=True, samesite=settings.SELFPAY_COOKIE_SAMESITE, path='/')
            return response
        if action == 'request':
            return Response(center_portal.open_request(database(), token, csrf, str(data.get('id', '')), data.get('status')))
        if action == 'logout':
            with booking.connection(database(), write=True) as db:
                center_portal.identity(db, token, csrf)
                db.execute('DELETE FROM center_portal_sessions WHERE token_hash=?', (booking.digest(token),))
            response = Response({'ok': True})
            response.delete_cookie(self.cookie_name(), path='/', samesite=settings.SELFPAY_COOKIE_SAMESITE)
            return response
        raise NotFound()


class DirectorySummaryView(ResourceView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        data = core.catalog_metadata(database(), include_locations=False)
        return Response({'listing_count': data['entry_count'], 'state_count': data['state_count'], 'spatial_api_version': 1, 'spatial_ready': data['spatial_ready'], 'account_zip_enabled': True})


class AuthenticationView(ResourceView):
    permission_classes = [AllowAny]
    action = 'login'
    serializer_class = inputs.LoginSerializer

    def post(self, request):
        data = mobile_accounts.authenticate(database(), self.validated(request), self.action,
                                     client_ip(request), session_token(request), request.auth)
        token = data.pop('token')
        return session_response(data, 201 if self.action == 'register' else 200, token)


class RegistrationView(AuthenticationView):
    action = 'register'
    serializer_class = inputs.RegistrationSerializer


class AccountSetupView(AuthenticationView):
    action = 'setup'
    serializer_class = inputs.RegistrationSerializer


class RegistrationMobileView(ResourceView):
    permission_classes = [AllowAny]
    # Match the existing OTP policy: no application-level send/check quotas.
    throttle_classes = []

    def post(self, request, action):
        data = self.validated(request)
        result = (mobile_accounts.request_code(database(), data, client_ip(request)) if action == 'code'
                  else mobile_accounts.verify_code(database(), data))
        return Response(result)

    def validated(self, request):
        if len(request.body) > 4096 or not isinstance(request.data, dict):
            raise ValueError('Invalid verification request.')
        return request.data


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
        member = dict(request.auth)
        if member['tenant'] != 'guest':
            with core.connect(database()) as db:
                member['zip_code'] = accounts.saved_zip(db, member['id'])
        return Response(session_details(member))


class LogoutView(ResourceView):
    def post(self, request):
        with core.connect(database()) as con:
            key = hashlib.sha256(session_token(request).encode()).hexdigest()
            con.execute('DELETE FROM guest_usage WHERE session_hash=?', (key,))
            con.execute('DELETE FROM sessions WHERE token_hash=?', (key,))
        return session_response({'ok': True}, token='')


class CatalogView(ResourceView):
    def get(self, request):
        if request.query_params.get('metadata') == '1':
            data = core.catalog_metadata(database())
            return Response({key: data[key] for key in ('version', 'entry_count', 'offer_count', 'locations')} | {'entries': [], 'offers': [], 'metadata_only': True})
        return Response(core.catalog(database()))


class SearchView(ResourceView):
    serializer_class = inputs.LocationSearchSerializer

    def post(self, request):
        data = self.validated(request)
        result = core.search({**data, 'body': '', 'scope': 'state'}, database())
        result['search'].update(body=data['body'], scope=data['scope'], radius=data['radius'])
        return Response(apply_filters(result, database(), data['sort'], data['scope'], informational_body=True, statewide=True))


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
        'phone-code': inputs.PatientPhoneSerializer,
        'phone-verify': inputs.PatientCodeSerializer,
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
        authenticated_actions = ('options', 'email-code', 'email-verify', 'phone-code', 'phone-verify', 'create')
        return [BookingAccountOnly() if self.kwargs['action'] in authenticated_actions else AllowAny()]

    def get_throttles(self):
        if self.kwargs['action'] in ('email-code', 'email-verify', 'phone-code', 'phone-verify'):
            return []
        classes = [BookingThrottle]
        if self.kwargs['action'] == 'create':
            classes.append(BookingCreateThrottle)
        return [throttle() for throttle in classes]

    def get_serializer(self, *args, **kwargs):
        return self.serializers_by_action[self.kwargs['action']](*args, **kwargs)

    def post(self, request, action):
        db, data = database(), self.validated(request)
        if action == 'options':
            result = booking.options(db, data['clinic_id'], data['offer_id'])
            with core.connect(db) as con:
                result['phone'] = mobile_accounts.booking_phone_for(con, request.auth['id'])
        elif action in ('email-code', 'email-verify'):
            return Response({'error':'Patient email verification has been retired. Use mobile verification at registration.'}, status=410)
        elif action == 'phone-code':
            import booking_phone
            result = booking_phone.request_code(db, request.auth['id'], data['phone'])
        elif action == 'phone-verify':
            import booking_phone
            result = booking_phone.verify_code(db, request.auth['id'], data['verification_id'], data['code'])
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


class DashboardView(ResourceView):
    permission_classes = [AccountMemberOnly]

    def get(self, request):
        try:
            page = max(1, int(request.query_params.get('page', '1')))
        except ValueError:
            return Response({'error':'Invalid page.'}, status=400)
        mode = request.query_params.get('filter', 'all')
        if mode not in ('all', 'open', 'approved'):
            return Response({'error':'Invalid request filter.'}, status=400)
        owner, now = booking.digest(request.auth['id']), time.time()
        with core.connect(database()) as con:
            profile = con.execute('SELECT m.id,m.name,a.phone,a.created FROM members m JOIN user_accounts a ON a.member_id=m.id WHERE m.id=?', (request.auth['id'],)).fetchone()
            if not profile:
                return Response({'error':'Account profile unavailable.'}, status=404)
            stats = {row['status']:row['n'] for row in con.execute("""SELECT CASE WHEN status IN ('requested','voicemail') AND link_expires<=? THEN 'expired' ELSE status END AS status,COUNT(*) AS n FROM booking_requests WHERE owner_hash=? GROUP BY 1""", (now,owner)).fetchall()}
            where, params = 'owner_hash=?', [owner]
            if mode == 'open':
                where += " AND status IN ('requested','voicemail') AND link_expires>?"
                params.append(now)
            elif mode == 'approved':
                where += " AND status='scheduled'"
            total = con.execute('SELECT COUNT(*) AS n FROM booking_requests WHERE '+where, tuple(params)).fetchone()['n']
            pages = max(1, (total+9)//10)
            page = min(page,pages)
            rows = con.execute('SELECT * FROM booking_requests WHERE '+where+' ORDER BY created DESC,id DESC LIMIT ? OFFSET ?', tuple(params+[10,(page-1)*10])).fetchall()
            requests = []
            for row in rows:
                item = booking.receipt(row,con)
                payload = booking.unpack(row)
                item.update(contact_name=payload['first_name'],contact_phone=payload.get('phone',''),consent=payload.get('consent',False),approved_at=row['closed'] if row['status']=='scheduled' else None)
                if item['status'] in ('requested','voicemail') and row['link_expires']<=now:
                    item['status']='expired'
                requests.append(item)
            account = mobile_accounts.profile(con, profile)
            account['zip_code'] = accounts.saved_zip(con,request.auth['id'])
        return Response({'profile':account,
                         'requests':requests,'stats':stats,'total':total,'page':page,'pages':pages})


class NotFoundView(ResourceView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, **kwargs):
        raise NotFound('Not found.')

    post = delete = put = patch = get
