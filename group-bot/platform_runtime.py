"""Small native adapters for file locks, terminal input and worker lifetimes."""
from collections import deque
import os
import signal
import subprocess
import sys

if os.name == 'nt':
    import msvcrt
else:
    import fcntl
    import select
    import codecs
    class PosixConsole:
        LK_NBLCK, LK_LOCK, LK_UNLCK = 1, 2, 3
        def __init__(self): self.pending=deque();self.decoder=codecs.getincrementaldecoder('utf-8')('replace')
        def read_char(self):
            while True:
                raw=os.read(sys.stdin.fileno(),1)
                if not raw:return ''
                value=self.decoder.decode(raw)
                if value:return value
        def locking(self,fd,mode,count):
            flags=fcntl.LOCK_UN if mode==self.LK_UNLCK else fcntl.LOCK_EX
            if mode==self.LK_NBLCK:flags|=fcntl.LOCK_NB
            fcntl.flock(fd,flags)
        def kbhit(self):return bool(self.pending or select.select([sys.stdin],[],[],0)[0])
        def getwch(self):
            if self.pending:return self.pending.popleft()
            value=self.read_char()
            if value=='\x1b' and select.select([sys.stdin],[],[],.03)[0]:
                sequence=self.read_char()
                if sequence=='[':
                    sequence+=self.read_char()
                    if sequence[-1].isdigit():sequence+=self.read_char()
                code={'[A':'H','[B':'P','[C':'M','[D':'K','[5~':'I','[6~':'Q'}.get(sequence)
                if code:self.pending.append(code);return '\xe0'
            return {'\n':'\r','\x7f':'\b'}.get(value,value)
    msvcrt=PosixConsole()


def process_options():
    return {'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}


def enable_terminal():
    """Return a restore function; non-interactive WebUI sessions need no raw mode."""
    if os.name=='nt':
        import ctypes
        api=ctypes.WinDLL('kernel32');api.GetStdHandle.restype=ctypes.c_void_p
        handle=api.GetStdHandle(-11);mode=ctypes.c_uint32()
        api.GetConsoleMode.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
        api.SetConsoleMode.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
        api.GetConsoleMode(handle,ctypes.byref(mode));api.SetConsoleMode(handle,mode.value|4)
        return lambda:api.SetConsoleMode(handle,mode.value)
    if not sys.stdin.isatty():return lambda:None
    import termios
    import tty
    fd=sys.stdin.fileno();previous=termios.tcgetattr(fd);tty.setcbreak(fd)
    return lambda:termios.tcsetattr(fd,termios.TCSADRAIN,previous)


class PosixJob:
    def __init__(self):self.pid=None
    def add(self,process):self.pid=process.pid
    def close(self):
        if self.pid:
            try:os.killpg(self.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            self.pid=None


def kill_process_group(process):
    if os.name!='nt':
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass

