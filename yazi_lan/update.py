"""Update the running installation from the project's published GitHub release."""

import hashlib
import os
import re
import site
import subprocess
import sys
import tempfile
from http.client import HTTPException
from pathlib import Path
from urllib.request import build_opener

from . import __version__
from .topology import SecureRedirect


RELEASES = "https://github.com/pypto-tools/pto-yazi-lan/releases"
SYSTEM_ROOT = Path("/usr/local/lib/pto-yazi-lan")
WHEEL_NAME = re.compile(r"(?:pto_)?yazi_lan-(\d+\.\d+\.\d+)-py3-none-any\.whl", re.ASCII)


def download(url, limit):
    try:
        with build_opener(SecureRedirect()).open(url, timeout=20) as response:
            data = response.read(limit + 1)
            if response.status != 200 or len(data) > limit:
                raise ValueError("发布文件大小或 HTTP 状态无效")
            return data
    except (OSError, HTTPException) as exc:
        raise ValueError(f"无法下载发布文件 {url}（{exc}）") from exc


def latest_release():
    manifest = download(f"{RELEASES}/latest/download/SHA256SUMS", 65536)
    hashes = {}
    for line in manifest.decode("ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](\S+)", line)
        if not match or match[2] in hashes:
            raise ValueError("发布校验清单格式无效")
        hashes[match[2]] = match[1]
    wheels = [name for name in hashes if WHEEL_NAME.fullmatch(name)]
    if len(wheels) != 1 or "install.py" not in hashes:
        raise ValueError("发布校验清单缺少唯一的软件包或安装器")
    wheel = wheels[0]
    return WHEEL_NAME.fullmatch(wheel)[1], wheel, hashes


def verified_asset(directory, version, name, hashes):
    data = download(f"{RELEASES}/download/v{version}/{name}", 32 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != hashes[name]:
        raise ValueError(f"{name} 的 SHA256 与发布清单不符，更新已停止")
    path = directory / name
    path.write_bytes(data)
    return path


def update():
    print(f"正在检查 pto-yazi-lan 更新（当前 {__version__}）…", flush=True)
    version, wheel_name, hashes = latest_release()
    if tuple(map(int, version.split("."))) <= tuple(map(int, __version__.split("."))):
        print(f"pto-yazi-lan {__version__} 无需更新。")
        return 0

    prefix = Path(sys.prefix).resolve()
    system = prefix.parent == (SYSTEM_ROOT / "versions").resolve()
    if system and os.geteuid() != 0:
        print("整机更新需要管理员权限，正在调用 sudo。", flush=True)
        # The privileged process rechecks the release and downloads its own verified files.
        python = SYSTEM_ROOT / "current/bin/python"
        return subprocess.run(["sudo", "--", str(python), "-I", "-m", "yazi_lan", "--update"]).returncode

    print(f"正在更新到 {version}…", flush=True)
    with tempfile.TemporaryDirectory(prefix="pto-yazi-lan-update-") as tmp:
        directory = Path(tmp)
        wheel = verified_asset(directory, version, wheel_name, hashes)
        marker = prefix / "pto-yazi-lan-userbase"
        personal = prefix.parent.name == "versions" and marker.is_file()
        env = os.environ.copy()
        if system or personal:
            script = verified_asset(directory, version, "install.py", hashes)
            command = [sys.executable, "-I", str(script), "--upgrade", "--wheel", str(wheel)]
            if system:
                command.append("--system")
            else:
                env["XDG_DATA_HOME"] = str(prefix.parents[2])
                env["PYTHONUSERBASE"] = marker.read_text(encoding="utf-8")
        else:
            command = [sys.executable, "-I", "-m", "pip", "install", "--upgrade", str(wheel)]
            user_site = Path(site.getusersitepackages()).resolve()
            env["PIP_USER"] = "1" if Path(__file__).resolve().is_relative_to(user_site) else "0"
        result = subprocess.run(command, env=env)
        if result.returncode == 0:
            print("更新完成。运行 pto-yazi-lan --version 查看已安装版本。")
        return result.returncode
