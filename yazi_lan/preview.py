"""Bounded, read-only previews for the focused entry."""
import codecs
import os
import stat
import struct

LIMIT = 8192
TEXT_EXTENSIONS = set("txt log md csv tsv json jsonl yaml yml toml ini cfg conf env xml html css js ts py sh c h cpp hpp rs go lua java sql rst".split())


def format_summary(data, size):
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR" and len(data) >= 24:
        w, h = struct.unpack_from(">II", data, 16)
        return ["PNG 图像", f"{w} × {h} 像素", f"{size} B"]
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        w, h = struct.unpack_from("<HH", data, 6)
        return ["GIF 图像", f"{w} × {h} 像素", f"{size} B"]
    if data.startswith(b"\xff\xd8\xff"):
        return ["JPEG 图像", f"{size} B"]
    if data.startswith(b"%PDF-"):
        version = data[5:8].decode("ascii", errors="replace")
        return [f"PDF 文档  {version}", f"{size} B"]
    if data.startswith(b"PK\x03\x04") and len(data) >= 30:
        result = ["ZIP 容器", f"{size} B"]
        length = int.from_bytes(data[26:28], "little")
        if 0 < length <= len(data) - 30:
            utf8 = int.from_bytes(data[6:8], "little") & 0x800
            result.append("首个条目  " + data[30:30 + length].decode("utf-8" if utf8 else "cp437", errors="replace"))
        return result
    return None


def text_preview(data, path, complete):
    # UTF-32LE and UTF-16LE share a two-byte prefix.
    encodings = ["utf-8"]
    for bom, codec in ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
                       (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"),
                       (codecs.BOM_UTF8, "utf-8-sig")):
        if data.startswith(bom):
            encodings = [codec]
            break
    else:
        ext = os.path.splitext(path)[1][1:].lower()
        if b"\0" in data:
            if ext not in TEXT_EXTENSIONS or len(data) < 4:
                return None
            even, odd = data[::2], data[1::2]
            if odd.count(0) >= len(odd) / 3 and not even.count(0):
                encodings = ["utf-16-le"]
            elif even.count(0) >= len(even) / 3 and not odd.count(0):
                encodings = ["utf-16-be"]
            else:
                return None
        elif ext in TEXT_EXTENSIONS or not ext:
            encodings.append("gb18030")
    for codec in encodings:
        try:
            text = codecs.getincrementaldecoder(codec)().decode(data, final=complete)
        except UnicodeDecodeError:
            continue
        # Logs may contain a few control codes, but binary records are not text.
        controls = sum(ord(c) < 32 and c not in "\n\r\t\f" for c in text)
        if "\0" not in text and controls <= len(text) // 50:
            return [line.expandtabs(4) for line in text.splitlines()] or ["（空文本）"]
    return None


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
    summary = format_summary(data, info.st_size)
    if summary:
        return summary
    lines = text_preview(data, path, len(data) < LIMIT or info.st_size == len(data))
    if lines is not None:
        return lines
    return [f"二进制 · {info.st_size} B", *[
        f"{i:04x}  {data[i:i + 16].hex(' ')}" for i in range(0, min(len(data), 256), 16)
    ]]
