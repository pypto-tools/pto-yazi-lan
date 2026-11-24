import os
import shlex
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from yazi_lan.connection import Session
from yazi_lan.jobs import CopyQueue, Job, transfer_session
from yazi_lan.preview import LIMIT, preview
from yazi_lan.transfer import CopyCancelled, copy_selected


class TransferTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ssh = self.root / "test-transport"
        self.session = Session(SimpleNamespace(ssh="peer"), "peer", "test", [str(self.ssh)], None, True)

    def transport(self, rsync):
        self.session.rsync = rsync
        if rsync:
            if not shutil.which("rsync"):
                self.skipTest("rsync unavailable")
            body = 'shift\nexec "$@"'
        else:
            server = next((p for p in ("/usr/lib/openssh/sftp-server", "/usr/libexec/openssh/sftp-server",
                                       "/usr/lib/ssh/sftp-server") if os.path.isfile(p)), None)
            if not server:
                self.skipTest("OpenSSH sftp-server unavailable")
            body = "exec " + shlex.quote(server)
        self.ssh.write_text("#!/bin/sh\n" + body + "\n")
        self.ssh.chmod(0o755)

    def roundtrip(self, rsync):
        self.transport(rsync)
        src = self.root / "训练 data"
        src.mkdir()
        data = os.urandom(135791)
        (src / "sample.bin").write_bytes(data)
        for push in (True, False):
            dest = self.root / ("upload" if push else "download")
            dest.mkdir()
            job = Job(push, [str(src)], str(dest), False)
            job.run(self.session)
            self.assertEqual((dest / src.name / "sample.bin").read_bytes(), data)
            count, pct, rate, _ = job.progress
            self.assertGreaterEqual(count, len(data))
            self.assertEqual(pct, 100)
            self.assertIn("/s", rate)
            src = dest / src.name

    def test_rsync_roundtrip(self):
        self.roundtrip(True)

    def test_rsync_cancel_without_waiting_for_progress_output(self):
        self.transport(True)
        marker = self.root / "started"
        self.ssh.write_text("#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\nexec sleep 15\n")
        source = self.root / "source"
        source.touch()
        job = Job(True, [str(source)], str(self.root), False)
        queue = CopyQueue()
        self.addCleanup(queue.close)
        queue.jobs.append(job)
        queue.poll(self.session)
        deadline = time.monotonic() + 4
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists())
        queue.cancel_all()
        with self.assertRaises(CopyCancelled):
            queue.future.result(timeout=5)
        self.assertEqual(queue.poll(self.session).status, "已取消")

    def test_sftp_roundtrip(self):
        self.roundtrip(False)

    def test_sftp_cancel_discards_partial_with_original_intact(self):
        self.transport(False)
        src = self.root / "large"
        src.write_bytes(os.urandom(256000))
        dest = self.root / "dest"
        dest.mkdir()
        old = dest / src.name
        old.write_bytes(b"old")
        cancel = threading.Event()
        def stop(*progress):
            cancel.set()
        with transfer_session(self.session) as session:
            with self.assertRaises(CopyCancelled):
                copy_selected(session, True, [str(src)], str(dest), True, stop, cancel)
        self.assertEqual(list(dest.iterdir()), [old])
        self.assertEqual(old.read_bytes(), b"old")

    def test_queue_cancels_waiting_without_starting_it(self):
        first, second = [Job(True, [name], "/", False) for name in ("/first","/second")]
        started = threading.Event()
        def worker(job, session):
            started.set()
            job.cancel.wait(3)
            raise CopyCancelled()
        queue = CopyQueue()
        self.addCleanup(queue.close)
        with patch.object(Job, "run", worker):
            queue.jobs.extend([first, second])
            queue.poll(None)
            self.assertTrue(started.wait(2))
            queue.cancel_all()
            deadline = time.monotonic() + 3
            while queue.current and time.monotonic() < deadline:
                queue.poll(None)
                time.sleep(0.01)
            self.assertIsNone(queue.current)
            self.assertEqual([j.status for j in queue.jobs], ["已取消", "已取消"])

    def test_preview_limit_binary_and_special_files(self):
        path = self.root / "preview"
        path.write_bytes(b"a" * LIMIT + b"not-read")
        self.assertEqual(preview(str(path)), ["a" * LIMIT])
        path.write_bytes(b"\x00\xff")
        self.assertIn("00 ff", preview(str(path))[1])
        link = self.root / "link"
        link.symlink_to(path)
        self.assertEqual(preview(str(link))[1], "→ " + str(path))
        pipe = self.root / "pipe"
        os.mkfifo(pipe)
        self.assertIn("特殊文件", preview(str(pipe)))

    def test_remote_preview_over_sftp(self):
        self.transport(False)
        path = self.root / "note"
        path.write_text("第一行\n第二行\n")
        with transfer_session(self.session) as session:
            self.assertEqual(preview(str(path), session.sftp), ["第一行", "第二行"])
