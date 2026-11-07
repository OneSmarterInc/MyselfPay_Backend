"""Bounded process-local cache for published directory data only, never identities."""
from collections import OrderedDict
from copy import deepcopy
import hashlib
from pathlib import Path
import threading
import time

_values = OrderedDict()
_lock = threading.RLock()
TTL = 30
LIMIT = 8


def source_key(path):
    value = str(path)
    if value.startswith(('postgres://', 'postgresql://')):
        return hashlib.sha256(value.encode()).hexdigest()
    file = Path(value).resolve()
    stamps = []
    for candidate in (file, Path(str(file) + '-wal')):
        try:
            info = candidate.stat()
            stamps.append((info.st_ino, info.st_mtime_ns, info.st_size))
        except FileNotFoundError:
            stamps.append(None)
    return str(file), tuple(stamps)


def snapshot(kind, source, version, load):
    key = kind, source, version
    with _lock:
        now = time.monotonic()
        saved = _values.get(key)
        if saved is None or saved[0] <= now:
            # Serialize the first load so concurrent requests don't fetch the
            # entire directory at once. Failed loads are never cached.
            saved = now + TTL, load()
            _values[key] = saved
        _values.move_to_end(key)
        while len(_values) > LIMIT:
            _values.popitem(last=False)
        value = saved[1]
    # Search adds annotations. Never let one search mutate another's data.
    return deepcopy(value)


def clear():
    with _lock:
        _values.clear()
