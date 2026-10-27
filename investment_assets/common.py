"""Small filesystem/serialization boundaries independent of operating collectors."""
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re


class ArchiveError(ValueError):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def utcnow():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def timestamp(value):
    try:
        result = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(dt.timezone.utc)
    except (ValueError, TypeError):
        raise ArchiveError('timezone_aware_timestamp_required') from None


def latest_time(*values):
    present = [timestamp(v) for v in values if v is not None]
    return max(present).isoformat() if present else None


def safe_name(value):
    p = PurePosixPath(value)
    if not value or p.is_absolute() or '..' in p.parts or '\\' in value or str(p) != value:
        raise ArchiveError('unsafe_relative_path')
    return value


def hash_name(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ArchiveError('invalid_sha256')
    return value


def exclusive_write(path, raw):
    """Never replace a pre-existing byte, including corrupt existing objects."""
    path = Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)):raise ArchiveError('symlink_write_forbidden')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        path.chmod(0o444)
        fsync_dir(path.parent);fsync_dir(path.parent.parent)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != raw:
            raise ArchiveError('immutable_existing_bytes_differ') from None


def fsync_dir(path):
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def writer_lock(root):
    with (Path(root)/'writer.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
