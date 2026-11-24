import argparse
import curses
import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__
from .access import check_directions
from .connection import Peer, connect, remote_directory
from .topology import DEPLOYMENT, ROUTES, load_site, manage, public_key
from .ui import Browser, FILE_ERRORS


NODE_OPTIONS = {"lan", "netbird", "interface", "port", "ssh", "host_key_file"}
ADMIN_OPTIONS = {
    "node": NODE_OPTIONS | {"file"},
    "bind": NODE_OPTIONS | {"file", "source", "deployment", "ca_file"},
    "link": {"file", "route"},
    "unlink": {"file"},
    "remove_node": {"file"},
    "check_topology": {"file"},
    "setup_sync": {"deployment"},
}


def legacy_options(cli, argv):
    """Translate the old topology executable's command line into the public flags."""
    head = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    head.add_argument("--file")
    known, rest = head.parse_known_args(argv)
    if not rest:
        cli.error("请指定操作，运行 pto-yazi-lan --help 查看用法")
    if rest[0] in ("-h", "--help"):
        return ["--help"]
    operation, *options = rest
    if operation not in ("node", "bind", "link", "unlink", "remove-node", "check", "sync"):
        cli.error(f"未知的拓扑操作 {operation}")
    if operation == "check":
        operation = "check-topology"
    elif operation == "sync" and "--setup" in options:
        operation = "setup-sync"
        options.remove("--setup")
    if known.file is not None and operation not in ("sync", "setup-sync"):
        options.extend(["--file", known.file])
    return ["--" + operation, *options]


