"""Serial copy queue with an independent SSH channel for background SFTP."""
import contextlib
import os
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from threading import Event

import paramiko

from .connection import SftpChannel
from .transfer import CopyCancelled, copy_selected


@contextlib.contextmanager
def transfer_session(session):
    if session.rsync:
        yield session
        return
    # The browser owns its SFTP request stream. Multiplex a second subsystem
    # on the authenticated OpenSSH connection for the worker's requests.
    client, child = socket.socketpair()
    with client, child:
        client.settimeout(30)
        with subprocess.Popen([*session.ssh, "-s", "--", session.peer.ssh, "sftp"],
                              stdin=child, stdout=child, stderr=subprocess.DEVNULL,
                              start_new_session=True) as proc:
            child.close()
            try:
                with paramiko.SFTPClient(SftpChannel(client)) as sftp:
                    yield replace(session, sftp=sftp)
            finally:
                client.close()
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()


@dataclass
class Job:
    push: bool
    paths: list
    dest: str
    update: bool
    status: str = "等待"
    progress: tuple = (0, None, "—", "")
    error: str = ""
    cancel: Event = field(default_factory=Event)

    @property
    def name(self):
        first = os.path.basename(self.paths[0])
        return first if len(self.paths) == 1 else f"{first} 等 {len(self.paths)} 项"

    def run(self, session):
        def report(count, percent, rate, name):
            self.progress = (count, percent, rate, name)

        with transfer_session(session) as channel:
            copy_selected(channel, self.push, self.paths, self.dest, self.update, report, self.cancel)


class CopyQueue:
    def __init__(self):
        self.jobs = []
        self.current = None
        self.future = None
        self.pool = None

    def poll(self, session):
        finished = None
        if self.current and self.future.done():
            finished = self.current
            try:
                self.future.result()
                finished.status = "完成"
            except CopyCancelled:
                finished.status = "已取消"
            except Exception as exc:
                finished.status = "失败"
                finished.error = str(exc) or type(exc).__name__
            self.current = self.future = None
        if self.current is None:
            for job in self.jobs:
                if job.status == "等待":
                    if self.pool is None:
                        self.pool = ThreadPoolExecutor(max_workers=1)
                    job.status = "传输中"
                    self.current = job
                    self.future = self.pool.submit(job.run, session)
                    break
        return finished

    def cancel_all(self):
        skipped = []
        for job in self.jobs:
            if job is self.current:
                job.cancel.set()
                job.status = "取消中"
            elif job.status == "等待":
                job.status = "已取消"
                skipped.append(job)
        return skipped

    def close(self):
        self.cancel_all()
        if self.pool:
            self.pool.shutdown(wait=True)
            self.pool = None
