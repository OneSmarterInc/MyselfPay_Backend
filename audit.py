"""Append-only audit trail kept in its own database.

Every row stores the hash of the row before it, so an edit or a deletion breaks the chain.
Secrets never enter the log: no login codes, session tokens or passwords.
"""
import contextvars
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

import booking
import core

logger = logging.getLogger(__name__)
GENESIS = 'GENESIS'
LOCK_KEY = 7201
FIELDS = ('seq', 'ts', 'request_id', 'actor_type', 'actor', 'scope', 'action', 'target_type', 'target_id',
          'result', 'old_value', 'new_value', 'detail', 'ip', 'user_agent')
_context = contextvars.ContextVar('audit_context', default=None)
_health = {'at': 0.0, 'ok': False, 'why': 'Not checked yet.'}
_health_lock = threading.Lock()

SQLITE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS audit_log(
    seq INTEGER PRIMARY KEY, ts REAL NOT NULL, request_id TEXT NOT NULL, actor_type TEXT NOT NULL, actor TEXT NOT NULL,
    scope TEXT NOT NULL, action TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL, result TEXT NOT NULL,
    old_value TEXT NOT NULL, new_value TEXT NOT NULL, detail TEXT NOT NULL, ip TEXT NOT NULL, user_agent TEXT NOT NULL,
    prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS audit_scope ON audit_log(scope, ts);
CREATE INDEX IF NOT EXISTS audit_actor ON audit_log(actor, ts);
CREATE INDEX IF NOT EXISTS audit_action ON audit_log(action, ts);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
'''


class AuditUnavailable(Exception):
    """Raised only in enforce mode, so a change never happens without its record."""


def url():
    return os.environ.get('AUDIT_DATABASE_URL', '').strip()


def mode():
    return 'enforce' if os.environ.get('AUDIT_MODE', '').strip().lower() == 'enforce' else 'observe'


def initialize(path):
    """Creates the schema in a local SQLite file. Hosted PostgreSQL is set up by hand: ops/audit-store.sql."""
    if str(path).startswith(('postgres://', 'postgresql://')):
        return
    con = sqlite3.connect(path)
    try:
        con.executescript(SQLITE_SCHEMA)
        con.commit()
    finally:
        con.close()


# ---- who and where -------------------------------------------------------------------------------------------

def client_ip(request):
    """The visitor's address. AUDIT_TRUSTED_PROXIES counts the proxies in front of the app; 0 trusts none."""
    remote = request.META.get('REMOTE_ADDR', '') or 'unknown'
    try:
        count = max(0, min(5, int(os.environ.get('AUDIT_TRUSTED_PROXIES', '0'))))
    except ValueError:
        count = 0
    parts = [p.strip() for p in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',') if p.strip()]
    if count and len(parts) >= count:
        return parts[-count][:64]
    return remote[:64]


def bind(request, actor_type='', actor=''):
    """Remember who is acting for the rest of this request."""
    context = {'request_id': uuid.uuid4().hex, 'ip': client_ip(request),
               'user_agent': request.META.get('HTTP_USER_AGENT', '')[:300],
               'remote_addr': request.META.get('REMOTE_ADDR', '')[:64],
               'forwarded': request.META.get('HTTP_X_FORWARDED_FOR', '')[:200],
               'actor_type': actor_type, 'actor': actor}
    _context.set(context)
    return context


def unbind():
    _context.set(None)


def set_actor(actor_type, actor):
    context = _context.get()
    if context is not None:
        context['actor_type'], context['actor'] = actor_type, actor


# ---- writing -------------------------------------------------------------------------------------------------

def clip(value, size=1000):
    text = '' if value is None else str(value)
    return text if len(text) <= size else text[:size - 1] + '…'


def digest_row(prev, row):
    body = json.dumps({key: row[key] for key in FIELDS}, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256((prev + '|' + body).encode()).hexdigest()


def log(action, *, scope='', target_type='', target_id='', old='', new='', result='ok', actor=None,
        actor_type=None, detail=None):
    """Append one event. In observe mode a failure is reported, never raised. In enforce mode it is raised."""
    target = url()
    if not target:
        if mode() == 'enforce':
            raise AuditUnavailable('The audit store is not configured.')
        return None
    context = _context.get() or {}
    extra = dict(detail or {})
    if context.get('remote_addr') is not None:
        extra.setdefault('remote_addr', context.get('remote_addr'))
        if context.get('forwarded'):
            extra.setdefault('forwarded_for', context.get('forwarded'))
    row = {'ts': round(time.time(), 6), 'request_id': context.get('request_id') or uuid.uuid4().hex,
           'actor_type': clip(actor_type if actor_type is not None else context.get('actor_type') or 'system', 40),
           'actor': clip(actor if actor is not None else context.get('actor') or '', 200),
           'scope': clip(scope, 200), 'action': clip(action, 80), 'target_type': clip(target_type, 60),
           'target_id': clip(target_id, 200), 'result': clip(result, 40), 'old_value': clip(old), 'new_value': clip(new),
           'detail': clip(json.dumps(extra, sort_keys=True, default=str), 4000), 'ip': clip(context.get('ip', ''), 64),
           'user_agent': clip(context.get('user_agent', ''), 300)}
    try:
        with booking.connection(target, write=True) as db:
            if not isinstance(db, sqlite3.Connection):
                db.execute('SELECT pg_advisory_xact_lock(?)', (LOCK_KEY,))
            last = db.execute('SELECT seq,row_hash FROM audit_log ORDER BY seq DESC LIMIT 1').fetchone()
            row['seq'] = (last['seq'] + 1) if last else 1
            prev = last['row_hash'] if last else GENESIS
            row_hash = digest_row(prev, row)
            db.execute('INSERT INTO audit_log(' + ','.join(FIELDS) + ',prev_hash,row_hash) VALUES(' + ','.join('?' * (len(FIELDS) + 2)) + ')',
                       tuple(row[key] for key in FIELDS) + (prev, row_hash))
        return row['seq']
    except Exception as exc:
        logger.error('AUDIT WRITE FAILED for %s: %s', action, type(exc).__name__)
        if mode() == 'enforce':
            raise AuditUnavailable('The audit store is not available.') from exc
        return None


# ---- reading -------------------------------------------------------------------------------------------------

def search(*, actor='', action='', scope='', since=0.0, until=0.0, text='', limit=100, offset=0):
    target = url()
    if not target:
        return []
    clauses, params = [], []
    for column, value, like in (('actor', actor, True), ('action', action, True), ('scope', scope, False)):
        if value:
            clauses.append(f'{column} LIKE ?' if like else f'{column}=?')
            params.append(f'%{value}%' if like else value)
    if since:
        clauses.append('ts>=?')
        params.append(since)
    if until:
        clauses.append('ts<?')
        params.append(until)
    if text:
        clauses.append('(old_value LIKE ? OR new_value LIKE ? OR target_id LIKE ? OR ip LIKE ?)')
        params += [f'%{text}%'] * 4
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    with core.connect(target) as db:
        rows = db.execute(f'SELECT * FROM audit_log {where} ORDER BY seq DESC LIMIT ? OFFSET ?',
                          tuple(params) + (max(1, min(int(limit), 5000)), max(0, int(offset)))).fetchall()
    return [dict(r) for r in rows]


LABELS = {
    'center.code': 'Login code requested', 'center.verify': 'Signed in', 'center.logout': 'Signed out',
    'center.dashboard': 'Opened the portal', 'center.price': 'Prices sent to publish',
    'center.confirm-price': 'Price confirmed', 'center.profile': 'Change sent to publish',
    'center.contact': 'Email or phone change started', 'center.contact-verify': 'Email or phone change confirmed',
    'center.request': 'Appointment request updated',
    'admin.price.approve': 'Price approved by MySelfPay', 'admin.price.reject': 'Price not accepted by MySelfPay',
    'admin.profile.approve': 'Change approved by MySelfPay', 'admin.profile.reject': 'Change not accepted by MySelfPay',
}


def for_center(keys, emails, limit=200):
    """What a center may see about itself: no IP address, no browser, no staff names."""
    target = url()
    keys, emails = sorted(set(keys)), sorted(set(emails))
    if not target or not keys:
        return []
    marks_k, marks_e = ','.join('?' * len(keys)), ','.join('?' * len(emails)) if emails else "''"
    with core.connect(target) as db:
        rows = db.execute(
            f"SELECT ts,action,actor_type,target_id,result,old_value,new_value FROM audit_log WHERE "
            f"(scope IN ({marks_k}) AND result='ok') OR (actor_type='center' AND actor IN ({marks_e}) AND scope='') "
            f"ORDER BY seq DESC LIMIT ?", tuple(keys) + tuple(emails) + (limit,)).fetchall()
    return [{'when': r['ts'], 'what': LABELS.get(r['action'], r['action']), 'by': 'MySelfPay review' if r['actor_type'] == 'admin' else 'You',
             'result': r['result'], 'old': r['old_value'], 'new': r['new_value']} for r in rows]


def verify_chain(path=None):
    """Recompute every hash. Returns (rows checked, first bad seq or None, reason)."""
    target = path or url()
    prev, count = GENESIS, 0
    with core.connect(target) as db:
        for row in db.execute('SELECT * FROM audit_log ORDER BY seq').fetchall():
            count += 1
            if row['seq'] != count:
                return count, row['seq'], 'A row is missing before this one.'
            if row['prev_hash'] != prev:
                return count, row['seq'], 'The link to the previous row is broken.'
            if digest_row(prev, row) != row['row_hash']:
                return count, row['seq'], 'The row content does not match its hash.'
            prev = row['row_hash']
    return count, None, ''


def anchor(path=None):
    """A small signed record of where the chain stands. Copy it somewhere the server cannot change."""
    target = path or url()
    with core.connect(target) as db:
        last = db.execute('SELECT seq,row_hash,ts FROM audit_log ORDER BY seq DESC LIMIT 1').fetchone()
    body = {'generated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'last_seq': last['seq'] if last else 0,
            'last_hash': last['row_hash'] if last else GENESIS, 'last_ts': last['ts'] if last else 0}
    key = os.environ.get('AUDIT_ANCHOR_KEY', '')
    body['signature'] = hmac.new(key.encode(), json.dumps(body, sort_keys=True).encode(), 'sha256').hexdigest() if key else ''
    return body


def healthy():
    """True when the audit store answers. Cached for one minute."""
    now = time.time()
    with _health_lock:
        if now - _health['at'] < 60:
            return _health['ok'], _health['why']
    ok, why = False, 'The audit store is not configured (AUDIT_DATABASE_URL).'
    if url():
        try:
            with core.connect(url()) as db:
                db.execute('SELECT seq FROM audit_log LIMIT 1').fetchall()
            ok, why = True, ''
        except Exception as exc:
            why = 'The audit store does not answer: ' + type(exc).__name__
    with _health_lock:
        _health.update(at=now, ok=ok, why=why)
    return ok, why
