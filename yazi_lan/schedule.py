"""Install the periodic pull for the system-wide Linux deployment."""

import os
import shutil
import subprocess
from pathlib import Path


ASSETS = Path("/usr/local/lib/pto-yazi-lan/current/share/yazi-lan/scheduler")
SYSTEMD = Path("/etc/systemd/system")
CRON = Path("/etc/cron.d")
NAME = "pto-yazi-lan-sync"


def install_link(path, source):
    if not source.is_file():
        raise ValueError(f"找不到整机安装文件 {source}")
    try:
        path.symlink_to(source)
    except FileExistsError:
        if not (path.is_symlink() and path.readlink() == source):
            raise ValueError(f"{path} 已有其他配置，请管理员检查定时任务")


def enable():
    if Path("/run/systemd/system").is_dir() and shutil.which("systemctl"):
        for suffix in (".service", ".timer"):
            install_link(SYSTEMD / (NAME + suffix), ASSETS / (NAME + suffix))
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "--now", NAME + ".timer"], check=True)
        cron = CRON / NAME
        if cron.is_symlink() and os.readlink(cron) == str(ASSETS / (NAME + ".cron")):
            cron.unlink()
        return f"已启用 {NAME}.timer，每分钟检查中心"
    if CRON.is_dir() and (shutil.which("crond") or shutil.which("cron")):
        install_link(CRON / NAME, ASSETS / (NAME + ".cron"))
        return f"已安装 {CRON / NAME}，由 cron 每分钟检查中心"
    raise ValueError("本机未检测到 systemd 或 cron")
