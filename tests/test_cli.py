import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from yazi_lan import __main__ as client


class UnifiedCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.inventory = Path(tmp.name) / "inventory.json"
        self.site = Path(tmp.name) / "deployment.json"

    def run_cli(self, *options, program="pto-yazi-lan", status=0):
        with patch.object(sys, "argv", [program, *map(str, options)]), \
             redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            try:
                code = client.main()
            except SystemExit as exc:
                code = exc.code
        self.assertEqual(code, status, err.getvalue())
        return out.getvalue() + err.getvalue()

    def test_bind_register_and_setup_with_main_flags(self):
        self.run_cli("--bind", "a", "--source", self.inventory, "--deployment", self.site, "--lan", "10.0.0.1")
        self.run_cli("--file", self.inventory, "--node", "b", "--lan", "10.0.0.2", "--port", "2222")
        self.run_cli("--link", "a", "b", "--file", self.inventory, "--route", "lan-only")
        self.assertIn("修订 3", self.run_cli("--check-topology", "--file", self.inventory))
        self.assertIn("直接使用本地清单", self.run_cli("--setup-sync", "--deployment", self.site))
        listing = self.run_cli("--sync", "--list", "--deployment", self.site)
        self.assertIn("b  lan-only  SSH 2222", listing)
        self.run_cli("--file", self.inventory, "--unlink", "a", "b")
        self.assertIn("尚未为本机配置连接", self.run_cli("--list", "--deployment", self.site))
        self.run_cli("--remove-node", "b", "--file", self.inventory)
        self.assertEqual(set(json.loads(self.inventory.read_bytes())["nodes"]), {"a"})

    def test_peer_flag_and_positional_compatibility(self):
        self.run_cli("--bind", "a", "--source", self.inventory, "--deployment", self.site, "--lan", "10.0.0.1")
        self.run_cli("--node", "b", "--file", self.inventory, "--lan", "10.0.0.2")
        self.run_cli("--link", "a", "b", "--file", self.inventory, "--route", "lan-only")
        for selector in (["--peer", "backup@b"], ["backup@b"]):
            with patch.object(sys, "argv", ["pto-yazi-lan", "--deployment", str(self.site), *selector]):
                peer, _, selected, _ = client.profile(client.arguments())
            self.assertEqual((peer.ssh, selected), ("backup@b", "backup@b"))
        self.run_cli("--peer", "b", "a", status=2)

    def test_invalid_combinations_cannot_edit_inventory_or_open_browser(self):
        self.run_cli("--node", "a", "--file", self.inventory, "--lan", "10.0.0.1")
        original = self.inventory.read_bytes()
        for opts in (("--remove-node", "a", "--list"), ("--remove-node", "a", "--update"),
                     ("--node", "b", "--link", "a", "b"), ("--link", "a", "b"),
                     ("--check-topology", "--peer", "b"), ("--node", "b", "--user", "root"),
                     ("--bind", "a"), ("--check",), ("--remove", "a")):
            with self.subTest(opts=opts):
                self.run_cli("--file", self.inventory, *opts, status=2)
        self.run_cli("--node", "b", "--lan", "10.0.0.2", status=2)
        self.run_cli("--bind", "a", "--source", self.inventory, "--deployment", self.inventory, status=1)
        self.assertEqual(self.inventory.read_bytes(), original)

    def test_owned_legacy_links_forward_command_and_literal_node_names(self):
        legacy = "pto-yazi-lan-topology"
        self.run_cli("--file", self.inventory, "node", "node", "--lan", "10.0.0.2", program=legacy)
        self.run_cli("bind", "node", "--source", self.inventory, "--deployment", self.site, program=legacy)
        self.assertIn("修订 1", self.run_cli("--file=" + str(self.inventory), "check", program="yazi-lan-topology"))
        self.assertIn("直接使用本地清单", self.run_cli("sync", "--setup", "--deployment", self.site, program=legacy))
        self.assertIn("修订 1", self.run_cli("sync", "--deployment", self.site, program=legacy))
        self.run_cli("unknown", program=legacy, status=2)
        self.run_cli(program=legacy, status=2)

    def test_help_lists_all_public_operations(self):
        help_text = self.run_cli("--help")
        for option in ("--peer", "--list", "--sync", "--update", "--check", "--enable-plugin",
                       "--version", "--node", "--bind", "--link", "--unlink", "--remove-node",
                       "--check-topology", "--setup-sync", "--file", "--source", "--route"):
            self.assertIn(option, help_text)
