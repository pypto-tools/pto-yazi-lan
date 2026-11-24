import contextlib
import curses
import os
import socket
import stat
import unicodedata
from typing import NamedTuple

import paramiko

from .access import check_destination, check_directions
from .connection import remote_directory
from .jobs import CopyQueue, Job
from .preview import preview
from .transfer import selection


FILE_ERRORS = (OSError, EOFError, ValueError, RuntimeError, paramiko.SSHException, paramiko.SFTPError)


class Entry(NamedTuple):
    name: str
    directory: bool
    link: bool
    size: int = None

    @property
    def kind(self):
        if self.directory:
            return "folder"
        if self.link:
            return "link"
        return EXTENSIONS.get(os.path.splitext(self.name)[1][1:].lower(), "file")


# Nerd Font icon, portable icon, terminal colour.
ICONS = {
    "folder": ("\uf07b", "▸", curses.COLOR_BLUE),
    "link": ("\uf0c1", "↗", curses.COLOR_CYAN),
    "archive": ("\uf410", "▦", curses.COLOR_RED),
    "python": ("\ue73c", "λ", curses.COLOR_YELLOW),
    "code": ("\uf121", "λ", curses.COLOR_CYAN),
    "text": ("\uf15c", "≡", curses.COLOR_WHITE),
    "image": ("\uf03e", "▧", curses.COLOR_MAGENTA),
    "model": ("\uf1c0", "◆", curses.COLOR_GREEN),
    "file": ("\uf016", "·", curses.COLOR_WHITE),
}
EXTENSIONS = {
    ext: kind for kind, extensions in {
        "archive": "zip tar gz bz2 xz zst tgz 7z rar",
        "python": "py pyi ipynb",
        "code": "sh bash c cpp h hpp rs go js ts lua yaml yml toml json",
        "text": "md rst txt log csv pdf",
        "image": "png jpg jpeg gif webp svg bmp",
        "model": "pt pth safetensors onnx gguf ckpt",
    }.items() for ext in extensions.split()
}


def file_size(size):
    if size is None:
        return ""
    for unit in ("B", "K", "M", "G", "T", "P"):
        if size < 1024 or unit == "P":
            return f"{size:.0f}{unit}" if size >= 10 or unit == "B" else f"{size:.1f}{unit}"
        size /= 1024


class Pane:
    def __init__(self, path):
        self.path = path
        self.items = []
        self.marked = set()
        self.cursor = 0
        self.writable = None

    def load(self, sftp=None, focus=None):
        if focus is None and self.items:
            focus = self.items[self.cursor].name
        if sftp is None:
            items = []
            with os.scandir(self.path) as entries:
                for e in entries:
                    try:
                        info = e.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        items.append(Entry(e.name, e.is_dir(follow_symlinks=False), e.is_symlink()))
                    else:
                        items.append(Entry(e.name, stat.S_ISDIR(info.st_mode), stat.S_ISLNK(info.st_mode), info.st_size))
        else:
            items = [Entry(e.filename, stat.S_ISDIR(e.st_mode), stat.S_ISLNK(e.st_mode), e.st_size)
                     for e in sftp.listdir_attr(self.path)]
        self.items = sorted(items, key=lambda e: (not e.directory, e.name.casefold(), e.name))
        self.cursor = next((i for i, item in enumerate(self.items) if item.name == focus), 0)

    def current(self):
        if self.items:
            return os.path.join(self.path, self.items[self.cursor].name)
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
        self.writable = None


