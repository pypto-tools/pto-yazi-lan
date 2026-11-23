import contextlib
import curses
import os
import socket
import stat
import unicodedata

import paramiko

from .connection import remote_directory
from .transfer import copy_selected, selection


FILE_ERRORS = (OSError, EOFError, ValueError, RuntimeError, paramiko.SSHException, paramiko.SFTPError)


class Pane:
    def __init__(self, path):
        self.path = path
        self.items = []
        self.marked = set()
        self.cursor = 0

    def load(self, sftp=None, focus=None):
        if focus is None and self.items:
            focus = self.items[self.cursor][0]
        if sftp is None:
            with os.scandir(self.path) as entries:
                items = [(e.name, e.is_dir(follow_symlinks=False), e.is_symlink()) for e in entries]
        else:
            items = [(e.filename, stat.S_ISDIR(e.st_mode), stat.S_ISLNK(e.st_mode))
                     for e in sftp.listdir_attr(self.path)]
        self.items = sorted(items, key=lambda e: (not e[1], e[0].casefold(), e[0]))
        self.cursor = next((i for i, item in enumerate(self.items) if item[0] == focus), 0)

    def current(self):
        if self.items:
            return os.path.join(self.path, self.items[self.cursor][0])
        return None

    def cd(self, path, sftp):
        if sftp is None:
            if not os.path.isdir(path):
                raise ValueError("用 F5 复制文件，回车进入目录")
            path = os.path.realpath(path)
        else:
            path = remote_directory(sftp, path)
        previous = self.path
        self.path = path
        try:
            self.load(sftp, focus=os.path.basename(previous))
        except BaseException:
            self.path = previous
            raise


def write(screen, row, col, width, value, style=0):
    # Keep control characters in filenames from affecting the terminal or neighbouring pane.
    text = "".join(c if c.isprintable() else ascii(c)[1:-1] for c in str(value))
    size, result = 0, []
    for char in text:
        size += 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if size > width:
            break
        result.append(char)
    with contextlib.suppress(curses.error):
        screen.addstr(row, col, "".join(result), style)


class Browser:
    def __init__(self, local, remote, marked=(), focus=None):
        self.panes = [Pane(local), Pane(remote)]
        self.local_host = socket.gethostname()
        self.panes[0].marked.update(marked)
        self.panes[0].load(focus=os.path.basename(focus) if focus else None)
        self.active = 0
        self.notice = ""

    def draw(self, screen, session, prompt=""):
        screen.erase()
        height, width = screen.getmaxyx()
        if height < 7 or width < 42:
            write(screen, 0, 0, width, "请放大终端窗口 · q 退出")
            screen.refresh()
            return
        middle = width // 2
        title = f"{session.peer.ssh}  {session.label}  {session.address}  ·  {'rsync' if session.rsync else 'SFTP'}"
        write(screen, 0, 0, width, title, curses.A_BOLD)
        rows = height - 5
        for side, pane in enumerate(self.panes):
            x = middle + 1 if side else 0
            span = width - x if side else middle - 1
            label = f"对端 {session.peer.ssh}" if side else f"本机 {self.local_host}"
            write(screen, 1, x, span, f"{label}  {pane.path}  [{len(pane.marked)}]",
                  curses.A_REVERSE if side == self.active else curses.A_BOLD)
            first = max(0, min(pane.cursor - rows // 2, len(pane.items) - rows))
            for j, (name, directory, link) in enumerate(pane.items[first:first + rows]):
                mark = "*" if os.path.join(pane.path, name) in pane.marked else " "
                current = side == self.active and first + j == pane.cursor
                write(screen, j + 2, x, span, f"{mark} {name}{'/' if directory else '@' if link else ''}",
                      curses.A_REVERSE if current else 0)
            if not pane.items:
                write(screen, 2, x, span, "（空目录）", curses.A_DIM)
        with contextlib.suppress(curses.error):
            screen.vline(1, middle - 1, curses.ACS_VLINE, height - 4)
        write(screen, height - 2, 0, width, prompt or self.notice, curses.A_BOLD)
        keys = ("[Enter/s] 跳过同名  [u] 更新  [Esc] 返回" if prompt else
                "Tab 切栏  ↑↓ 移动  Enter 进入  h 上级  空格 选择  F5/c 复制  r 重连  q 退出")
        write(screen, height - 1, 0, width - 1, keys)
        screen.refresh()

    def transfer(self, screen, session):
        pane, target = self.panes[self.active], self.panes[1 - self.active]
        sources = selection(pane.marked or ([pane.current()] if pane.current() else []))
        hosts = (self.local_host, session.peer.ssh)
        dest_host = hosts[1 - self.active]
        prompt = f"{len(sources)} 项 → {dest_host} {target.path}"
        self.draw(screen, session, prompt)
        key = screen.getch()
        if key not in (10, 13, curses.KEY_ENTER, ord("s"), ord("u")):
            return
        curses.def_prog_mode()
        curses.endwin()
        try:
            print(f"{session.label} {session.address}  {hosts[self.active]} → {dest_host}"
                  f"  {len(sources)} 项 → {target.path!r}  ·  Ctrl-C 中断", flush=True)
            copy_selected(session, self.active == 0, sources, target.path, key == ord("u"))
            self.notice = "已完成复制"
            pane.marked.clear()
        except KeyboardInterrupt:
            self.notice = "复制已中断，可按 F5 重试"
        except FILE_ERRORS as exc:
            self.notice = str(exc)
        finally:
            print(self.notice, flush=True)
            print("按回车返回 ", end="", flush=True)
            with contextlib.suppress(EOFError, KeyboardInterrupt):
                input()
            curses.reset_prog_mode()
            screen.keypad(True)
            screen.clear()
        target.load(session.sftp if self.active == 0 else None)

    def run(self, screen, session):
        with contextlib.suppress(curses.error):
            curses.curs_set(0)
        self.panes[1].load(session.sftp)
        self.notice = session.reason
        while True:
            self.draw(screen, session)
            key = screen.getch()
            pane = self.panes[self.active]
            remote = session.sftp if self.active else None
            try:
                if key in (ord("q"), 27):
                    return False
                if key == ord("r"):
                    return True
                if key == 9:
                    self.active = 1 - self.active
                elif key in (curses.KEY_DOWN, ord("j")):
                    pane.cursor = min(pane.cursor + 1, max(0, len(pane.items) - 1))
                elif key in (curses.KEY_UP, ord("k")):
                    pane.cursor = max(0, pane.cursor - 1)
                elif key in (10, 13, curses.KEY_ENTER, curses.KEY_RIGHT, ord("l")) and pane.current():
                    pane.cd(pane.current(), remote)
                elif key in (127, 8, curses.KEY_BACKSPACE, curses.KEY_LEFT, ord("h")):
                    pane.cd(os.path.dirname(pane.path), remote)
                elif key == ord(" ") and pane.current():
                    pane.marked.symmetric_difference_update([pane.current()])
                elif key in (curses.KEY_F5, ord("c")):
                    self.transfer(screen, session)
            except FILE_ERRORS as exc:
                self.notice = str(exc) or "会话已断开，按 r 重连"
