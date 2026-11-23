import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yazi_lan import __main__ as client
from yazi_lan.topology import SecureRedirect


class TopologyTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.file = Path(temp.name) / "topology.json"
        self.site = Path(temp.name) / "deployment.json"
        self.site.write_text(json.dumps({"node": "a", "topology": str(self.file)}))
        self.update("node", "a", "--lan", "10.20.0.11", "--netbird", "100.80.0.11")
        self.update("node", "b", "--lan", "10.20.0.22", "--netbird", "100.80.0.22")
        self.update("link", "a", "b", "--route", "lan-first")

    def update(self, *args, success=True):
        proc = subprocess.run([sys.executable, "-m", "yazi_lan.topology", "--file", str(self.file), *args],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode == 0, success, proc.stderr)
        return proc

    def args(self, *options):
        with patch.object(sys, "argv", ["yazi-lan", "--deployment", str(self.site), *options]):
            return client.arguments()

    def test_port_route_and_removal_take_effect_on_reconnect(self):
        args = self.args("backup@b")
        first, _, selected, revision = client.profile(args)
        self.assertEqual((first.ssh, first.port, first.local_lan, revision),
                         ("backup@b", 22, "10.20.0.11", "拓扑 3"))
        self.update("node", "b", "--port", "2222")
        self.update("link", "b", "a", "--route", "netbird-only")
        peer, _, _, revision = client.profile(args, selected)
        self.assertEqual((peer.lan, peer.netbird, peer.port, revision), (None, "100.80.0.22", 2222, "拓扑 5"))
        with self.assertRaises(ValueError):
            client.profile(self.args("b", "--only-lan"))
        self.update("unlink", "a", "b")
        with self.assertRaises(ValueError):
            client.profile(args, selected)

    def test_invalid_edit_and_repeated_edit_leave_file_unchanged(self):
        before = self.file.read_bytes()
        self.update("link", "a", "unregistered", "--route", "lan-first", success=False)
        self.update("node", "b", "--port", "0", success=False)
        self.update("link", "b", "a", "--route", "lan-first")
        self.assertEqual(self.file.read_bytes(), before)

    def test_concurrent_registrations_preserve_both_nodes(self):
        base = [sys.executable, "-m", "yazi_lan.topology", "--file", str(self.file), "node"]
        procs = [subprocess.Popen(base + [name, "--lan", ip], stdout=subprocess.DEVNULL)
                 for name, ip in (("c", "10.1.0.3"), ("d", "10.1.0.4"))]
        for proc in procs:
            self.assertEqual(proc.wait(timeout=10), 0)
        state = json.loads(self.file.read_bytes())
        self.assertEqual(set(state["nodes"]), {"a", "b", "c", "d"})
        self.assertEqual(state["revision"], 5)

    def test_cleartext_topology_and_redirect_rejected(self):
        self.site.write_text(json.dumps({"node": "a", "topology": "http://example.org/topology.json"}))
        with self.assertRaises(ValueError):
            client.profile(self.args("b"))
        with self.assertRaises(ValueError):
            SecureRedirect().redirect_request(None, None, 302, None, {}, "http://example.org/topology.json")

    def test_managed_endpoint_cannot_be_overridden_by_personal_flags(self):
        with self.assertRaises(ValueError):
            client.profile(self.args("b", "--port", "1234"))

    def test_retire_node_removes_its_links(self):
        self.update("remove-node", "b")
        state = json.loads(self.file.read_bytes())
        self.assertEqual((set(state["nodes"]), state["links"], state["revision"]), ({"a"}, [], 4))


if __name__ == "__main__":
    unittest.main()
