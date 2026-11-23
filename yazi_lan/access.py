"""Check the directories involved in a transfer with the authenticated account."""

import os
import tempfile
import uuid

import paramiko


def check_destination(directory, sftp=None):
    """Create an exclusive empty file, then remove only the file this probe created."""
    if sftp is None:
        with tempfile.NamedTemporaryFile(prefix=".yazi-lan-check-", dir=directory):
            pass
        return
    path = os.path.join(directory, ".yazi-lan-check-" + uuid.uuid4().hex)
    file = sftp.open(path, "wx")
    try:
        file.close()
    finally:
        try:
            sftp.remove(path)
        except (OSError, EOFError, paramiko.SSHException) as exc:
            raise RuntimeError(f"已新建检查文件，但无法清理 {path!r}（{exc}）") from exc


def check_directions(local, remote, sftp):
    checks = []
    for label, path, client in (("上传到对端", remote, sftp), ("下载到本机", local, None)):
        try:
            check_destination(path, client)
        except PermissionError as exc:
            checks.append((False, f"{label} {path!r}  当前账户无新建权限（{exc}）"))
        except (OSError, RuntimeError) as exc:
            checks.append((False, f"{label} {path!r}  检查失败（{exc}）"))
        else:
            checks.append((True, f"{label} {path!r}  可新建文件"))
    return checks