def arguments(legacy=False):
    cli = argparse.ArgumentParser(
        prog="pto-yazi-lan", allow_abbrev=False, add_help=False,
        usage="%(prog)s [操作] [选项]", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="双向 SSH 文件传输与服务器拓扑管理。省略操作时，在终端选择对端。",
        epilog="示例\n  pto-yazi-lan --sync --list\n  pto-yazi-lan --check --peer b\n"
               "  pto-yazi-lan --check-topology --file /srv/yazi/topology.json")
    actions = cli.add_argument_group("日常操作")
    single = actions.add_mutually_exclusive_group()
    single.add_argument("--update", action="store_true", help="升级程序")
    single.add_argument("--sync", action="store_true", help="从中心同步服务器列表，可和 --list 合用")
    actions.add_argument("--list", action="store_true", help="列出本机可连接的对端")
    single.add_argument("--check", action="store_true", help="检查对端 SSH 认证、线路和两栏的目录权限")
    single.add_argument("--enable-plugin", action="store_true", help="为当前用户启用 Yazi 插件")
    actions.add_argument("--version", action="version", version=f"pto-yazi-lan {__version__}", help="查看程序版本")
    actions.add_argument("-h", "--help", action="help", help="查看全部操作和选项")
    single = cli.add_argument_group("节点与拓扑操作").add_mutually_exclusive_group()
    single.add_argument("--node", metavar="NAME", help="在 --file 清单中新增或更新节点")
    single.add_argument("--bind", metavar="NAME", help="将本机绑定到 --source 清单中的节点")
    single.add_argument("--link", nargs=2, metavar=("A", "B"), help="在 --file 清单中按 --route 建立或更新双向连接")
    single.add_argument("--unlink", nargs=2, metavar=("A", "B"), help="从 --file 清单移除双向连接")
    single.add_argument("--remove-node", metavar="NAME", help="从 --file 清单移除节点及其连接")
    single.add_argument("--check-topology", action="store_true", help="验证 --file 指定的中心拓扑清单")
    single.add_argument("--setup-sync", action="store_true", help="设置整机定时同步并立即拉取清单")

    connection = cli.add_argument_group("对端与传输选项")
    connection.add_argument("--peer", metavar="NAME", help="直接选择对端，可写成 用户@节点")
    cli.add_argument("positional_peer", nargs="?", help=argparse.SUPPRESS)
    connection.add_argument("--user", help="使用自己的密钥登录对端的指定账户")
    connection.add_argument("--only-lan", action="store_true", help="本次仅使用机房内网")
    connection.add_argument("--jobs", type=int, choices=(1, 2), help="机房内网 rsync 的最大并发路数（默认 2）")
    connection.add_argument("--config", type=Path, help="个人对端配置文件")
    connection.add_argument("--ssh-config", help="OpenSSH 配置文件")
    connection.add_argument("--local-dir", help="本机起始目录，默认当前目录")
    connection.add_argument("--remote-dir", help="对端起始目录，默认登录目录")
    connection.add_argument("--select", action="append", metavar="PATH", help="在左栏预选文件或目录，可重复")
    connection.add_argument("--focus", metavar="PATH", help="打开面板时将左栏光标定位到指定文件")

    endpoints = cli.add_argument_group("SSH 端点（连接对端或登记节点时使用）")
    endpoints.add_argument("--lan", help="机房内网 IP")
    endpoints.add_argument("--netbird", help="NetBird IP")
    endpoints.add_argument("--interface", help="机房内网出口网卡")
    endpoints.add_argument("--port", type=int, help="SSH 端口")
    inventory = cli.add_argument_group("节点与拓扑选项")
    inventory.add_argument("--file", type=Path, help="中心拓扑 JSON 原件")
    inventory.add_argument("--source", "--topology", help="绑定时使用的 HTTPS 地址或本地绝对路径")
    inventory.add_argument("--deployment", type=Path, help="本机登记文件，默认 /etc/yazi-lan/deployment.json")
    inventory.add_argument("--ca-file", type=Path, help="中心 HTTPS 站点的私有 CA 文件")
    inventory.add_argument("--route", choices=ROUTES, help="双向连接使用的线路策略")
    inventory.add_argument("--ssh", help="登记节点使用的 OpenSSH 别名")
    inventory.add_argument("--host-key-file", type=Path, help="已核对的节点 SSH 主机公钥文件")

    argv = sys.argv[1:]
    if legacy or Path(sys.argv[0]).name in ("pto-yazi-lan-topology", "yazi-lan-topology"):
        argv = legacy_options(cli, argv)
    args = cli.parse_args(argv)
    positional = args.positional_peer
    del args.positional_peer
    if positional is not None:
        if args.peer is not None:
            cli.error("请只指定一个对端")
        args.peer = positional
    supplied = {key for key, value in vars(args).items() if value is not None and value is not False}
    args.action = next((key for key in ADMIN_OPTIONS if key in supplied), None)
    if args.action:
        extra = supplied - {args.action} - ADMIN_OPTIONS[args.action]
        if args.action not in ("bind", "setup_sync") and args.file is None:
            cli.error("该操作需要 --file 指定中心拓扑清单")
        if args.action == "bind" and args.source is None:
            cli.error("--bind 需要 --source 指定清单地址")
        if args.action == "link" and args.route is None:
            cli.error("--link 需要 --route 指定线路策略")
    else:
        extra = supplied & {"file", "source", "ca_file", "route", "ssh", "host_key_file"}
        if args.list and (args.check or args.enable_plugin or args.update):
            cli.error("--list 可单独使用或与 --sync 合用")
    if extra:
        cli.error("当前操作不接受 " + "、".join("--" + key.replace("_", "-") for key in sorted(extra)))
    args.local_dir = "." if args.local_dir is None else args.local_dir
    args.select = args.select or []
    return args


def choose_peer(profiles, conf):
    names = sorted(profiles)
    if not names:
        raise ValueError(f"请在 {conf} 中添加对端，或运行 pto-yazi-lan --peer <对端> --lan IP --interface 网卡")
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
    local, topology, neighbors = load_site(deployment, refresh=not args.sync)
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
                host_key=public_key(there["host_key"]) if there.get("host_key") else None,
                local_name=local, remote_name=name)
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
                args.ssh_config or saved.get("ssh_config"), port, remote_name=host), args.remote_dir or saved.get("remote_dir", "."), name, ""


def main(*, legacy=False):
    args = arguments(legacy=legacy)
    try:
        if args.action:
            manage(args)
            return 0
        if args.update:
            from .update import update
            return update()
        if args.sync:
            from .topology import sync_now
            result = sync_now(args.deployment or DEPLOYMENT)
            if result or not args.list:
                return result
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
                if args.jobs:
                    session.lan_jobs = min(session.lan_jobs, args.jobs)
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
                    marked = [os.path.abspath(os.path.join(local, p)) for p in args.select]
                    browser = Browser(local, path, marked, args.focus)
                if not curses.wrapper(browser.run, session):
                    return 0
    except KeyboardInterrupt:
        return 130
    except (*FILE_ERRORS, subprocess.SubprocessError, curses.error) as exc:
        print(f"pto-yazi-lan: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
