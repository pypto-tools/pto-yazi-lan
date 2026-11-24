import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from yazi_lan.connection import Session
from yazi_lan.jobs import Job
from yazi_lan.parallel import copy_lan
from yazi_lan.transfer import CopyCancelled, rsync_copy


@unittest.skipUnless(shutil.which("rsync"), "requires rsync")
class ParallelCopyTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        shim = self.root / "ssh"
        shim.write_text('#!/bin/sh\nshift\nexec "$@"\n')
        shim.chmod(0o755)
        self.session = Session(SimpleNamespace(ssh="peer"), "peer", "LAN", [str(shim)], None, True, lan_jobs=2)
        self.src = self.root / "sets" / "训练"
        self.src.mkdir(parents=True)
        self.dest = self.root / "dest"
        self.dest.mkdir()
        self.key = "tab\t newline\n \\#012"
        for name in ("alpha", "beta", "中文", self.key):
            (self.src / name).write_bytes((name.encode() + b"data") * 506)
        (self.src / "nested").mkdir()
        (self.src / "nested/alpha").write_bytes(b"nested")
        (self.src / "empty").mkdir()
        (self.src / "link").symlink_to("nested/alpha")
        os.utime(self.src, (1000000000, 1000000000))

    def copy(self, push, update=False):
        job = Job(push, [str(self.src)], str(self.dest), update)
        job.run(self.session)
        return job

    def check_copy(self, push):
        gate = threading.Barrier(2)
        threads = set()
        lock = threading.Lock()
        def watch(*args, options=(), **kwargs):
            if "--relative" in options:
                with lock:
                    first = threading.get_ident() not in threads
                    threads.add(threading.get_ident())
                if first:
                    gate.wait(5)
            return rsync_copy(*args, options=options, **kwargs)
        with patch("yazi_lan.parallel.rsync_copy", watch):
            job = self.copy(push)
        self.assertEqual(len(threads), 2)
        self.assertEqual(job.progress[1], 100)
        tree = self.dest / self.src.name
        self.assertEqual((tree / self.key).read_bytes(), (self.src / self.key).read_bytes())
        self.assertEqual((tree / "中文").read_bytes(), (self.src / "中文").read_bytes())
        self.assertEqual((tree / "nested/alpha").read_bytes(), b"nested")
        self.assertTrue((tree / "empty").is_dir())
        self.assertEqual(os.readlink(tree / "link"), "nested/alpha")
        self.assertEqual(int(tree.stat().st_mtime), int(self.src.stat().st_mtime))

    def test_upload(self):
        self.check_copy(True)

    def test_download(self):
        self.check_copy(False)

    def test_multiple_large_files_from_different_directories(self):
        paths = []
        for index in range(3):
            parent = self.root / str(index)
            parent.mkdir()
            path = parent / f"checkpoint-{index}"
            path.write_bytes(bytes([index + 1]) * (8 * 1024 * 1024 + index))
            paths.append(str(path))
        job = Job(True, paths, str(self.dest), False)
        with patch("yazi_lan.parallel.rsync_copy", wraps=rsync_copy) as runner:
            job.run(self.session)
        batches = [call.args[2] for call in runner.call_args_list if "--relative" in call.kwargs.get("options", ())]
        self.assertEqual(sorted(map(len, batches)), [1, 1, 1])
        self.assertGreater(job.elapsed, 0)
        self.assertEqual(job.progress[0], sum(os.stat(path).st_size for path in paths))
        for path in map(Path, paths):
            self.assertEqual(path.read_bytes(), (self.dest / path.name).read_bytes())

    def test_skip_and_update(self):
        tree = self.dest / self.src.name
        tree.mkdir()
        (tree / "alpha").write_bytes(b"old")
        (tree / "dest-only").write_bytes(b"keep")
        self.copy(True)
        self.assertEqual((tree / "alpha").read_bytes(), b"old")
        (tree / "beta").write_bytes(b"changed")
        self.copy(True, True)
        self.assertEqual((tree / "alpha").read_bytes(), (self.src / "alpha").read_bytes())
        self.assertEqual((tree / "beta").read_bytes(), (self.src / "beta").read_bytes())
        self.assertEqual((tree / "dest-only").read_bytes(), b"keep")

    def test_cancel_parallel_preserves_existing_target(self):
        tree = self.dest / self.src.name
        tree.mkdir()
        (tree / "alpha").write_bytes(b"keep")
        cancel = threading.Event()
        def stop(*args, options=(), **kwargs):
            if "--relative" in options:
                cancel.set()
            return rsync_copy(*args, options=options, **kwargs)
        with patch("yazi_lan.parallel.rsync_copy", stop), self.assertRaises(CopyCancelled):
            copy_lan(self.session, True, [str(self.src)], str(self.dest), False, lambda *_: None, cancel)
        self.assertEqual((tree / "alpha").read_bytes(), b"keep")

    def test_serial_route_uses_original_single_command(self):
        self.session.lan_jobs = 1
        with patch("yazi_lan.transfer.rsync_copy", wraps=rsync_copy) as runner:
            self.copy(True)
        self.assertEqual(runner.call_count, 1)
