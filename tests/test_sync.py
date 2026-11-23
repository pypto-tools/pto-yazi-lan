import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from yazi_lan import schedule, topology


class SyncTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.site = self.root / "deployment.json"
        self.dest = self.root / "state/topology.json"
        self.binding = {"node": "a", "topology": "https://center.example/topology.json"}
        self.site.write_text(json.dumps(self.binding))
        self.inventory = {"schema": 1, "revision": 1, "nodes": {
            "a": {"lan": "10.0.0.1"}, "b": {"lan": "10.0.0.2"}},
            "links": [{"nodes": ["a", "b"], "route": "lan-only"}]}
        for key, value in (("DEPLOYMENT", self.site), ("SNAPSHOT", self.dest)):
            change = patch.object(topology, key, value)
            change.start()
            self.addCleanup(change.stop)
        opener = patch.object(topology, "build_opener")
        self.open = opener.start().return_value.open
        self.addCleanup(opener.stop)

    def serve(self, body=None, *, status=200, length=None, etag=None):
        body = json.dumps(self.inventory).encode() if body is None else body
        response = io.BytesIO(body)
        response.status = status
        response.headers = {"Content-Length": str(len(body) if length is None else length)}
        if etag:
            response.headers["ETag"] = etag
        self.open.side_effect = None
        self.open.return_value = response

    def test_shared_read_is_offline_and_a_failed_pull_keeps_previous_revision(self):
        with self.assertRaisesRegex(ValueError, "没有可用的本地拓扑"):
            topology.load_site(self.site)
        self.open.assert_not_called()
        self.serve(etag='"one"')
        self.assertEqual(topology.sync_site(self.site), self.inventory)
        self.assertEqual(self.dest.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.dest.parent.stat().st_mode & 0o777, 0o755)
        old = self.dest.stat()
        self.open.side_effect = OSError("offline")
        with self.assertRaisesRegex(ValueError, "offline"):
            topology.sync_site(self.site)
        self.assertEqual(topology.load_site(self.site), ("a", self.inventory, {"b": "lan-only"}))
        self.assertEqual(self.open.call_count, 2)
        self.assertEqual(self.dest.stat().st_ino, old.st_ino)
        self.open.side_effect = HTTPError(self.binding["topology"], 304, "Not Modified", {}, None)
        self.assertEqual(topology.sync_site(self.site), self.inventory)
        self.assertEqual(self.open.call_args.args[0].get_header("If-none-match"), '"one"')
        self.assertEqual(self.dest.stat().st_mtime_ns, old.st_mtime_ns)

    def test_incomplete_invalid_and_oversized_updates_are_never_published(self):
        self.serve()
        topology.sync_site(self.site)
        original = self.dest.read_bytes()
        for body, status, length in (
            (b"{", 200, 1), (b"{}", 200, 2), (None, 200, 1500), (None, 206, None),
            (None, 200, topology.MAX_SIZE + 1),
        ):
            with self.subTest(status=status, length=length):
                self.serve(body, status=status, length=length)
                with self.assertRaises(ValueError):
                    topology.sync_site(self.site)
                self.assertEqual(self.dest.read_bytes(), original)
        self.inventory.update(revision=2, nodes={}, links=[])
        self.serve()
        topology.sync_site(self.site)
        with self.assertRaisesRegex(ValueError, "未登记本机"):
            topology.load_site(self.site)
        self.assertEqual(json.loads(self.dest.read_bytes())["revision"], 2)

    def test_rebinding_and_corrupt_snapshot_require_a_fresh_download(self):
        self.inventory[topology.SYNC_META] = {"origin": "foreign", "etag": '"forged"'}
        self.serve(etag='"one"')
        topology.sync_site(self.site)
        old = self.dest.read_bytes()
        self.assertNotIn(b"forged", old)
        for field, value in (("node", "b"), ("topology", "https://other.example/t.json"), ("ca_file", "/new/ca")):
            self.site.write_text(json.dumps({**self.binding, field: value}))
            with self.assertRaisesRegex(ValueError, "不匹配"):
                topology.load_site(self.site)
        self.site.write_text(json.dumps(self.binding))
        self.dest.write_bytes(b"{")
        self.open.side_effect = HTTPError(self.binding["topology"], 304, "Not Modified", {}, None)
        with self.assertRaisesRegex(ValueError, "304"):
            topology.sync_site(self.site)
        self.assertIsNone(self.open.call_args.args[0].get_header("If-none-match"))
        self.serve()
        topology.sync_site(self.site)
        self.assertEqual(topology.load_site(self.site)[2], {"b": "lan-only"})

    def test_custom_binding_refreshes_then_falls_back_to_its_own_disk_copy(self):
        custom = self.root / "custom.json"
        custom.write_bytes(self.site.read_bytes())
        self.serve()
        self.assertEqual(topology.load_site(custom)[1], self.inventory)
        self.assertTrue((self.root / "custom.json.d/topology.json").is_file())
        self.assertFalse(self.dest.exists())
        self.open.side_effect = OSError("offline")
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(topology.load_site(custom)[1], self.inventory)
        self.assertIn("offline", err.getvalue())

    def test_unchanged_untagged_topology_preserves_file_and_later_update_replaces_it(self):
        self.serve()
        topology.sync_site(self.site)
        inode = self.dest.stat().st_ino
        self.serve()
        topology.sync_site(self.site)
        self.assertEqual(inode, self.dest.stat().st_ino)
        self.inventory["revision"] = 2
        self.inventory["nodes"]["b"]["port"] = 2200
        self.serve()
        topology.sync_site(self.site)
        self.assertNotEqual(inode, self.dest.stat().st_ino)
        self.assertEqual(topology.load_site(self.site)[1], self.inventory)


class TimerTest(unittest.TestCase):
    def test_systemd_and_cron_install_packaged_jobs_idempotently(self):
        assets = Path(__file__).resolve().parents[1] / "packaging"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            systemd, cron = root / "systemd", root / "cron"
            systemd.mkdir()
            cron.mkdir()
            with patch.object(schedule, "SYSTEMD", systemd), patch.object(schedule, "CRON", cron), \
                 patch.object(schedule, "ASSETS", assets), patch.object(schedule.shutil, "which", return_value="cmd"), \
                 patch.object(schedule.subprocess, "run") as run:
                with patch.object(Path, "is_dir", lambda p: p == cron):
                    schedule.enable()
                    self.assertTrue((cron / schedule.NAME).is_file())
                with patch.object(Path, "is_dir", return_value=True):
                    for _ in range(2):
                        schedule.enable()
                self.assertFalse(os.path.lexists(cron / schedule.NAME))
                self.assertTrue((systemd / (schedule.NAME + ".service")).is_file())
                self.assertIn("OnCalendar=minutely", (systemd / (schedule.NAME + ".timer")).read_text())
                run.assert_called_with(["systemctl", "enable", "--now", schedule.NAME + ".timer"], check=True)
