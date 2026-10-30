"""Small adapter for the app's fixed SQL statements; values always remain parameters."""
from urllib.parse import urlparse, parse_qs

def uses_pooler(url):
    host=urlparse(url).hostname or ''
    return '-pooler.' in host or not host.endswith('neon.tech')
class PostgresConnection:
    def __init__(self,url):
        import psycopg
        from psycopg.rows import dict_row
        # Serverless functions open a connection per call, so DATABASE_URL must be Neon's pooled (-pooler)
        # endpoint. Server-side prepared statements are disabled because a transaction pooler can hand the
        # next statement to a different backend connection.
        parsed = urlparse(url)
        mode = parse_qs(parsed.query).get('sslmode', ['require'])[-1]
        # Explicit opt-out only for PostgreSQL on the same machine, never remote hosts.
        if mode in ('disable','allow','prefer') and parsed.hostname not in ('localhost','127.0.0.1','::1'):
            raise ValueError('Remote PostgreSQL connections require TLS.')
        if mode not in ('disable','require','verify-ca','verify-full'):
            raise ValueError('Use require/verify-full, or disable for loopback PostgreSQL only.')
        self.connection=psycopg.connect(url,row_factory=dict_row,connect_timeout=10,sslmode=mode,prepare_threshold=None)
    def __enter__(self):return self
    def __exit__(self,kind,value,tb):
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

