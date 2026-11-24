"""Bounded, read-only previews for the focused entry."""
import codecs
import os
import stat

LIMIT = 8192


def preview(path, sftp=None):
    fs = sftp or os
    info = fs.lstat(path)
    mode = info.st_mode
    if stat.S_ISLNK(mode):
        return ["符号链接", "→ " + fs.readlink(path)]
    if stat.S_ISDIR(mode):
        return ["目录", "按 Enter 浏览内容"]
    if not stat.S_ISREG(mode):
        return ["特殊文件", stat.filemode(mode)]
    if sftp:
        with sftp.open(path, "rb") as stream:
            data = stream.read(LIMIT)
    else:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb", buffering=0) as stream:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return ["文件类型已改变"]
            data = stream.read(LIMIT)
    if not data:
        return ["（空文件）"]
    if b"\0" not in data:
        try:
            text = codecs.getincrementaldecoder("utf-8-sig")().decode(data, final=len(data) < LIMIT)
        except UnicodeDecodeError:
            pass
        else:
            return [line.expandtabs(4) for line in text.splitlines()] or ["（空文本）"]
    return [f"二进制 · {info.st_size} B", *[
        f"{i:04x}  {data[i:i + 16].hex(' ')}" for i in range(0, min(len(data), 256), 16)
    ]]
