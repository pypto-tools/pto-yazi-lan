"""The administrator's machine and route inventory."""

import argparse
import base64
import copy
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shlex
import ssl
import stat
import subprocess
import sys
import tempfile
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


DEPLOYMENT = Path("/etc/yazi-lan/deployment.json")
SNAPSHOT = Path("/var/lib/yazi-lan/topology.json")
SYNC_META = "_yazi_lan_sync"
ROUTES = ("lan-first", "lan-only", "netbird-only")
NODE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,252}\Z")
MAX_SIZE = 2 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def public_key(text):
    require(isinstance(text, str) and len(text) <= 16384 and "\n" not in text.strip(),
            "主机公钥应为单行 OpenSSH 公钥")
    parts = text.split()
    require(len(parts) >= 2 and re.fullmatch(r"[a-zA-Z0-9@._-]+", parts[0]), "无效的 SSH 主机公钥")
    data = base64.b64decode(parts[1], validate=True)
    algorithm = parts[0].encode("ascii")
    require(data.startswith(len(algorithm).to_bytes(4, "big") + algorithm) and len(data) > len(algorithm) + 4,
            "SSH 主机公钥类型与内容不匹配")
    return " ".join(parts[:2])


def validate(topology):
    require(isinstance(topology, dict) and type(topology.get("schema")) is int and topology["schema"] == 1,
            "拓扑需要 schema 1")
    require(type(topology.get("revision", 0)) is int and topology.get("revision", 0) >= 0,
            "拓扑 revision 应为非负整数")
    nodes, links = topology.get("nodes"), topology.get("links")
    require(isinstance(nodes, dict) and isinstance(links, list), "拓扑需要 nodes 对象和 links 数组")
    for name, node in nodes.items():
        require(NODE_NAME.fullmatch(name) and isinstance(node, dict), f"无效的服务器记录 {name!r}")
        require(set(node) <= {"lan", "netbird", "port", "interface", "ssh", "host_key"}, f"{name} 有未知字段")
        require(node.get("lan") or node.get("netbird"), f"{name} 需登记一个机房或 NetBird IP")
        for field in ("lan", "netbird"):
            if field in node:
                require(isinstance(node[field], str), f"{name} 的 {field} 需要 IP 地址")
                ip = ipaddress.ip_address(node[field])
                require(not (ip.is_multicast or ip.is_unspecified or "%" in str(ip)), f"{name} 的 {field} 需要单播 IP")
        if node.get("lan") and node.get("netbird"):
            require(ipaddress.ip_address(node["lan"]) != ipaddress.ip_address(node["netbird"]),
                    f"{name} 的机房和 NetBird 地址应分别登记")
        port = node.get("port", 22)
        require(type(port) is int and 1 <= port <= 65535, f"{name} 的 SSH 端口无效")
        if "interface" in node:
            require(node.get("lan") and isinstance(node["interface"], str)
                    and re.fullmatch(r"[\w.:-]{1,15}", node["interface"], re.ASCII), f"{name} 的机房出口网卡无效")
        if "ssh" in node:
            require(isinstance(node["ssh"], str) and NODE_NAME.fullmatch(node["ssh"]), f"{name} 的 SSH 别名无效")
        if "host_key" in node:
            public_key(node["host_key"])
    seen = set()
    for link in links:
        require(isinstance(link, dict) and set(link) == {"nodes", "route"}, "每条连接需要 nodes 和 route")
        pair = link["nodes"]
        require(isinstance(pair, list) and len(pair) == 2 and all(isinstance(n, str) and n in nodes for n in pair)
                and pair[0] != pair[1], f"连接两端必须是已登记的不同服务器 {pair!r}")
        key = tuple(sorted(pair))
        require(key not in seen and link["route"] in ROUTES, f"{key} 的线路重复或类型无效")
        seen.add(key)
        a, b = (nodes[n] for n in pair)
        common = [f for f in ("lan", "netbird") if a.get(f) and b.get(f)
                  and ipaddress.ip_address(a[f]).version == ipaddress.ip_address(b[f]).version]
        required = {"lan-only": "lan", "netbird-only": "netbird"}.get(link["route"])
        require(required in common if required else bool(common), f"{key} 缺少该线路所需的同地址族端点")
    return topology


def read_json(stream, limit=MAX_SIZE, length=None):
    if length is not None:
        require(0 <= length <= limit, "配置文件大小无效或超过限制")
    data = stream.read(limit + 1)
    require(len(data) <= limit, "配置文件超过大小限制")
    require(length is None or len(data) == length, "拓扑下载不完整")
    return json.loads(data)


def source_location(source):
    require(isinstance(source, str) and source, "请指定拓扑的绝对文件路径或 HTTPS 地址")
    if Path(source).is_absolute():
        return source
    url = urlsplit(source)
    require(url.scheme == "https" and url.hostname and not url.username and not url.password and not url.fragment,
            "网络拓扑来源应为 HTTPS 地址")
    return source


class SecureRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require(urlsplit(newurl).scheme == "https", "拓扑重定向需要 HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_binding(path):
    with path.open("rb") as f:
        site = read_json(f)
    require(isinstance(site, dict) and isinstance(site.get("node"), str) and NODE_NAME.fullmatch(site["node"]),
            f"无效的本机登记文件 {path}")
    source_location(site.get("topology"))
    require(site.get("ca_file") is None or isinstance(site["ca_file"], str), "ca_file 应为 CA 文件路径")
    return site


def system_binding(path):
    return path.resolve() == DEPLOYMENT.resolve()


def snapshot_path(path):
    if system_binding(path):
        return SNAPSHOT
    return path.parent / (path.name + ".d") / "topology.json"


def origin_id(site):
    fields = [site["topology"], site["node"], site.get("ca_file")]
    return hashlib.sha256(json.dumps(fields).encode("utf-8")).hexdigest()


def read_snapshot(path, site):
    with path.open("rb") as f:
        topology = validate(read_json(f, limit=MAX_SIZE + 4096))
    meta = topology.pop(SYNC_META, None)
    require(isinstance(meta, dict) and meta.get("origin") == origin_id(site),
            "本地副本与当前登记的中心或节点不匹配")
    return topology, meta.get("etag")


def fetch(site, etag):
    request = Request(site["topology"], headers={"Accept": "application/json", "Cache-Control": "no-cache"})
    if etag:
        request.add_header("If-None-Match", etag)
    try:
        tls = ssl.create_default_context(cafile=site.get("ca_file"))
        opener = build_opener(HTTPSHandler(context=tls), SecureRedirect())
        try:
            response = opener.open(request, timeout=5)
        except HTTPError as exc:
            with exc:
                if exc.code == 304 and etag:
                    return None, etag
                raise
        with response as f:
            require(f.status == 200, f"中心返回 HTTP {f.status}，需要完整的拓扑文件")
            length = f.headers.get("Content-Length")
            topology = validate(read_json(f, length=int(length) if length is not None else None))
            topology.pop(SYNC_META, None)
            tag = f.headers.get("ETag", "")
            return topology, tag if valid_etag(tag) else None
    except (OSError, HTTPException, ValueError, RecursionError) as exc:
        raise ValueError(f"拉取中心 {urlsplit(site['topology']).hostname} 的拓扑失败（{exc}）") from exc


def valid_etag(tag):
    return isinstance(tag, str) and len(tag) <= 512 and re.fullmatch(r'(W/)?"[!#-~]*"', tag)


def sync_site(path):
    site = read_binding(path)
    if Path(site["topology"]).is_absolute():
        with Path(site["topology"]).open("rb") as f:
            return validate(read_json(f))
    dest = snapshot_path(path)
    make_parent(dest, shared=system_binding(path))
    with dest.with_name(".sync.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        require(read_binding(path) == site, "本机登记刚刚变更，请重新同步")
        try:
            prior, etag = read_snapshot(dest, site)
        except (OSError, ValueError, RecursionError):
            prior, etag = None, None
        topology, new_etag = fetch(site, etag if valid_etag(etag) else None)
        require(read_binding(path) == site, "下载期间本机登记发生变更，请重新同步")
        if topology is None:
            return prior
        if topology != prior or new_etag != etag:
            meta = {"origin": origin_id(site), "etag": new_etag}
            write_json(dest, {**topology, SYNC_META: meta}, compact=True)
        return topology


def load_site(path):
    site = read_binding(path)
    source = Path(site["topology"])
    if source.is_absolute():
        with source.open("rb") as f:
            topology = validate(read_json(f))
    else:
        dest = snapshot_path(path)
        if not system_binding(path):
            # Standalone bindings have no system timer. Refresh them on open and reconnect.
            try:
                sync_site(path)
            except (OSError, ValueError) as exc:
                print(f"拓扑同步失败，将尝试读取本地副本（{exc}）", file=sys.stderr)
        site = read_binding(path)
        try:
            topology, _ = read_snapshot(dest, site)
        except (OSError, ValueError, RecursionError) as exc:
            command = "yazi-lan-topology sync"
            if not system_binding(path):
                command += " --deployment " + shlex.quote(str(path))
            raise ValueError(f"没有可用的本地拓扑 {dest}，请运行 {command}（{exc}）") from exc
    local = site["node"]
    require(local in topology["nodes"], f"拓扑未登记本机 {local!r}")
    neighbors = {}
    for link in topology["links"]:
        a, b = link["nodes"]
        if local in (a, b):
            neighbors[b if a == local else a] = link["route"]
    return local, topology, neighbors


def make_parent(path, *, shared):
    try:
        path.parent.mkdir(parents=True)
    except FileExistsError:
        return
    if shared:
        path.parent.chmod(0o755)


def write_json(path, data, *, compact=False):
    make_parent(path, shared=path == DEPLOYMENT)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as f:
        tmp = Path(f.name)
        try:
            json.dump(data, f, ensure_ascii=False, indent=None if compact else 2,
                      separators=(",", ":") if compact else None)
            f.write("\n")
            f.flush()
            os.fchmod(f.fileno(), mode)
            os.fsync(f.fileno())
            os.replace(tmp, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            tmp.unlink(missing_ok=True)


def edit(path, args):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name("." + path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            with path.open("rb") as f:
                topology = validate(read_json(f))
        else:
            topology = {"schema": 1, "revision": 0, "nodes": {}, "links": []}
        before = copy.deepcopy(topology)
        if args.action in ("node", "bind"):
            node = topology["nodes"].setdefault(args.name, {})
            for field in ("lan", "netbird", "port", "interface", "ssh"):
                value = getattr(args, field)
                if value is not None:
                    node[field] = value
            if args.host_key_file:
                node["host_key"] = public_key(args.host_key_file.read_text(encoding="ascii"))
        elif args.action == "remove-node":
            require(args.name in topology["nodes"], f"服务器 {args.name!r} 尚未登记")
            del topology["nodes"][args.name]
            topology["links"] = [link for link in topology["links"] if args.name not in link["nodes"]]
        else:
            names = sorted([args.a, args.b])
            for link in topology["links"]:
                if sorted(link["nodes"]) == names:
                    if args.action == "unlink":
                        topology["links"].remove(link)
                    else:
                        link["route"] = args.route
                    break
            else:
                if args.action == "link":
                    topology["links"].append({"nodes": names, "route": args.route})
                else:
                    raise ValueError(f"拓扑中没有连接 {names}")
        validate(topology)
        if topology != before:
            topology["revision"] = topology.get("revision", 0) + 1
            write_json(path, topology)
        return topology.get("revision", 0)


def setup_pull(path):
    site = read_binding(path)
    if Path(site["topology"]).is_absolute():
        print(f"直接使用本地清单 {site['topology']}")
        return
    if system_binding(path):
        from . import schedule
        try:
            print(schedule.enable())
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            print(f"未能启用自动同步（{exc}）。可安排每分钟以管理员身份运行 yazi-lan-topology sync。",
                  file=sys.stderr)
    try:
        topology = sync_site(path)
        print(f"{snapshot_path(path)}  修订 {topology.get('revision', 0)}")
    except (OSError, ValueError) as exc:
        print(f"本次未能同步（{exc}）。已有的有效副本可继续使用。首次接入需要成功拉取一次。", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="登记服务器，更新集中管理的传输拓扑")
    parser.add_argument("--file", type=Path, help="管理端拓扑 JSON 文件")
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("node", "bind"):
        node = commands.add_parser(action, help="登记服务器" if action == "node" else "将本机接入集中拓扑")
        node.add_argument("name")
        node.add_argument("--lan")
        node.add_argument("--netbird")
        node.add_argument("--interface")
        node.add_argument("--port", type=int)
        node.add_argument("--ssh", help="OpenSSH 别名")
        node.add_argument("--host-key-file", type=Path, help="管理员已核对的 SSH 主机公钥")
        if action == "bind":
            node.add_argument("--source", required=True)
            node.add_argument("--deployment", type=Path, default=DEPLOYMENT)
            node.add_argument("--ca-file", type=Path)
    for action in ("link", "unlink"):
        sub = commands.add_parser(action)
        sub.add_argument("a")
        sub.add_argument("b")
        if action == "link":
            sub.add_argument("--route", choices=ROUTES, required=True)
    commands.add_parser("remove-node", help="移除服务器及其连接").add_argument("name")
    commands.add_parser("check", help="验证拓扑文件")
    sync = commands.add_parser("sync", help="从中心更新本机的拓扑副本")
    sync.add_argument("--deployment", type=Path, default=DEPLOYMENT)
    sync.add_argument("--setup", action="store_true", help="为整机 HTTPS 部署设置定时同步并立即拉取")
    args = parser.parse_args()
    try:
        if args.action == "bind":
            require(NODE_NAME.fullmatch(args.name), "本机名称格式无效")
            source = source_location(args.source)
            site = {"node": args.name, "topology": source}
            if args.ca_file:
                ssl.create_default_context(cafile=str(args.ca_file))
                site["ca_file"] = str(args.ca_file.resolve())
            if any(getattr(args, key) is not None for key in ("lan", "netbird", "port", "interface", "ssh", "host_key_file")):
                backing = args.file or (Path(source) if Path(source).is_absolute() else None)
                require(backing is not None, "HTTPS 清单请在管理端用 node 登记，或用 --file 指定可写的清单原文件")
                print(f"{backing}  修订 {edit(backing, args)}")
            write_json(args.deployment, site)
            print(f"{args.name} 已登记，本机部署配置为 {args.deployment}")
            setup_pull(args.deployment)
            return
        if args.action == "sync":
            if args.setup:
                setup_pull(args.deployment)
            else:
                topology = sync_site(args.deployment)
                print(f"本机拓扑修订 {topology.get('revision', 0)}")
            return
        if not args.file:
            parser.error("此操作需要 --file 指定管理端清单")
        if args.action == "check":
            with args.file.open("rb") as f:
                rev = validate(read_json(f)).get("revision", 0)
        else:
            rev = edit(args.file, args)
        print(f"{args.file}  修订 {rev}")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
