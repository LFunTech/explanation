import os


def acquire(path):
    handle=path.open('a+b')
    if handle.tell()==0:
        handle.write(b'0');handle.flush()
    handle.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except (OSError,IOError):
        handle.close()
        raise RuntimeError('此数据目录已有服务实例运行；请使用单进程单副本。')
    return handle
