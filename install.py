#!/usr/bin/env python3
"""Install a pto-yazi-lan release for the current Linux user."""

import argparse
import os
import site
import subprocess
import sys
import tempfile
from pathlib import Path


VERSION = "0.2.0"
WHEEL = f"yazi_lan-{VERSION}-py3-none-any.whl"
RELEASE = f"https://github.com/pypto-tools/pto-yazi-lan/releases/download/v{VERSION}"


def link_if_free(link, target):
    try:
        link.symlink_to(target)
    except FileExistsError:
        return link.is_symlink() and link.readlink() == target
    return True


def install_environment(env, package):
    python = env / "bin/python"
    command = env / "bin/yazi-lan"
    if os.access(python, os.X_OK) and os.access(command, os.X_OK):
        probe = subprocess.run([command, "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode == 0:
            return
    subprocess.run([sys.executable, "-I", "-m", "venv", env], check=True)
    subprocess.run([python, "-I", "-m", "pip", "--disable-pip-version-check",
                    "install", "--force-reinstall", package], check=True)
    subprocess.run([command, "--help"], stdout=subprocess.DEVNULL, check=True)


def install_plugin(shipped, config):
    plugin = Path("plugins/lan-copy.yazi")
    (config / plugin.parent).mkdir(parents=True, exist_ok=True)
    if not link_if_free(config / plugin, shipped / plugin):
        print(f"已有插件 {config / plugin}。可从 {shipped / plugin} 手动合并。")
        return

    keymap = config / "keymap.toml"
    default_keys = (shipped / "keymap.toml").read_bytes()
    try:
        with keymap.open("xb") as file:
            file.write(default_keys)
    except FileExistsError:
        if keymap.is_file() and keymap.read_bytes() == default_keys:
            return
        print(f"已保留 {keymap}。如需绑定 F5，可将 {shipped / 'keymap.toml'} 中的条目加入其中。")


def main():
    if sys.platform != "linux" or sys.version_info < (3, 9):
        sys.exit("一键安装需要 Linux 和 Python 3.9 或更高版本。")
    import fcntl

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, help="使用已下载的本版本 wheel 文件")
    args = parser.parse_args()
    if args.wheel and (args.wheel.name != WHEEL or not args.wheel.is_file()):
        parser.error(f"请提供已下载的 {WHEEL}")

    root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "yazi-lan"
    bindir = Path(site.getuserbase()) / "bin"
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    config = Path(os.environ.get("YAZI_CONFIG_HOME") or config_home / "yazi")
    root = root.absolute()
    active = root / "current"
    command = bindir / "yazi-lan"
    entry = active / "bin/yazi-lan"
    env = root / "versions" / VERSION
    root.mkdir(parents=True, exist_ok=True)
    bindir.mkdir(parents=True, exist_ok=True)
    with (root / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if os.path.lexists(command) and not (command.is_symlink() and command.readlink() == entry):
            sys.exit(f"{command} 已由其他安装占用。请先移走该入口，再重新安装。")
        if active.exists() and not active.is_symlink():
            sys.exit(f"{active} 已被占用。请先确认其内容。")
        package = args.wheel.resolve() if args.wheel else f"{RELEASE}/{WHEEL}"
        install_environment(env, package)

        # A venv's scripts use absolute shebangs. Switch the link after the install succeeds.
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            candidate = Path(tmp) / "current"
            candidate.symlink_to(env)
            candidate.replace(active)
        if not link_if_free(command, entry):
            sys.exit(f"无法安装命令，{command} 已被占用。")
        try:
            install_plugin(active / "share/yazi-lan/yazi", config)
        except OSError as exc:
            sys.exit(f"{command} 已安装。写入 Yazi 配置 {config} 失败（{exc}），修正后可重新运行。")

    print(f"已安装 yazi-lan {VERSION}。运行 {command} --help 查看用法。")
    if str(bindir) not in os.get_exec_path():
        print(f"请将 {bindir} 加入 PATH，以便在终端及 Yazi 中使用 yazi-lan。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"安装失败（{exc}）")
