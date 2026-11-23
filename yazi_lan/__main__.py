import argparse
import curses
import json
import os
import subprocess
import sys
from pathlib import Path

from .connection import Peer, connect, remote_directory
from .ui import Browser, FILE_ERRORS


def arguments():
    cli = argparse.ArgumentParser(description="在本机和对端 SSH 服务器之间双向复制文件")
    cli.add_argument("peer", nargs="?", help="对端配置名或 SSH 别名，省略时使用本机的对端列表")
    cli.add_argument("--lan", help="对端的机房内网 IP")
    cli.add_argument("--interface", help="本机的机房内网出口网卡")
    cli.add_argument("--netbird", help="对端可用于回退的 NetBird IP")
    cli.add_argument("--only-lan", action="store_true", help="本次仅使用内网")
    cli.add_argument("--config", type=Path, help="服务器配置 JSON 文件")
    cli.add_argument("--ssh-config", help="OpenSSH 配置文件，默认使用系统 ssh 的配置")
    cli.add_argument("--local-dir", default=".", help="本机的起始目录")
    cli.add_argument("--remote-dir", help="对端的起始目录，默认登录目录")
    cli.add_argument("--select", action="append", default=[], metavar="PATH", help="在左栏预选文件或目录")
    cli.add_argument("--focus", help=argparse.SUPPRESS)
    cli.add_argument("--check", action="store_true", help="验证路由、SSH 身份、SFTP 目录和传输方式")
    return cli.parse_args()


def choose_peer(profiles, conf):
    names = sorted(profiles)
    if not names:
        raise ValueError(f"请在 {conf} 中添加对端，或运行 yazi-lan <对端> --lan IP --interface 网卡")
    if len(names) == 1:
        return names[0]
    if not sys.stdin.isatty():
        raise ValueError("请指定要连接的对端，可选 " + "、".join(names))

    print("选择要连接的对端")
    for index, name in enumerate(names, start=1):
        print(f"  {index}. {name}")
    while True:
        print("输入编号，按回车退出  ", end="", flush=True)
        answer = input().strip()
        if not answer:
            raise KeyboardInterrupt
        if answer.isascii() and answer.isdecimal():
            index = int(answer)
            if 1 <= index <= len(names):
                return names[index - 1]


def profile(args):
    conf = args.config or Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config") / "yazi-lan/hosts.json"
    profiles = {}
    if args.config or conf.exists():
        profiles = json.loads(conf.read_text(encoding="utf-8"))
        if not isinstance(profiles, dict):
            raise ValueError(f"服务器配置格式无效 {conf}")
    name = args.peer or os.environ.get("YAZI_LAN_PEER") or choose_peer(profiles, conf)
    saved = profiles.get(name, {})
    if not isinstance(saved, dict) or any(not isinstance(value, str) for value in saved.values()):
        raise ValueError(f"{name} 的配置应为包含字符串字段的对象")
    lan = args.lan or saved.get("lan")
    if args.only_lan and not lan:
        raise ValueError("--only-lan 需要配置机房内网地址和网卡")
    return Peer(saved.get("ssh", name), lan, args.interface or saved.get("interface"),
                None if args.only_lan else args.netbird or saved.get("netbird"),
                args.ssh_config or saved.get("ssh_config")), args.remote_dir or saved.get("remote_dir", ".")


def main():
    args = arguments()
    try:
        peer, remote = profile(args)
        browser = None
        while True:
            with connect(peer) as session:
                path = remote_directory(session.sftp, browser.panes[1].path if browser else remote)
                if args.check:
                    # Also check listing permission on the directory the user selected.
                    session.sftp.listdir_attr(path)
                    if session.reason:
                        print(session.reason, file=sys.stderr)
                    print(f"{peer.ssh}  {session.label}  {session.address}  {path!r}  "
                          f"{'rsync over SSH' if session.rsync else 'SFTP'}")
                    return 0
                if browser is None:
                    local = os.path.realpath(os.path.expanduser(args.local_dir))
                    selected = [os.path.abspath(os.path.join(local, p)) for p in args.select]
                    browser = Browser(local, path, selected, args.focus)
                if not curses.wrapper(browser.run, session):
                    return 0
    except KeyboardInterrupt:
        return 130
    except (*FILE_ERRORS, subprocess.SubprocessError, curses.error) as exc:
        print(f"yazi-lan: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
