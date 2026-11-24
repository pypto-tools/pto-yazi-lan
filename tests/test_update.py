import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from yazi_lan import __main__ as client
from yazi_lan import topology, update


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.version = "20.0.0"
        self.wheel = "pto_yazi_lan-20.0.0-py3-none-any.whl"
        self.assets = {"install.py": b"installer", self.wheel: b"wheel"}
        self.manifest = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n"
                                for name, data in self.assets.items()).encode()
        patcher = patch.object(update, "download", side_effect=self.download)
        self.fetch = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(update.subprocess, "run")
        self.run = patcher.start()
        self.run.return_value.returncode = 0
        self.addCleanup(patcher.stop)

    def download(self, url, _limit):
        if url == update.RELEASES + "/latest/download/SHA256SUMS":
            return self.manifest
        base = update.RELEASES + "/download/v" + self.version + "/"
        self.assertTrue(url.startswith(base), url)
        return self.assets[url[len(base):]]

    def invoke(self, *options):
        with patch.object(sys, "argv", ["pto-yazi-lan", *options]), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return client.main()

    def test_equal_or_older_release_does_not_install(self):
        for installed in ("20.0.0", "21.0.0"):
            with patch.object(update, "__version__", installed):
                self.assertEqual(self.invoke("--update"), 0)
        self.assertEqual(self.fetch.call_count, 2)
        self.run.assert_not_called()

    def test_personal_install_reuses_original_locations(self):
        prefix = self.root / "xdg/yazi-lan/versions/0.5.0"
        prefix.mkdir(parents=True)
        (prefix / "pto-yazi-lan-userbase").write_text(str(self.root / "user base"))
        with patch.object(sys, "prefix", str(prefix)):
            self.assertEqual(self.invoke("--update"), 0)
        command = self.run.call_args.args[0]
        self.assertEqual(Path(command[2]).name, "install.py")
        self.assertIn("--upgrade", command)
        self.assertNotIn("--system", command)
        environment = self.run.call_args.kwargs["env"]
        self.assertEqual(environment["XDG_DATA_HOME"], str(self.root / "xdg"))
        self.assertEqual(environment["PYTHONUSERBASE"], str(self.root / "user base"))

    def test_system_update_reenters_trusted_install_before_downloading_executables(self):
        with patch.object(update, "SYSTEM_ROOT", self.root), \
             patch.object(sys, "prefix", str(self.root / "versions/0.5.0")), \
             patch.object(update.os, "geteuid", return_value=1000):
            self.run.return_value.returncode = 7
            self.assertEqual(self.invoke("--update"), 7)
        self.run.assert_called_once_with(["sudo", "--", str(self.root / "current/bin/python"),
                                          "-I", "-m", "yazi_lan", "--update"])
        self.assertEqual(self.fetch.call_count, 1)

    def test_failed_hash_or_invalid_manifest_never_runs_an_installer(self):
        self.assets[self.wheel] = b"bad download"
        self.assertEqual(self.invoke("--update"), 1)
        self.manifest += self.manifest
        self.assertEqual(self.invoke("--update"), 1)
        self.run.assert_not_called()

    def test_sync_updates_and_lists_the_selected_binding_without_sudo(self):
        path, inventory = self.root / "site.json", self.root / "topology.json"
        path.write_text(json.dumps({"node": "a", "topology": str(inventory)}))
        inventory.write_text(json.dumps({"schema": 1, "revision": 12, "nodes": {
            "a": {"lan": "10.0.0.1"}, "b": {"lan": "10.0.0.2"}},
            "links": [{"nodes": ["a", "b"], "route": "lan-only"}]}))
        with patch.object(sys, "argv", ["pto-yazi-lan", "--sync", "--list", "--deployment", str(path)]), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(client.main(), 0)
        self.assertIn("本机拓扑修订 12", output.getvalue())
        self.assertIn("b  lan-only", output.getvalue())
        self.run.assert_not_called()

    def test_only_the_default_binding_can_request_privileged_sync(self):
        with patch.object(topology, "sync_site", side_effect=PermissionError("read-only")), \
             patch.object(topology.os, "geteuid", return_value=1000), \
             patch.object(Path, "is_file", return_value=True):
            self.assertEqual(self.invoke("--sync", "--deployment", str(self.root / "custom")), 1)
            self.run.assert_not_called()
            self.assertEqual(self.invoke("--sync"), 0)
        self.run.assert_called_once_with(["sudo", "--", "/usr/local/bin/pto-yazi-lan-topology", "sync"])
