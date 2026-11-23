import argparse
import curses
import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path

from .access import check_directions
from .connection import Peer, connect, remote_directory
from .topology import DEPLOYMENT, load_site, public_key
from .ui import Browser, FILE_ERRORS


def arguments():
    cli = argparse.ArgumentParser(description="在本机和对端 SSH 服务器之间双向复制文件")
    cli.add_argument("peer", nargs="?", help="对端配置名或 SSH 别名，省略时使用本机的对端列表")
    cli.add_argument("--lan", help="对端的机房内网 IP")
    cli.add_argument("--interface", help="本机的机房内网出口网卡")
    cli.add_argument("--netbird", help="对端可用于回退的 NetBird IP")
    cli.add_argument("--port", type=int, help="对端 SSH 端口")
    cli.add_argument("--user", help="使用自己的密钥登录对端的指定账户，默认沿用 OpenSSH 设置")
    cli.add_argument("--only-lan", action="store_true", help="本次仅使用内网")
    cli.add_argument("--config", type=Path, help="服务器配置 JSON 文件")
    cli.add_argument("--deployment", type=Path, help="本机部署配置，默认 /etc/yazi-lan/deployment.json")
    cli.add_argument("--list", action="store_true", help="列出本机拓扑中的对端和路线")
    cli.add_argument("--enable-plugin", action="store_true", help="为当前用户启用已安装的 Yazi 插件")
    cli.add_argument("--ssh-config", help="OpenSSH 配置文件，默认使用系统 ssh 的配置")
    cli.add_argument("--local-dir", default=".", help="本机的起始目录")
    cli.add_argument("--remote-dir", help="对端的起始目录，默认登录目录")
    cli.add_argument("--select", action="append", default=[], metavar="PATH", help="在左栏预选文件或目录")
    cli.add_argument("--focus", help=argparse.SUPPRESS)
    cli.add_argument("--check", action="store_true", help="验证 SSH 身份、线路和两栏目录的新建权限")
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


def managed_profile(args, deployment, requested):
    if any(value is not None for value in (args.config, args.lan, args.netbird, args.interface, args.port)):
        raise ValueError("本机已登记，地址、端口和线路由管理端拓扑统一配置。")
    local, topology, neighbors = load_site(deployment)
    nodes = topology["nodes"]
    revision = f"拓扑 {topology.get('revision', 0)}"
    if args.list:
        print(f"{local}  {revision}")
        for name, route in sorted(neighbors.items()):
            print(f"{name}  {route}  SSH {nodes[name].get('port', 22)}  选择后认证")
        if not neighbors:
            print("管理端尚未为本机配置连接")
        return None
    if not neighbors:
        raise ValueError(f"{revision} 中本机 {local} 没有可选对端")
    selected = requested or choose_peer(neighbors, deployment)
    user, _, name = selected.rpartition("@")
    if name not in neighbors:
        raise ValueError(f"{revision} 未配置 {local} 与 {name} 的连接，请用 --list 查看。")
    route = neighbors[name]
    here, there = nodes[local], nodes[name]

    def available(field):
        return here.get(field) and there.get(field) and (
            ipaddress.ip_address(here[field]).version == ipaddress.ip_address(there[field]).version)

    lan = there.get("lan") if route != "netbird-only" and available("lan") else None
    mesh = there.get("netbird") if route != "lan-only" and available("netbird") else None
    if args.only_lan:
        if not lan:
            raise ValueError(f"{name} 没有可用的已登记内网线路")
        mesh = None
    ssh = there.get("ssh", name)
    if args.user or user:
        ssh = f"{args.user or user}@{ssh}"
    peer = Peer(ssh, lan, here.get("interface") if lan else None, mesh, args.ssh_config,
                port=there.get("port", 22), local_lan=here.get("lan") if lan else None,
                local_netbird=here.get("netbird") if mesh else None,
                host_key=public_key(there["host_key"]) if there.get("host_key") else None)
    return peer, args.remote_dir or ".", selected, revision


def profile(args, previous=None):
    requested = previous or args.peer or os.environ.get("YAZI_LAN_PEER")
    deployment = args.deployment or DEPLOYMENT
    if args.deployment or deployment.exists():
        return managed_profile(args, deployment, requested)
    conf = args.config or Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config") / "yazi-lan/hosts.json"
    profiles = {}
    if args.config or conf.exists():
        profiles = json.loads(conf.read_text(encoding="utf-8"))
        if not isinstance(profiles, dict):
            raise ValueError(f"服务器配置格式无效 {conf}")
    if args.list:
        for name in sorted(profiles):
            print(name)
        return None
    name = requested or choose_peer(profiles, conf)
    login, separator, host = name.rpartition("@")
    saved = profiles.get(name, profiles.get(host, {}))
    if not isinstance(saved, dict) or any(not isinstance(value, str) for key, value in saved.items() if key != "port"):
        raise ValueError(f"{name} 的配置应为包含字符串字段的对象")
    ssh = saved.get("ssh", name if name in profiles else host)
    user = args.user or (login if separator and name not in profiles else None)
    if user:
        ssh = f"{user}@{ssh.rsplit('@', 1)[-1]}"
    port = args.port if args.port is not None else saved.get("port")
    if isinstance(port, str) and port.isascii() and port.isdigit():
        port = int(port)
    lan = args.lan or saved.get("lan")
    if args.only_lan and not lan:
        raise ValueError("--only-lan 需要配置机房内网地址和网卡")
    return Peer(ssh, lan, args.interface or saved.get("interface"),
                None if args.only_lan else args.netbird or saved.get("netbird"),
                args.ssh_config or saved.get("ssh_config"), port), args.remote_dir or saved.get("remote_dir", "."), name, ""


def main():
    args = arguments()
    try:
        if args.enable_plugin:
            from .plugin import enable_plugin
            enable_plugin()
            return 0
        browser = None
        selected = None
        while True:
            target = profile(args, selected)
            if target is None:
                return 0
            peer, remote, selected, revision = target
            with connect(peer) as session:
                session.revision = revision
                path = remote_directory(session.sftp, browser.panes[1].path if browser else remote)
                if args.check:
                    local = os.path.realpath(os.path.expanduser(args.local_dir))
                    with os.scandir(local) as listing:
                        next(listing, None)
                    session.sftp.listdir_attr(path)
                    if session.reason:
                        print(session.reason, file=sys.stderr)
                    print(f"{revision}  SSH 已认证 {session.login}  {session.label}  {session.address}  {path!r}  "
                          f"{'rsync over SSH' if session.rsync else 'SFTP'}")
                    checks = check_directions(local, path, session.sftp)
                    for _, result in checks:
                        print(result)
                    return 0 if all(ok for ok, _ in checks) else 1
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
