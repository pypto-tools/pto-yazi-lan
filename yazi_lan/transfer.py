"""Copy explicit selections into the opposite pane's directory."""

import contextlib
import os
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid


def selection(paths):
    paths = sorted(set(os.path.normpath(path) for path in paths))
    if not paths or any(not path.startswith("/") or path == "/" for path in paths):
        raise ValueError("请选择具体的文件或子目录")
    names = [os.path.basename(path) for path in paths]
    if len(names) != len(set(names)):
        raise ValueError("不同选中项的目标文件名相同，请分别复制到不同目录")
    return paths


def rsync_copy(session, push, paths, dest, update):
    # A NUL-separated files-from list avoids shell/glob expansion of either side's names.
    peer = session.peer.ssh
    target = dest.rstrip("/") + "/"
    ends = ["/", f"{peer}:{target}"] if push else [f"{peer}:/", target]
    args = ["rsync", "-rltps", "--from0", "--files-from=-", "--no-relative",
            "--partial-dir=.rsync-partial", "--info=progress2", "--human-readable",
            "-e", shlex.join(session.ssh)]
    if not update:
        args.append("--ignore-existing")
    with tempfile.TemporaryFile() as names:
        for path in paths:
            names.write(os.fsencode(path.lstrip("/")) + b"\0")
        names.seek(0)
        proc = subprocess.Popen([*args, "--", *ends], stdin=names, stderr=sys.stdout)
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
            proc.wait()
            raise
        if code:
            raise RuntimeError(f"rsync 退出码 {code}，可用同一选择和目标重试")


def sftp_copy(session, push, paths, dest, update):
    sftp = session.sftp
    source, target = (os, sftp) if push else (sftp, os)

    def copy(path, to):
        info = source.lstat(path)
        mode = info.st_mode
        try:
            existing = target.lstat(to)
        except FileNotFoundError:
            existing = None
        is_dir = stat.S_ISDIR(mode)
        if existing is not None and not (is_dir and stat.S_ISDIR(existing.st_mode)):
            if not update:
                return
            if is_dir or stat.S_ISDIR(existing.st_mode):
                raise ValueError(f"目录和文件同名，无法覆盖 {to!r}")
        if is_dir:
            if existing is None:
                target.mkdir(to, 0o700)
            for name in source.listdir(path):
                copy(os.path.join(path, name), os.path.join(to, name))
            if existing is None:
                target.chmod(to, mode & 0o777)
                target.utime(to, (info.st_atime, info.st_mtime))
            return

        if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
            raise ValueError(f"仅支持普通文件、目录和符号链接 {path!r}")
        tmp = os.path.join(os.path.dirname(to), ".yazi-lan-" + uuid.uuid4().hex)
        print(f"{path!r}", flush=True)
        last = 0.0

        def progress(done, total):
            nonlocal last
            now = time.monotonic()
            if now - last >= 0.25 or done == total:
                print(f"\r{done:,} / {total:,} bytes", end="", flush=True)
                last = now

        try:
            if stat.S_ISLNK(mode):
                target.symlink(source.readlink(path), tmp)
            else:
                if push:
                    sftp.put(path, tmp, callback=progress)
                else:
                    sftp.get(path, tmp, callback=progress, max_concurrent_prefetch_requests=32)
                print()
                target.chmod(tmp, mode & 0o777)
                target.utime(tmp, (info.st_atime, info.st_mtime))

            if push:
                if update and existing is not None:
                    sftp.posix_rename(tmp, to)
                else:
                    sftp.rename(tmp, to)  # SFTP v3 rename refuses an existing name.
            elif update:
                os.replace(tmp, to)
            else:
                os.link(tmp, to, follow_symlinks=False)
        finally:
            with contextlib.suppress(OSError, EOFError):
                target.unlink(tmp)

    for path in paths:
        copy(path, os.path.join(dest, os.path.basename(path)))


def copy_selected(session, push, paths, dest, update):
    paths = selection(paths)
    if session.rsync:
        rsync_copy(session, push, paths, dest, update)
    else:
        sftp_copy(session, push, paths, dest, update)
