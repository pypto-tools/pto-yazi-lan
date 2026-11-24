"""Copy explicit selections into the opposite pane's directory."""

import contextlib
import os
import re
import selectors
import shlex
import signal
import stat
import subprocess
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


class CopyCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel.is_set():
        raise CopyCancelled()


RSYNC_PROGRESS = re.compile(rb"^\s*([\d,]+)\s+(\d+)%\s+(\S+/s)(?:\s|$)")


def rsync_copy(session, push, paths, dest, update, report, cancel):
    # A NUL-separated files-from list avoids shell/glob expansion of either side's names.
    peer = session.peer.ssh
    target = dest.rstrip("/") + "/"
    ends = ["/", f"{peer}:{target}"] if push else [f"{peer}:/", target]
    args = ["rsync", "-rltps", "--from0", "--files-from=-", "--no-relative",
            "--partial-dir=.rsync-partial", "--info=progress2,name0", "--no-human-readable", "--outbuf=N",
            "-e", shlex.join(session.ssh)]
    if not update:
        args.append("--ignore-existing")
    with tempfile.TemporaryFile() as names:
        for path in paths:
            names.write(os.fsencode(path.lstrip("/")) + b"\0")
        names.seek(0)
        with subprocess.Popen([*args, "--", *ends], stdin=names, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, start_new_session=True,
                              env={**os.environ, "LC_ALL": "C"}) as proc:
            pending = b""
            diagnostic = ""
            try:
                with selectors.DefaultSelector() as poller:
                    poller.register(proc.stdout, selectors.EVENT_READ)
                    while True:
                        check_cancel(cancel)
                        if not poller.select(0.15):
                            continue
                        block = os.read(proc.stdout.fileno(), 16384)
                        lines = re.split(rb"[\r\n]", pending + block)
                        pending = lines.pop()[-8192:] if block else b""
                        for line in lines:
                            match = RSYNC_PROGRESS.match(line)
                            if match:
                                count, percent, speed = match.groups()
                                report(int(count.replace(b",", b"")), int(percent), speed.decode("ascii"), "")
                            elif line.strip():
                                diagnostic = line.decode(errors="replace")[-500:]
                        if not block:
                            break
                while proc.poll() is None:
                    if cancel.wait(0.15):
                        raise CopyCancelled()
                if proc.returncode:
                    raise RuntimeError(f"rsync 退出码 {proc.returncode}  {diagnostic}")
            finally:
                if proc.poll() is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(proc.pid, signal.SIGKILL)


def sftp_copy(session, push, paths, dest, update, report, cancel):
    sftp = session.sftp
    source, target = (os, sftp) if push else (sftp, os)

    def copy(path, to):
        check_cancel(cancel)
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
        last = time.monotonic()
        previous = 0
        report(0, 0, "—", os.path.basename(path))

        def progress(done, total):
            nonlocal last, previous
            check_cancel(cancel)
            now = time.monotonic()
            elapsed = now - last
            if elapsed >= 0.25 or done == total:
                rate = (done - previous) / max(elapsed, 0.000001)
                speed = f"{rate / 1048576:.1f} MiB/s" if rate >= 1048576 else f"{rate / 1024:.1f} KiB/s"
                report(done, 100 * done // max(1, total), speed, os.path.basename(path))
                last, previous = now, done

        try:
            if stat.S_ISLNK(mode):
                target.symlink(source.readlink(path), tmp)
            else:
                if push:
                    sftp.put(path, tmp, callback=progress)
                else:
                    sftp.get(path, tmp, callback=progress, max_concurrent_prefetch_requests=32)
                check_cancel(cancel)
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


def copy_selected(session, push, paths, dest, update, report, cancel):
    paths = selection(paths)
    if session.rsync:
        rsync_copy(session, push, paths, dest, update, report, cancel)
    else:
        sftp_copy(session, push, paths, dest, update, report, cancel)
