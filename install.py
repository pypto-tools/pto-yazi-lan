#!/usr/bin/env python3
"""Install a pto-yazi-lan release for the current Linux user."""

import argparse
import os
import re
import site
import subprocess
import sys
import tempfile
from pathlib import Path


VERSION = "0.5.0"
WHEEL = f"pto_yazi_lan-{VERSION}-py3-none-any.whl"
RELEASE = f"https://github.com/pypto-tools/pto-yazi-lan/releases/download/v{VERSION}"
SYSTEM_ROOT = Path("/usr/local/lib/pto-yazi-lan")
SYSTEM_BIN = Path("/usr/local/bin")
SITE_FILE = Path("/etc/yazi-lan/deployment.json")
COMMANDS = ("pto-yazi-lan", "pto-yazi-lan-topology", "yazi-lan", "yazi-lan-topology")


def link_if_free(link, target):
    try:
        link.symlink_to(target)
    except FileExistsError:
        return link.is_symlink() and link.readlink() == target
    return True


def install_environment(env, package):
    python = env / "bin/python"
    command = env / "bin/pto-yazi-lan"
    if python.is_file() and all(os.access(env / "bin" / name, os.X_OK) for name in COMMANDS):
        probe = subprocess.run([command, "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode == 0:
            return
    subprocess.run([sys.executable, "-I", "-m", "venv", env], check=True)
    subprocess.run([python, "-I", "-m", "pip", "--disable-pip-version-check",
                    "install", "--force-reinstall", package], check=True)
    subprocess.run([command, "--help"], stdout=subprocess.DEVNULL, check=True)


def main():
    if sys.platform != "linux" or sys.version_info < (3, 9):
        sys.exit("一键安装需要 Linux 和 Python 3.9 或更高版本。")
    import fcntl

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, help="使用已下载的本版本 wheel 文件")
    parser.add_argument("--system", action="store_true", help="在 /usr/local 为本机所有账户安装")
    parser.add_argument("--upgrade", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--node", help="中央拓扑中的本机名称")
    parser.add_argument("--topology", help="中央拓扑的 HTTPS 地址或绝对路径")
    parser.add_argument("--registry-file", help="可写的中央清单原文件，用于部署时登记本机")
    for field in ("lan", "netbird", "port", "interface", "host-key-file", "ca-file"):
        parser.add_argument("--" + field)
    args = parser.parse_args()
    if args.wheel and (args.wheel.name != WHEEL or not args.wheel.is_file()):
        parser.error(f"请提供已下载的 {WHEEL}")

    options = []
    for field in ("lan", "netbird", "port", "interface", "host_key_file", "ca_file"):
        if getattr(args, field) is not None:
            options.extend(["--" + field.replace("_", "-"), getattr(args, field)])
    if bool(args.node) != bool(args.topology) or ((options or args.registry_file) and not args.node):
        parser.error("接入拓扑需要同时指定 --node 和 --topology")
    if args.node and not args.system:
        parser.error("整机部署请加 --system")
    if args.system:
        root, bindir = SYSTEM_ROOT, SYSTEM_BIN
        os.umask(0o022)  # All local users need to execute the shared venv and its entry points.
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "yazi-lan"
        bindir = Path(site.getuserbase()) / "bin"
    root = root.absolute()
    active = root / "current"
    command = bindir / "pto-yazi-lan"
    env = root / "versions" / VERSION
    root.mkdir(parents=True, exist_ok=True)
    bindir.mkdir(parents=True, exist_ok=True)
    with (root / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.upgrade and active.is_symlink():
            current = active.resolve()
            if (current.parent == env.parent.resolve() and current.is_dir()
                    and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", current.name)
                    and tuple(map(int, current.name.split("."))) >= tuple(map(int, VERSION.split(".")))):
                print(f"已安装 pto-yazi-lan {current.name}。")
                return
        for name in COMMANDS:
            path, entry = bindir / name, active / "bin" / name
            if os.path.lexists(path) and not (path.is_symlink() and path.readlink() == entry):
                sys.exit(f"{path} 已由其他安装占用。请先确认已有入口。")
        if active.exists() and not active.is_symlink():
            sys.exit(f"{active} 已被占用。请先确认其内容。")
        package = args.wheel.resolve() if args.wheel else f"{RELEASE}/{WHEEL}"
        install_environment(env, package)
        # A venv's scripts use absolute shebangs. Switch the link after the install succeeds.
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            if not args.system:
                userbase = Path(tmp) / "userbase"
                userbase.write_text(str(bindir.absolute().parent), encoding="utf-8")
                userbase.replace(env / "pto-yazi-lan-userbase")
            candidate = Path(tmp) / "current"
            candidate.symlink_to(env)
            candidate.replace(active)
        for name in COMMANDS:
            if not link_if_free(bindir / name, active / "bin" / name):
                sys.exit(f"无法安装命令，{bindir / name} 已被占用。")
        manager = bindir / "pto-yazi-lan-topology"
        if args.node:
            registry = ["--file", args.registry_file] if args.registry_file else []
            subprocess.run([manager, *registry, "bind", args.node, "--source",
                            args.topology, "--deployment", SITE_FILE, *options], check=True)
        elif args.system and SITE_FILE.exists():
            subprocess.run([manager, "sync", "--setup", "--deployment", SITE_FILE], check=True)
        elif not args.system:
            subprocess.run([command, "--enable-plugin"], check=True)

    print(f"已安装 pto-yazi-lan {VERSION}。以后运行 pto-yazi-lan --update 即可更新。")
    if str(bindir) not in os.get_exec_path():
        print(f"请将 {bindir} 加入 PATH，以便在终端及 Yazi 中使用 pto-yazi-lan。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"安装失败（{exc}）")
