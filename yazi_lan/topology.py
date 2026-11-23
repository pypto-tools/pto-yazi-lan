"""The administrator's machine and route inventory."""

import argparse
import base64
import copy
import fcntl
import ipaddress
import json
import os
import re
import ssl
import stat
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, build_opener


DEPLOYMENT = Path("/etc/yazi-lan/deployment.json")
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


def read_json(stream):
    data = stream.read(MAX_SIZE + 1)
    require(len(data) <= MAX_SIZE, "配置文件超过 2 MiB")
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


def load_site(path):
    with path.open("rb") as f:
        site = read_json(f)
    require(isinstance(site, dict) and isinstance(site.get("node"), str) and NODE_NAME.fullmatch(site["node"]),
            f"无效的本机登记文件 {path}")
    source = source_location(site.get("topology"))
    if Path(source).is_absolute():
        with Path(source).open("rb") as f:
            topology = validate(read_json(f))
    else:
        ca_file = site.get("ca_file")
        require(ca_file is None or isinstance(ca_file, str), "ca_file 应为 CA 文件路径")
        try:
            tls = ssl.create_default_context(cafile=ca_file)
            opener = build_opener(HTTPSHandler(context=tls), SecureRedirect())
            with opener.open(source, timeout=5) as f:
                topology = validate(read_json(f))
        except OSError as exc:
            raise ValueError(f"无法读取管理端 {urlsplit(source).hostname} 的拓扑（{exc}）") from exc
    local = site["node"]
    require(local in topology["nodes"], f"拓扑未登记本机 {local!r}")
    neighbors = {}
    for link in topology["links"]:
        a, b = link["nodes"]
        if local in (a, b):
            neighbors[b if a == local else a] = link["route"]
    return local, topology, neighbors


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as f:
        tmp = Path(f.name)
        try:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fchmod(f.fileno(), mode)
            os.fsync(f.fileno())
            os.replace(tmp, path)
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
