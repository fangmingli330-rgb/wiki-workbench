"""Small standard-library file locks for local workspaces.

Locks are advisory and protect cooperating processes. Workspace directories must
be local, user-owned directories; network filesystem lock semantics vary.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import time


class LockBusy(TimeoutError):
    pass


def redirected(path):
    """Reject symlinks and Windows junction/reparse redirects alike."""
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


@contextmanager
def exclusive(path, timeout=None):
    path = Path(path)
    if redirected(path):
        raise ValueError('锁文件不能是符号链接')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    acquired = False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('锁文件格式无效')
        if os.fstat(fd).st_size == 0:
            os.write(fd, b'\0')
        started = time.monotonic()
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, PermissionError):
                if timeout is not None and time.monotonic() - started >= timeout:
                    raise LockBusy('已有任务正在处理') from None
                time.sleep(0.025)
        yield
    finally:
        if acquired:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
