import curses
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from yazi_lan.jobs import Job
from yazi_lan.ui import Browser, ICONS, Pane, file_size, write


class PanelRenderingTest(unittest.TestCase):
    def test_preview_footer_fits_minimum_width_and_yields_to_copy_prompt(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(curses, "ACS_VLINE", ord("|"), create=True):
            browser = Browser(folder, "/remote")
            browser.show_preview = True
            screen = Mock()
            session = SimpleNamespace(peer=SimpleNamespace(local_name="local", remote_name="remote"),
                                      label="LAN", address="address", rsync=True, login="user", revision="1")
            for height, width in ((7, 42), (24, 100)):
                screen.getmaxyx.return_value = (height, width)
                browser.draw(screen, session)
                row, _, help_text, _ = screen.addstr.call_args.args
                self.assertEqual(row, height - 1)
                self.assertIn("PgUp 上翻  PgDn 下翻  p 关闭预览", help_text)
                browser.draw(screen, session, "确认复制")
                self.assertIn("Esc 返回", screen.addstr.call_args.args[2])
                self.assertNotIn("PgDn", screen.addstr.call_args.args[2])

    def test_portable_icons_and_transfer_list_divider(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"PTO_YAZI_ICONS": "ascii"}):
            (Path(folder) / "script.py").touch()
            browser = Browser(folder, "/remote")
            screen = Mock()
            screen.getmaxyx.return_value = (12, 60)
            session = SimpleNamespace(peer=SimpleNamespace(local_name="local", remote_name="remote"),
                                      label="LAN", address="address", rsync=True, login="user", revision="1")
            browser.queue.jobs = [Job(True, ["/test"], "/", False) for _ in range(3)]
            with patch.object(curses, "ACS_VLINE", ord("|"), create=True):
                browser.draw(screen, session)
            row, col, _, width, _ = screen.hline.call_args.args
            self.assertEqual((row, col, width), (6, 0, 60))
            texts = [(call.args[0], call.args[2]) for call in screen.addstr.call_args_list]
            self.assertTrue(any(y < row and value == "Py" for y, value in texts))
            self.assertTrue(any(y == row + 1 and "传输列表" in value for y, value in texts))
            screen.reset_mock()
            browser.show_jobs = True
            browser.draw(screen, session)
            self.assertEqual(screen.hline.call_args.args[0], 1)

    def test_icons_pass_through_while_filename_controls_are_escaped(self):
        screen = Mock()
        glyph = ICONS["folder"][0]
        write(screen, 0, 0, 2, glyph, curses.A_BOLD)
        screen.addstr.assert_called_with(0, 0, glyph, curses.A_BOLD)
        write(screen, 0, 0, 30, "log\x1b[31m")
        screen.addstr.assert_called_with(0, 0, r"log\x1b[31m", 0)

    def test_long_path_keeps_directory_name(self):
        screen = Mock()
        write(screen, 0, 0, 8, "/some/long/数据", tail=True)
        screen.addstr.assert_called_with(0, 0, "…ng/数据", 0)

    def test_file_types_size_and_sorting(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "数据").mkdir()
            (path / "TRAIN.PY").write_bytes(b"x" * 2048)
            os.symlink("TRAIN.PY", path / "alias")
            pane = Pane(folder)
            pane.load(focus="TRAIN.PY")
            self.assertEqual([e.kind for e in pane.items], ["folder", "link", "python"])
            self.assertEqual(pane.current(), str(path / "TRAIN.PY"))
            self.assertEqual(file_size(pane.items[pane.cursor].size), "2.0K")