def write(screen, row, col, width, value, style=0, *, tail=False):
    # Keep control characters in filenames from affecting the terminal or neighbouring pane.
    text = "".join(c if c.isprintable() or unicodedata.category(c) == "Co" else ascii(c)[1:-1] for c in str(value))
    size, result = 0, []
    for char in reversed(text) if tail else text:
        size += 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if size > width:
            break
        result.append(char)
    if tail:
        result.reverse()
        if size > width and result:
            result[0] = "…"
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
        self.basic_icons = False
        self.colors = {}
        self.queue = CopyQueue()
        self.show_jobs = False
        self.job_scroll = 0
        self.show_preview = False
        self.preview_key = None
        self.preview_lines = []
        self.preview_scroll = 0

    def draw(self, screen, session, prompt=""):
        screen.erase()
        height, width = screen.getmaxyx()
        if height < 7 or width < 42:
            write(screen, 0, 0, width, "请放大终端窗口 · q 退出")
            screen.refresh()
            return
        if self.show_jobs and not prompt:
            self.draw_jobs(screen, 0, height - 1, width, expanded=True)
            write(screen, height - 1, 0, width - 1, "t/Esc 返回  ↑↓ 滚动  x 取消传输队列")
            screen.refresh()
            return
        middle = width // 2
        detailed = height >= 12
        pane_top = 2 if detailed else 1
        job_rows = min(3, max(1, len(self.queue.jobs))) if height >= 11 else 0
        dock = job_rows + 1 if job_rows else 0
        end = height - (3 if detailed else 2) - dock
        hosts = (session.peer.local_name or self.local_host, session.peer.remote_name or session.peer.ssh)
        route = f"{session.label}  {session.address}  ·  {'rsync / SSH' if session.rsync else 'SFTP'}"
        write(screen, 0, 0, 14, " pto-yazi-lan ", curses.A_REVERSE | curses.A_BOLD)
        write(screen, 0, 15, width - 15, route, curses.A_BOLD)
        if detailed:
            write(screen, 1, 1, width - 2, f"SSH 已认证  {session.login}    {session.revision}", curses.A_DIM)
        for side in range(2):
            x = middle + 1 if side else 0
            span = width - x if side else middle
            if self.show_preview and side != self.active and not prompt:
                self.draw_preview(screen, session, x, span, pane_top, end)
            else:
                self.draw_pane(screen, side, hosts[side], x, span, pane_top, end, detailed)
        with contextlib.suppress(curses.error):
            screen.vline(pane_top, middle, curses.ACS_VLINE, height - pane_top - 2 - dock)
        if dock:
            self.draw_jobs(screen, height - 2 - dock, dock, width)
        source, target = self.panes[self.active], self.panes[1 - self.active]
        action = " 上传 " if self.active == 0 else " 下载 "
        write(screen, height - 2, 0, 6, action, curses.A_BOLD | curses.A_REVERSE)
        count = len(source.marked) or int(bool(source.items))
        status = f"{count} 项 → {hosts[1 - self.active]}  {target.path}"
        if not dock and self.queue.current and not prompt:
            status = self.job_line(self.queue.current)
        write(screen, height - 2, 7, width - 8, prompt or self.notice or status, curses.A_BOLD)
        keys = ("Enter/s 跳过同名   u 更新   Esc 返回" if prompt else
                "F5 复制  p 预览  t 任务  Tab 切栏  空格 选择  Enter 进入  i 图标  r 重连  q 退出")
        write(screen, height - 1, 0, width - 1, keys)
        screen.refresh()

    def draw_pane(self, screen, side, host, x, span, pane_top, end, detailed):
        pane = self.panes[side]
        start = pane_top + 2
        active = side == self.active
        accent = self.colors.get(curses.COLOR_CYAN if side == 0 else curses.COLOR_GREEN, 0)
        header = accent | curses.A_BOLD | (curses.A_REVERSE if active else 0)
        write(screen, pane_top, x, span, " " * span, header)
        write(screen, pane_top, x + 1, 4, "本机" if side == 0 else "对端", header)
        write(screen, pane_top, x + 6, span - 7, host, header, tail=True)
        write(screen, pane_top + 1, x + 1, span - 2, pane.path, accent, tail=True)
        rows = end - start
        first = max(0, min(pane.cursor - rows // 2, len(pane.items) - rows))
        size_width = 7 if span >= 32 else 0
        for index, item in enumerate(pane.items[first:first + rows], first):
            y = start + index - first
            marked = os.path.join(pane.path, item.name) in pane.marked
            current = active and index == pane.cursor
            row_style = accent | curses.A_REVERSE | curses.A_BOLD if current else 0
            if current:
                write(screen, y, x, span, " " * span, row_style)
            glyph, basic, color = ICONS[item.kind]
            tint = self.colors.get(color, 0)
            if marked:
                write(screen, y, x, 1, "*", row_style | curses.A_BOLD)
            write(screen, y, x + 2, 2, basic if self.basic_icons else glyph, row_style or tint)
            suffix = "/" if item.directory else "@" if item.link else ""
            name_style = tint | curses.A_BOLD if item.directory else 0
            write(screen, y, x + 5, span - 6 - size_width, item.name + suffix, row_style or name_style)
            if size_width and not item.directory:
                write(screen, y, x + span - size_width, size_width,
                      file_size(item.size).rjust(size_width - 1), row_style or curses.A_DIM)
        if not pane.items:
            write(screen, start, x + 2, span - 2, "（空目录）", curses.A_DIM)
        if detailed:
            access = {None: "写入待查", True: "可新建", False: "写入受限"}[pane.writable]
            position = pane.cursor + 1 if pane.items else 0
            write(screen, end, x + 1, span - 1,
                  f"{position}/{len(pane.items)}  ·  已选 {len(pane.marked)}  ·  {access}", accent)

    def draw_preview(self, screen, session, x, span, top, end):
        path = self.panes[self.active].current()
        key = (self.active, path)
        if key != self.preview_key:
            try:
                self.preview_lines = preview(path, session.sftp if self.active else None) if path else ["（空目录）"]
            except FILE_ERRORS as exc:
                self.preview_lines = [f"无法预览  {exc}"]
            self.preview_key = key
            self.preview_scroll = 0
        write(screen, top, x + 1, span - 2, "预览  " + os.path.basename(path or ""), curses.A_BOLD)
        rows = max(0, end - top - 1)
        self.preview_scroll = min(self.preview_scroll, max(0, len(self.preview_lines) - rows))
        for i, line in enumerate(self.preview_lines[self.preview_scroll:self.preview_scroll + rows]):
            write(screen, top + 1 + i, x + 1, span - 2, line)

    @staticmethod
    def job_line(job):
        count, percent, rate, name = job.progress
        direction = "↑" if job.push else "↓"
        detail = ""
        if percent is not None:
            scope = "文件 " if name else ""
            detail = f"  {scope}{percent}%  {rate}  {file_size(count)}"
        return f"{direction} {job.status}{detail}  {name or job.name}  → {job.dest}" + (
            f"  {job.error}" if job.error else "")

    def draw_jobs(self, screen, top, rows, width, expanded=False):
        jobs = self.queue.jobs
        write(screen, top, 0, width, f" 传输列表  {len(jobs)}   t {'收起' if expanded else '展开'}", curses.A_BOLD)
        if not jobs:
            write(screen, top + 1, 1, width - 2, "暂无传输", curses.A_DIM)
            return
        if expanded:
            self.job_scroll = min(self.job_scroll, max(0, len(jobs) - rows + 1))
            visible = jobs[self.job_scroll:self.job_scroll + rows - 1]
        else:
            index = jobs.index(self.queue.current) if self.queue.current else len(jobs)
            start = min(index, max(0, len(jobs) - rows + 1))
            visible = jobs[start:start + rows - 1]
        for offset, job in enumerate(visible, 1):
            write(screen, top + offset, 1, width - 2, self.job_line(job),
                  curses.A_BOLD if job is self.queue.current else 0)

    def transfer(self, screen, session):
        pane, target = self.panes[self.active], self.panes[1 - self.active]
        sources = selection(pane.marked or ([pane.current()] if pane.current() else []))
        hosts = (session.peer.local_name or self.local_host, session.peer.remote_name or session.peer.ssh)
        dest_host = hosts[1 - self.active]
        prompt = f"{len(sources)} 项 → {dest_host} {target.path}"
        self.draw(screen, session, prompt)
        # A copy can be active while the user confirms the next one.
        # Keep refreshing the confirmation without treating a timeout as a key.
        key = screen.getch()
        while key in (-1, curses.KEY_RESIZE):
            self.poll_jobs(session)
            self.draw(screen, session, prompt)
            key = screen.getch()
        if key not in (10, 13, curses.KEY_ENTER, ord("s"), ord("u")):
            return
        push = self.active == 0
        try:
            check_destination(target.path, session.sftp if push else None)
        except FILE_ERRORS:
            target.writable = False
            raise
        target.writable = True
        self.queue.jobs.append(Job(push, sources, target.path, key == ord("u")))
        pane.marked.clear()
        self.notice = ""

    def poll_jobs(self, session):
        finished = self.queue.poll(session)
        if finished:
            target = self.panes[1 if finished.push else 0]
            if finished.status != "完成":
                self.panes[0 if finished.push else 1].marked.update(finished.paths)
            self.notice = finished.error
            self.preview_key = None
            if target.path == finished.dest:
                try:
                    target.load(session.sftp if finished.push else None)
                except FILE_ERRORS as exc:
                    self.notice = str(exc)

    def run(self, screen, session):
        try:
            return self.browse(screen, session)
        finally:
            self.queue.close()

    def browse(self, screen, session):
        self.preview_key = None
        with contextlib.suppress(curses.error):
            curses.curs_set(0)
        self.colors.clear()
        if curses.has_colors():
            background = curses.COLOR_BLACK
            with contextlib.suppress(curses.error):
                curses.use_default_colors()
                background = -1
            for pair, color in enumerate(sorted({icon[2] for icon in ICONS.values()}), 1):
                with contextlib.suppress(curses.error):
                    curses.init_pair(pair, color, background)
                    self.colors[color] = curses.color_pair(pair)
        self.panes[1].load(session.sftp)
        upload, download = check_directions(self.panes[0].path, self.panes[1].path, session.sftp)
        self.panes[0].writable, self.panes[1].writable = download[0], upload[0]
        notices = [message for ok, message in (upload, download) if not ok]
        self.notice = "  ".join(filter(None, [session.reason, *notices]))
        while True:
            self.poll_jobs(session)
            screen.timeout(180 if self.queue.current else -1)
            self.draw(screen, session)
            try:
                key = screen.getch()
            except KeyboardInterrupt:
                if not self.queue.current:
                    raise
                key = ord("x")
            pane = self.panes[self.active]
            remote = session.sftp if self.active else None
            try:
                if key == ord("x"):
                    for job in self.queue.cancel_all():
                        self.panes[0 if job.push else 1].marked.update(job.paths)
                    self.notice = "正在取消传输" if self.queue.current else ""
                    continue
                if key == ord("t") or self.show_jobs and key == 27:
                    self.show_jobs = not self.show_jobs
                    continue
                if key in (ord("q"), 27, ord("r")):
                    if self.queue.current:
                        self.notice = "传输中，等待完成或按 x 取消队列"
                        continue
                    return key == ord("r")
                if self.show_jobs:
                    if key in (curses.KEY_UP, ord("k")):
                        self.job_scroll = max(0, self.job_scroll - 1)
                    elif key in (curses.KEY_DOWN, ord("j")):
                        self.job_scroll += 1
                    continue
                if key == ord("p"):
                    self.show_preview = not self.show_preview
                    self.preview_key = None
                elif self.show_preview and key in (curses.KEY_PPAGE, curses.KEY_NPAGE):
                    step = max(1, screen.getmaxyx()[0] // 2)
                    self.preview_scroll = max(0, self.preview_scroll + (step if key == curses.KEY_NPAGE else -step))
                elif key == 9:
                    self.active = 1 - self.active
                elif key == ord("i"):
                    self.basic_icons = not self.basic_icons
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
