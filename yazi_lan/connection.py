"""Open the selected peer through the user's system OpenSSH client."""

import contextlib
import ipaddress
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
from dataclasses import dataclass

import paramiko


class ConnectionError(RuntimeError):
    pass


class Unreachable(ConnectionError):
    """The endpoint has no usable network path."""


class SftpChannel:
    """The OpenSSH subprocess supplies the authenticated subsystem byte stream."""

    def __init__(self, sock):
        self.send = sock.send
        self.recv = sock.recv
        self.close = sock.close
        self.gettimeout = sock.gettimeout
        self.settimeout = sock.settimeout

    def get_name(self):
        return "openssh-sftp"


@dataclass
class Peer:
    ssh: str
    lan: str
    interface: str
    netbird: str
    ssh_config: str

    def __post_init__(self):
        if not re.fullmatch(r"(?:[\w.][\w.$-]*@)?[\w][\w.-]*", self.ssh, re.ASCII):
            raise ValueError("目标应为 SSH 主机别名或 user@别名。IPv6 地址请填在 --lan 或 --netbird。")
        for address in (self.lan, self.netbird):
            if address:
                ipaddress.ip_address(address)
        if not (self.lan or self.netbird):
            raise ValueError("请指定 --lan IP --interface 网卡，或 --netbird IP。")
        if bool(self.lan) != bool(self.interface):
            raise ValueError("--lan 和 --interface 需要一起指定。")

    @property
    def base(self):
        return ["ssh", "-F", self.ssh_config] if self.ssh_config else ["ssh"]

    def host_key_alias(self):
        info = subprocess.run(self.base + ["-G", "--", self.ssh], capture_output=True,
                              text=True, errors="replace", check=True)
        options = dict(line.split(" ", 1) for line in info.stdout.splitlines() if " " in line)
        alias = options.get("hostkeyalias")
        if alias and alias != "none":
            return alias
        host = options["hostname"]
        port = options["port"]
        return host if port == "22" else f"[{host}]:{port}"


def lan_source(address, interface):
    if not shutil.which("ip"):
        raise ConnectionError("验证内网路由需要本机的 ip 命令（iproute2）。")

    def lookup(extra):
        result = subprocess.run(["ip", "-j", "route", "get", address, *extra],
                                capture_output=True, text=True)
        if result.returncode:
            raise Unreachable(result.stderr.strip() or f"没有到 {address} 的路由")
        routes = json.loads(result.stdout)
        if not routes or routes[0].get("dev") != interface:
            actual = routes[0].get("dev") if routes else "无"
            raise Unreachable(f"{address} 的出口是 {actual}，预期是 {interface}")
        return routes[0]

    route = lookup([])
    source = route.get("prefsrc") or route.get("src")
    if not source:
        raise Unreachable(f"无法确定 {interface} 的源地址")
    lookup(["from", source])
    return source


def netbird_label(address):
    label = "NetBird（连接类型未知）"
    if not shutil.which("netbird"):
        return label
    try:
        proc = subprocess.run(["netbird", "status", "--json"], capture_output=True, timeout=3)
        peers = json.loads(proc.stdout).get("peers", {})
        address = ipaddress.ip_address(address)
        for peer in peers.get("details", []):
            ips = [peer.get("netbirdIp"), peer.get("netbirdIpv6")]
            if any(ipaddress.ip_interface(ip).ip == address for ip in ips if ip):
                kind = str(peer.get("connectionType", "")).lower()
                return {"p2p": "NetBird 直连", "relayed": "NetBird 中转"}.get(kind, label)
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        pass
    return label


@dataclass
class Session:
    peer: Peer
    address: str
    label: str
    ssh: list
    sftp: paramiko.SFTPClient
    rsync: bool
    reason: str = ""


@contextlib.contextmanager
def open_endpoint(peer, address, source, label, alias):
    with tempfile.TemporaryDirectory(prefix="yazi-lan-", dir="/tmp") as tmp, \
            tempfile.TemporaryFile() as errors, contextlib.ExitStack() as stack:
        # Every transfer uses this session's address, host key and private control socket.
        ssh = peer.base + ["-T", "-S", os.path.join(tmp, "ssh"), "-oBatchMode=yes",
                           "-oStrictHostKeyChecking=yes", "-oUpdateHostKeys=no",
                           "-oForwardAgent=no", "-oClearAllForwardings=yes",
                           "-oProxyCommand=none", "-oProxyJump=none", "-oControlPersist=no",
                           "-oConnectTimeout=5", "-oServerAliveInterval=15",
                           "-oServerAliveCountMax=2", f"-oHostName={address}",
                           f"-oHostKeyAlias={alias}"]
        if source:
            ssh.extend(["-b", source])
        client, child = socket.socketpair()
        stack.callback(client.close)
        with child:
            proc = subprocess.Popen(ssh + ["-oControlMaster=yes", "-s", "--", peer.ssh, "sftp"],
                                    stdin=child, stdout=child, stderr=errors,
                                    start_new_session=True, env={**os.environ, "LC_ALL": "C"})

        def stop():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            proc.wait()

        stack.callback(stop)
        client.settimeout(12)
        try:
            sftp = paramiko.SFTPClient(SftpChannel(client))
        except (OSError, EOFError, paramiko.SSHException, paramiko.SFTPError) as exc:
            stop()
            errors.seek(0)
            detail = errors.read(8192).decode(errors="replace").strip() or str(exc)
            network_errors = ("connection timed out", "connection refused", "no route to host",
                              "network is unreachable", "operation timed out")
            if any(msg in detail.lower() for msg in network_errors):
                raise Unreachable(f"{address}: {detail}") from exc
            raise ConnectionError(f"SSH/SFTP {address}: {detail or '连接已关闭'}") from exc
        stack.callback(sftp.close)
        client.settimeout(None)
        ssh += ["-oControlMaster=no"]
        has_rsync = False
        if shutil.which("rsync"):
            try:
                probe = subprocess.run(ssh + ["--", peer.ssh, "rsync", "--version"],
                                       stdin=subprocess.DEVNULL, capture_output=True, timeout=12)
                has_rsync = probe.returncode == 0 and bool(re.search(rb"(?m)^rsync\s+version\s", probe.stdout))
            except subprocess.TimeoutExpired:
                pass  # Some subsystem-only servers do not answer exec requests.
        yield Session(peer, address, label, ssh, sftp, has_rsync)


@contextlib.contextmanager
def connect(peer):
    alias = peer.host_key_alias()
    reason = ""
    # Keep errors from the file browser or a running copy outside the fallback logic.
    with contextlib.ExitStack() as stack:
        session = None
        if peer.lan:
            try:
                source = lan_source(peer.lan, peer.interface)
                session = stack.enter_context(open_endpoint(
                    peer, peer.lan, source, f"内网 {peer.interface}", alias))
            except Unreachable as exc:
                if not peer.netbird:
                    raise
                reason = str(exc)
        if session is None:
            session = stack.enter_context(open_endpoint(
                peer, peer.netbird, None, "NetBird", alias))
            session.label = netbird_label(peer.netbird)
        session.reason = reason
        yield session


def remote_directory(sftp, path):
    path = sftp.normalize(path or ".")
    if not stat.S_ISDIR(sftp.stat(path).st_mode):
        raise ValueError(f"远端路径并非目录 {path!r}")
    return path
