"""Atomic snapshots that tolerate Windows readers briefly holding a file open."""
import os
import errno
from pathlib import Path
import tempfile
import time


def file_busy(error):
    # WinError 5 can also be a persistent ACL failure. Retrying is bounded;
    # callers still receive the original error if access never recovers.
    return isinstance(error, OSError) and (getattr(error, 'winerror', None) in {5, 32, 33}
        or (os.name == 'nt' and isinstance(error, PermissionError)
            and error.errno in {errno.EACCES, errno.EPERM}))


def replace_with_retry(source, destination):
    for attempt in range(7):
        try:
            Path(source).replace(destination)
            return
        except OSError as exc:
            if not file_busy(exc) or attempt == 6:
                raise
            time.sleep(min(.01 * 2 ** attempt, .2))


def read_text(path, encoding='utf-8'):
    for attempt in range(7):
        try:
            return Path(path).read_text(encoding=encoding)
        except OSError as exc:
            if not file_busy(exc) or attempt == 6:
                raise
            time.sleep(min(.01 * 2 ** attempt, .2))


def atomic_write_text(path, text, encoding='utf-8'):
    """Write privately, then replace; readers see the old or the complete new file."""
    path = Path(path)
    # Unique siblings avoid collisions with concurrent snapshots or old .tmp files.
    fd, name = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding=encoding, newline='') as stream:
            stream.write(text)
        replace_with_retry(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # An antivirus may briefly hold the temporary file too. Do not mask
            # the original write error (or report failure after a successful write).
            pass
