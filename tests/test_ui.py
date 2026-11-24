import curses
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from yazi_lan.ui import ICONS, Pane, file_size, write


class PanelRenderingTest(unittest.TestCase):
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
