"""Small adapter for the app's fixed SQL statements; values always remain parameters."""
from urllib.parse import urlparse, parse_qs
import atexit
import os
import threading

_pools = {}
_pool_lock = threading.Lock()


def close_pools():
    for (pid, _), pool in list(_pools.items()):
        if pid == os.getpid():
            pool.close()
    _pools.clear()


atexit.register(close_pools)


def connection_pool(url, mode):
    from psycopg_pool import ConnectionPool
    from psycopg.rows import dict_row
    key = os.getpid(), url
    with _pool_lock:
        if key not in _pools:
            size = max(1, min(32, int(os.getenv('SELFPAY_DB_POOL_SIZE', '4'))))
            _pools[key] = ConnectionPool(url, min_size=0, max_size=size, max_waiting=32,
                timeout=10, max_idle=60, max_lifetime=600, num_workers=1, open=True,
                check=ConnectionPool.check_connection,
                kwargs={'row_factory': dict_row, 'connect_timeout': 10, 'sslmode': mode, 'prepare_threshold': None})
        return _pools[key]

def uses_pooler(url):
    host=urlparse(url).hostname or ''
    return '-pooler.' in host or not host.endswith('neon.tech')
class PostgresConnection:
    def __init__(self,url):
        import psycopg
        from psycopg.rows import dict_row
        # Reuse bounded connections within each worker. Prepared statements stay
        # disabled for compatibility with upstream transaction poolers.
        parsed = urlparse(url)
        mode = parse_qs(parsed.query).get('sslmode', ['require'])[-1]
        # Explicit opt-out only for PostgreSQL on the same machine, never remote hosts.
        if mode in ('disable','allow','prefer') and parsed.hostname not in ('localhost','127.0.0.1','::1'):
            raise ValueError('Remote PostgreSQL connections require TLS.')
        if mode not in ('disable','require','verify-ca','verify-full'):
            raise ValueError('Use require/verify-full, or disable for loopback PostgreSQL only.')
        self._checkout = None
        if os.getenv('SELFPAY_DB_POOL_SIZE', '4') == '0':
            self.connection=psycopg.connect(url,row_factory=dict_row,connect_timeout=10,sslmode=mode,prepare_threshold=None)
        else:
            self._checkout = connection_pool(url, mode).connection()
            self.connection = self._checkout.__enter__()
    def __enter__(self):return self
    def __exit__(self,kind,value,tb):
        if self._checkout is not None:
            return self._checkout.__exit__(kind, value, tb)
        try:
            if kind:self.connection.rollback()
            else:self.connection.commit()
        finally:self.connection.close()
    def execute(self,sql,params=()):
        if sql.startswith('PRAGMA '):return None
        return self.connection.execute(sql.replace('?', '%s'),params)
    def executemany(self,sql,rows):
        with self.connection.cursor() as cursor:
            cursor.executemany(sql.replace('?', '%s'),rows)
    def executescript(self,script):
        script=script.replace('id INTEGER PRIMARY KEY','id BIGSERIAL PRIMARY KEY').replace(' REAL ', ' DOUBLE PRECISION ')
        for statement in script.split(';'):
            if statement.strip():self.connection.execute(statement)

