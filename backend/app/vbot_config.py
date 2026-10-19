"""真机连接配置与只读自检。配置落盘 vbot_config.json；自检全部只读，不碰控制接口。"""
import json
import os
import socket
import subprocess
from pathlib import Path

DEFAULTS = {
    "dog_ip": "192.168.126.2",
    "ssh_user": "vbot",
    "zenoh_endpoint": "tcp/192.168.1.2:7447",
    "ros_domain_id": 178,
    "map_name": "",
    "notes": "",
}
CONFIG_PATH = Path(os.getenv("VBOT_CONFIG", str(Path(__file__).resolve().parents[2] / "vbot_config.json")))


def load():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except ValueError:
            pass
    return cfg


def save(cfg):
    merged = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    CONFIG_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    return merged


def tcp_probe(host, port, timeout=3., expect_banner=None):
    """expect_banner: 连上后必须读到该前缀才算真服务（防透明代理假阳性）。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            if expect_banner:
                sock.settimeout(timeout)
                data = sock.recv(64)
                if not data.startswith(expect_banner):
                    return False, f"TCP 通但横幅不对({data[:16]!r})：可能是代理/防火墙代答，非真服务"
            return True, "connect ok"
    except OSError as exc:
        return False, str(exc)


def ssh_run(cfg, cmd, timeout=20):
    target = f"{cfg['ssh_user']}@{cfg['dog_ip']}"
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
         "-o", "StrictHostKeyChecking=accept-new", target, cmd],
        capture_output=True, text=True, timeout=timeout)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()[:400]


def local_subnets():
    """返回 (本机所有 /24 网段 + 直连固定网段, 本机自身 IP 集合)。换 WiFi 后自动跟随。"""
    subs = {"192.168.126.0"}
    own = set()
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 4 or parts[1] == "lo":
                continue
            ip, _, prefix = parts[3].partition("/")
            own.add(ip)
            if prefix and int(prefix) <= 24:
                subs.add(".".join(ip.split(".")[:3]) + ".0")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return sorted(subs), own


def scan_for_dog(cfg, timeout=0.4):
    """扫 SSH 横幅找狗（换网后狗 IP 可能变）。并行探测，只读。"""
    from concurrent.futures import ThreadPoolExecutor
    candidates = []
    subs, own = local_subnets()
    targets = [(f"{sub}.{i}", 22) for sub in subs for i in range(1, 255) if f"{sub}.{i}" not in own]

    def probe(host_port):
        host, port = host_port
        ok, _ = tcp_probe(host, port, timeout=timeout, expect_banner=b"SSH-")
        return host if ok else None

    with ThreadPoolExecutor(max_workers=64) as pool:
        for hit in pool.map(probe, targets):
            if hit:
                candidates.append(hit)
    known = cfg.get("dog_ip")
    candidates.sort(key=lambda ip: (ip != known, ip))
    return candidates


def run_checks(cfg):
    checks = []
    ok_ssh, detail = tcp_probe(cfg["dog_ip"], 22, expect_banner=b"SSH-")
    checks.append({"name": "SSH 横幅（狗开机+网线/桥接通）", "ok": ok_ssh, "detail": detail})
    endpoint = cfg["zenoh_endpoint"].replace("tcp/", "")
    host, _, port = endpoint.rpartition(":")
    ok_z, detail_z = tcp_probe(host or cfg["dog_ip"], port or 7447)
    checks.append({"name": f"Zenoh {cfg['zenoh_endpoint']}", "ok": ok_z, "detail": detail_z})
    if not ok_ssh:
        checks.append({"name": "SSH 密钥免密", "ok": False,
                       "detail": f"先修网络；之后配密钥: ssh-copy-id {cfg['ssh_user']}@{cfg['dog_ip']}"})
        return checks
    ok_key, detail_key = ssh_run(cfg, "echo KEYAUTH_OK")
    checks.append({"name": "SSH 密钥免密（BatchMode）", "ok": ok_key and "KEYAUTH_OK" in detail_key,
                   "detail": detail_key or f"未通：请在本机执行 ssh-copy-id {cfg['ssh_user']}@{cfg['dog_ip']}"})
    if not ok_key:
        return checks
    ok_cli, detail_cli = ssh_run(cfg, "source /opt/vita/ros/humble/setup.bash; command -v aorta && command -v ros2")
    checks.append({"name": "狗上 aorta/ros2 CLI", "ok": ok_cli, "detail": detail_cli})
    ok_peer, detail_peer = ssh_run(cfg, "test -r /opt/vita/aorta/edu/edu_session_peer.json5 && echo PEER_OK || echo PEER_MISSING(仅client模式,非白名单topic静默丢弃)")
    checks.append({"name": "EDU 会话 ACL(peer 文件)", "ok": ok_peer and "PEER_OK" in detail_peer, "detail": detail_peer})
    ok_ver, detail_ver = ssh_run(cfg, "cat /etc/version 2>/dev/null | head -1")
    checks.append({"name": "系统镜像版本(≠应用版本,记录用)", "ok": ok_ver, "detail": detail_ver})
    return checks
