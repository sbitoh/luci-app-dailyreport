#!/usr/bin/env python3
"""
OpenWrt Daily Report - Traffic Analysis Engine
Extracts WAN-only traffic per LAN host and top visited sites.
Supports dual-track verification: Physical WAN interface delta vs LAN client sum.
Compatible with nlbwmon and standard OpenWrt environments.
"""
import json
import subprocess
import os
import sys
import time
import re
import urllib.request

def normalize_target_domain(raw_target, lang="zh"):
    """
    智能聚类与友好域名归并：
    将复杂的底层 CDN 切片域名、音视频碎片分发域名、以及代理/游戏节点，
    归拢到对应的主流服务主品牌名下，确保统计报表一目了然。
    """
    if not raw_target:
        return "未知 (Unknown)"
    t = raw_target.lower().strip()

    # 1. 哔哩哔哩 (Bilibili)
    if any(k in t for k in ["bilibili.com", "bilivideo.cn", "bilivideo.com", "biliapi.net", "hdslb.com", "mcdn.bilivideo.cn"]):
        return "Bilibili" if lang == "en" else "哔哩哔哩 (Bilibili)"

    # 2. YouTube / Google 视频与服务
    if any(k in t for k in ["youtube.com", "googlevideo.com", "ytimg.com", "youtu.be"]):
        return "YouTube" if lang == "en" else "YouTube 视频"
    if any(k in t for k in ["google.com", "googleapis.com", "gstatic.com", "googleusercontent.com", "1e100.net"]):
        return "Google Services" if lang == "en" else "Google 服务"

    # 3. 抖音 / 剪映 / 字节跳动视频
    if any(k in t for k in ["douyinvod.com", "douyinpic.com", "douyin.com", "bytegoofy.com", "bytedance.com", "snssdk.com"]):
        return "Douyin / TikTok" if lang == "en" else "抖音 / 字节跳动视频"

    # 4. 腾讯视频 / 微信 / 腾讯云 CDN
    if any(k in t for k in ["video.qq.com", "qpic.cn", "gtimg.cn", "weixin.qq.com", "wechat.com", "tenpay.com", "tencent.com", "cdnhwc2.com", "myqcloud.com", "tdnsvod"]):
        return "Tencent / WeChat Stream" if lang == "en" else "腾讯 / 微信 / 视频流媒体"

    # 5. 阿里巴巴 / 优酷 / 阿里 CDN
    if any(k in t for k in ["youku.com", "ykimg.com", "taobao.com", "alipay.com", "aliyuncs.com", "alibabadns.com", "alicdn.com"]):
        return "Alibaba / Youku CDN" if lang == "en" else "阿里 / 优酷 / 媒体分发网络"

    # 6. 爱奇艺
    if any(k in t for k in ["iqiyi.com", "qiyi.com", "iqiyipic.com"]):
        return "iQIYI" if lang == "en" else "爱奇艺 (iQIYI)"

    # 7. Steam / Dota 2 / 游戏分发
    if any(k in t for k in ["dota2.com", "steampowered.com", "steamcommunity.com", "steamcontent.com", "steamstatic.com", "valvesoftware.com"]):
        return "Steam / Dota 2" if lang == "en" else "Steam / Dota 2 游戏平台"

    # 8. GitHub / 开发者源
    if any(k in t for k in ["github.com", "githubusercontent.com", "github.io", "ghcr.io"]):
        return "GitHub" if lang == "en" else "GitHub 源码与容器库"

    # 9. Docker / 容器镜像
    if any(k in t for k in ["docker.com", "docker.io", "cloudfront.docker.com"]):
        return "Docker Registry" if lang == "en" else "Docker 镜像源"

    # 10. 小红书
    if "xiaohongshu.com" in t or "xhscdn.com" in t:
        return "Xiaohongshu" if lang == "en" else "小红书 (Xiaohongshu)"

    # 11. 百度
    if any(k in t for k in ["baidu.com", "bdstatic.com", "baidupcs.com"]):
        return "Baidu" if lang == "en" else "百度服务"

    # 12. 苹果服务
    if any(k in t for k in ["apple.com", "icloud.com", "aaplimg.com", "mzstatic.com"]):
        return "Apple / iCloud" if lang == "en" else "Apple / iCloud 服务"

    # 13. 微软
    if any(k in t for k in ["microsoft.com", "windowsupdate.com", "live.com", "office.com", "azure.com"]):
        return "Microsoft / Windows" if lang == "en" else "Microsoft / Windows 更新"

    # 14. 常见的运营商/主流公共 CDN 汇总
    if any(k in t for k in ["bsgslb.cn", "cloudjscdn.cn", "gslb4.com", "glb4.com", "ctlcdn.cn", "ourwebpic.com"]):
        return "Streaming Media CDN" if lang == "en" else "运营商视频与流媒体 CDN"

    # 15. 若是纯 IP，按已知网段做友好提示
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", t):
        if t in ["180.163.126.1", "111.206.90.3"]:
            return f"{t} (Backbone CDN)" if lang == "en" else f"{t} (骨干节点高速分发)"
        return t

    # 16. 其他常规多级域名：提取二级主域名（如 a.b.example.com -> example.com）
    parts = t.split(".")
    if len(parts) >= 2:
        if len(parts) >= 3 and parts[-2] in ["com", "net", "org", "gov", "edu"] and parts[-1] in ["cn", "hk", "tw", "uk"]:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])

    return raw_target

def fmt_bytes(b):
    try:
        b = float(b)
    except Exception:
        return "0 B"
    if b >= 1073741824:
        return f"{b / 1073741824:.2f} GB"
    elif b >= 1048576:
        return f"{b / 1048576:.2f} MB"
    elif b >= 1024:
        return f"{b / 1024:.2f} KB"
    else:
        return f"{int(b)} B"

def get_uci_option(package, section, option, default=""):
    try:
        res = subprocess.check_output(["uci", "-q", "get", f"{package}.{section}.{option}"]).decode("utf-8").strip()
        return res if res else default
    except Exception:
        return default

def collect_layer7_cur_raw():
    """
    通过 nlbwmon 原生支持的七层应用/服务协议聚合 (-g layer7)，获取全网各类业务协议的真实吞吐
    """
    l7_raw = {}
    try:
        subprocess.run(["nlbw", "-c", "commit"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out = subprocess.check_output(["nlbw", "-c", "json", "-g", "layer7"]).decode("utf-8")
        data = json.loads(out)
        cols = data["columns"]
        rows = data["data"]

        proto_col = cols.index("proto")
        port_col = cols.index("port")
        rx_col = cols.index("rx_bytes")
        tx_col = cols.index("tx_bytes")
        l7_col = cols.index("layer7")

        for r in rows:
            proto = str(r[proto_col])
            port = int(r[port_col])
            rx = int(r[rx_col])
            tx = int(r[tx_col])
            l7_name = r[l7_col]

            # 友好命名业务服务
            if l7_name == "HTTPS" or (proto == "TCP" and port == 443):
                name = "HTTPS (网页浏览与App安全访问)"
            elif l7_name == "HTTP" or (proto == "TCP" and port == 80):
                name = "HTTP (网页浏览与常规下载)"
            elif l7_name == "QUIC" or (proto == "UDP" and (port == 443 or port == 80)):
                name = "QUIC / HTTP3 (流媒体与极速访问)"
            elif port == 0:
                name = "📦 P2P / PT 下载与大文件直连"
            elif l7_name in ["IMAPS", "IMAP", "SMTP"]:
                name = f"邮件服务 ({l7_name})"
            elif l7_name in ["Google Cloud Messaging", "Apple Push Service"]:
                name = f"手机推送 ({l7_name})"
            elif l7_name in ["DNS", "DNS-over-TLS"]:
                name = f"域名解析 ({l7_name})"
            elif l7_name in ["Microsoft RDP", "SSH"]:
                name = f"远程管理 ({l7_name})"
            elif l7_name:
                name = f"{l7_name} ({proto}:{port})"
            else:
                name = f"公网直连传输 ({proto}:{port})"

            if name not in l7_raw:
                l7_raw[name] = {"rx": 0, "tx": 0}
            l7_raw[name]["rx"] += rx
            l7_raw[name]["tx"] += tx
    except Exception:
        pass
    return l7_raw

def collect_target_ip_cur_raw():
    """
    从 nftables 的 dailyreport_tracker 动态 set 中读取所有公网目标/源 IP 的真实累计字节数
    """
    rx_dict = {}
    tx_dict = {}
    
    def parse_nft_set(set_name):
        res = {}
        try:
            out = subprocess.check_output(["nft", "list", "set", "inet", "dailyreport_tracker", set_name]).decode("utf-8")
            for l in out.splitlines():
                if "counter" in l and "bytes" in l:
                    items = l.split(",")
                    for item in items:
                        if "counter" in item and "bytes" in item:
                            parts = item.split()
                            ip = parts[0].strip("{\t ").strip()
                            if "bytes" in parts:
                                idx = parts.index("bytes")
                                b = int(parts[idx+1].strip("}\t ").strip())
                                res[ip] = b
        except Exception:
            pass
        return res

    rx_dict = parse_nft_set("target_ip_rx")
    tx_dict = parse_nft_set("target_ip_tx")

    all_ips = set(rx_dict.keys()) | set(tx_dict.keys())
    res = {}
    for ip in all_ips:
        res[ip] = {
            "rx": rx_dict.get(ip, 0),
            "tx": tx_dict.get(ip, 0)
        }
    return res

def get_wan_interface():
    """获取 WAN 口对应的 3 层物理/逻辑设备名 (如 pppoe-wan)"""
    try:
        out = subprocess.check_output(["ifstatus", "wan"]).decode("utf-8")
        st = json.loads(out)
        l3 = st.get("l3_device")
        if l3:
            return l3
    except Exception:
        pass
    return "pppoe-wan"

def read_wan_bytes(ifname):
    """从 /proc/net/dev 读取指定网口的底层收发字节数"""
    try:
        with open("/proc/net/dev", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.strip().split()
                if parts and parts[0].rstrip(":") == ifname:
                    return int(parts[1]), int(parts[9])
    except Exception:
        pass
    return 0, 0

def collect_proxy_controller_stats():
    """
    通用自适应代理探针：
    静默探测本地常见代理内核控制端口 (9090 / 7890 / 9999 等)，提取客户端与网站的实时传输量。
    若无代理或未开启外部控制器，自动捕获异常并平滑返回空数据，绝不阻塞或抛错。
    """
    client_stats = {}
    site_stats = {}
    
    ports_and_secrets = []
    
    # 1. 尝试从 OpenClash 配置文件读取端口与密钥
    try:
        cfg_path = "/etc/openclash/config.yaml"
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                port = 9090
                sec = ""
                m_port = re.search(r"external-controller:\s*['\"]?(?:0\.0\.0\.0|127\.0\.0\.1)?:?(\d+)['\"]?", content)
                if m_port:
                    port = int(m_port.group(1))
                m_sec = re.search(r"secret:\s*['\"](.*?)['\"]", content)
                if m_sec:
                    sec = m_sec.group(1).strip()
                ports_and_secrets.append((port, sec))
    except Exception:
        pass

    # 2. 默认常见组合探针
    for p, s in [(9090, ""), (9090, "852172"), (7890, ""), (9999, "")]:
        if (p, s) not in ports_and_secrets:
            ports_and_secrets.append((p, s))

    for port, secret in ports_and_secrets:
        try:
            url = f"http://127.0.0.1:{port}/connections"
            headers = {"User-Agent": "dailyreport-probe"}
            if secret:
                headers["Authorization"] = f"Bearer {secret}"
            
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                conns = data.get("connections", [])
                for c in conns:
                    meta = c.get("metadata", {})
                    src_ip = meta.get("sourceIP")
                    host = meta.get("host") or meta.get("destinationIP")
                    dl = int(c.get("download", 0))
                    ul = int(c.get("upload", 0))
                    
                    if src_ip:
                        if src_ip not in client_stats:
                            client_stats[src_ip] = {"rx": 0, "tx": 0}
                        client_stats[src_ip]["rx"] += dl
                        client_stats[src_ip]["tx"] += ul
                        
                    if host:
                        # 过滤掉 DNS 与内部广播探测
                        if host not in ["1.1.1.1", "1.12.12.12", "dns.google", "cloudflare-dns.com", "223.5.5.5"]:
                            if host not in site_stats:
                                site_stats[host] = [0.0, 0.0]
                            site_stats[host][0] += dl
                            site_stats[host][1] += ul
                # 探测到一个可用并提取成功，结束探针
                if client_stats or site_stats:
                    break
        except Exception:
            continue

    return client_stats, site_stats

def safe_atomic_json_dump(data, target_path):
    """安全原子写入 JSON 文件，杜绝大并发或中断导致的文件损坏与数据重叠"""
    tmp_path = f"{target_path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

def load_window(snapshots_dir, margin=6 * 3600):
    """发信用：找离「现在往前 24 小时」最近的快照。不够 24 小时就用最老的快照降级，标注实际跨度。"""
    now_ts = int(time.time())
    target_ts = now_ts - 24 * 3600
    best = None          # 最接近24小时前的快照
    oldest = None        # 最老的快照（降级用）
    best_diff = None

    if os.path.isdir(snapshots_dir):
        for fname in os.listdir(snapshots_dir):
            if not (fname.startswith("snap_") and fname.endswith(".json")):
                continue
            try:
                file_ts = int(fname[5:-5])
            except Exception:
                continue
            try:
                with open(f"{snapshots_dir}/{fname}", "r", encoding="utf-8") as sf:
                    payload = json.load(sf)
            except Exception:
                continue
            if not isinstance(payload, dict) or "nlbw" not in payload:
                continue
            payload.setdefault("ts", file_ts)

            diff = abs(file_ts - target_ts)
            if best_diff is None or diff < best_diff:
                best = payload
                best_diff = diff

            if oldest is None or file_ts < oldest["ts"]:
                oldest = payload

    # 有满足 24h 窗口的快照
    if best and best_diff is not None and best_diff <= margin:
        since = best.get("time_str") or time.strftime("%m-%d %H:%M", time.localtime(best["ts"]))
        return {
            "ready": True,
            "nlbw": best.get("nlbw") or {},
            "wan": best.get("wan") or {},
            "target_ips": best.get("target_ips") or {},
            "proxy_clients": best.get("proxy_clients") or {},
            "proxy_sites": best.get("proxy_sites") or {},
            "label": "过去24小时",
            "note": f"统计区间：{since} 至现在",
            "since": since,
        }

    # 降级：用最老的快照，标注实际跨度
    if oldest:
        elapsed_min = max(1, (now_ts - oldest["ts"]) // 60)
        if elapsed_min >= 60:
            span_str = f"约{elapsed_min // 60}小时{elapsed_min % 60}分钟"
        else:
            span_str = f"约{elapsed_min}分钟"
        since = oldest.get("time_str") or time.strftime("%m-%d %H:%M", time.localtime(oldest["ts"]))
        return {
            "ready": True,
            "nlbw": oldest.get("nlbw") or {},
            "wan": oldest.get("wan") or {},
            "target_ips": oldest.get("target_ips") or {},
            "proxy_clients": oldest.get("proxy_clients") or {},
            "proxy_sites": oldest.get("proxy_sites") or {},
            "label": f"过去{span_str}（快照积累中，满24小时后对齐）",
            "note": f"统计区间：{since} 至现在（快照尚不足24小时，数据仅供参考）",
            "since": since,
        }

    # 完全没有快照
    return {
        "ready": False,
        "nlbw": {},
        "wan": {},
        "target_ips": {},
        "proxy_clients": {},
        "proxy_sites": {},
        "label": "过去24小时",
        "note": "暂无快照数据，首次快照将在整点写入。",
        "since": "",
    }

def collect_nlbw_cur_raw():
    """从 nlbwmon 提取当前所有设备的外网业务累计 (基于物理 MAC 聚合 IPv4/IPv6 及 Clash 代理流量)"""
    nlbw_cur_raw = {}
    try:
        # 1. 建立动态 IPv4/IPv6 到物理 MAC 的穿透映射表
        ip_to_mac = {}
        try:
            neigh = subprocess.check_output(["ip", "-6", "neigh"]).decode("utf-8", errors="ignore")
            for line in neigh.splitlines():
                parts = line.split()
                if len(parts) >= 5 and "lladdr" in parts:
                    ip = parts[0].lower()
                    idx = parts.index("lladdr")
                    mac = parts[idx+1].lower()
                    ip_to_mac[ip] = mac
        except Exception:
            pass

        try:
            if os.path.exists("/tmp/dhcp.leases"):
                with open("/tmp/dhcp.leases", "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 3:
                            mac = parts[1].lower()
                            ip = parts[2].lower()
                            ip_to_mac[ip] = mac
        except Exception:
            pass

        # 2. 提交并读取 nlbwmon 原始全部数据
        subprocess.run(["nlbw", "-c", "commit"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out = subprocess.check_output(["nlbw", "-c", "json"]).decode("utf-8")
        data = json.loads(out)
        cols = data["columns"]
        rows = data["data"]

        rx_col = cols.index("rx_bytes")
        tx_col = cols.index("tx_bytes")
        mac_col = cols.index("mac")
        ip_col = cols.index("ip")
        port_col = cols.index("port")

        # 补充 nlbw 中已知记录到映射表
        for r in rows:
            ip = str(r[ip_col]).lower() if r[ip_col] else ""
            mac = str(r[mac_col]).lower() if r[mac_col] else ""
            if ip and mac and mac not in ["00:00:00:00:00:00", "00:e0:70:87:cd:69"]:
                ip_to_mac[ip] = mac

        # 3. 按物理 MAC 进行多轨穿透聚合 (合并直连 + IPv6 + Fake-IP 代理流量)
        for r in rows:
            mac = str(r[mac_col]).lower() if r[mac_col] else ""
            ip = str(r[ip_col]).lower() if r[ip_col] else ""
            port = int(r[port_col])
            rx = int(r[rx_col])
            tx = int(r[tx_col])

            # 排除局域网回环和 Docker 虚拟网段
            if ip == "127.0.0.1" or ip.startswith("172."):
                continue

            # 若 MAC 缺失或为路由器，尝试通过 IP 反查真实发起终端的物理 MAC
            if not mac or mac in ["00:00:00:00:00:00", "00:e0:70:87:cd:69"]:
                if ip in ip_to_mac:
                    mac = ip_to_mac[ip]
                else:
                    continue

            # 排除非业务类纯内部广播与 port=0 的海量局域网大流量备份
            if port == 0:
                continue

            if mac not in nlbw_cur_raw:
                nlbw_cur_raw[mac] = {"rx": 0, "tx": 0, "ip": ip}
            nlbw_cur_raw[mac]["rx"] += rx
            nlbw_cur_raw[mac]["tx"] += tx
            if ip.startswith("192.168."):
                nlbw_cur_raw[mac]["ip"] = ip
    except Exception:
        pass
    return nlbw_cur_raw

def main():
    lang = "zh_cn"
    for arg in sys.argv[1:]:
        if arg.lower() in ["en", "english"]:
            lang = "en"

    persist_dir = "/etc/dailyreport/data"
    snapshots_dir = f"{persist_dir}/snapshots"
    nlbw_base_file = f"{persist_dir}/nlbw_base.json"
    wan_base_file = f"{persist_dir}/wan_base.json"
    l7_base_file = f"{persist_dir}/l7_base.json"

    # 处理快照：只追加带时间戳的记录，不改日报用的底数。发信和测试都不走这里。
    if "--save-snapshot" in sys.argv or "--reset-base" in sys.argv:
        try:
            os.makedirs(snapshots_dir, exist_ok=True)
            now_ts = int(time.time())
            proxy_clients, proxy_sites = collect_proxy_controller_stats()
            snap_payload = {
                "ts": now_ts,
                "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts)),
                "wan": {"ifname": get_wan_interface(), "rx": read_wan_bytes(get_wan_interface())[0], "tx": read_wan_bytes(get_wan_interface())[1]},
                "nlbw": collect_nlbw_cur_raw(),
                "target_ips": collect_target_ip_cur_raw(),
                "proxy_clients": proxy_clients,
                "proxy_sites": proxy_sites,
            }
            wan_if = snap_payload["wan"]["ifname"]
            wrx, wtx = read_wan_bytes(wan_if)
            snap_payload["wan"] = {"ifname": wan_if, "rx": wrx, "tx": wtx}
            safe_atomic_json_dump(snap_payload, f"{snapshots_dir}/snap_{now_ts}.json")
            cutoff_ts = now_ts - (48 * 3600)
            for fname in os.listdir(snapshots_dir):
                if not (fname.startswith("snap_") and fname.endswith(".json")):
                    continue
                try:
                    file_ts = int(fname[5:-5])
                except Exception:
                    continue
                if file_ts < cutoff_ts:
                    try:
                        os.remove(f"{snapshots_dir}/{fname}")
                    except Exception:
                        pass
        except Exception:
            pass
        return

    window = load_window(snapshots_dir)
    base_data = window["nlbw"]
    wan_base_data = window["wan"]
    base_ips = window["target_ips"]
    base_proxy_clients = window.get("proxy_clients", {})
    base_proxy_sites = window.get("proxy_sites", {})
    window_label = window["label"]
    window_note = window["note"]

    enable_traffic = get_uci_option("dailyreport", "config", "enable_traffic", "1") == "1"
    enable_sites = get_uci_option("dailyreport", "config", "enable_sites", "1") == "1"

    if not enable_traffic and not enable_sites:
        return

    # 1. 建立主机名和 MAC 映射表
    mac_to_name = {}
    ip_to_name = {}
    ip_to_mac = {}
    sorted_sites = []

    # 从 UCI 读取静态分配
    try:
        out = subprocess.check_output(["uci", "show", "dhcp"]).decode("utf-8")
        names, ips, macs = {}, {}, {}
        for line in out.splitlines():
            if ".name=" in line:
                sec = line.split(".")[1].split("=")[0]
                val = line.split("=")[1].strip("'\"")
                names[sec] = val
            elif ".ip=" in line:
                sec = line.split(".")[1].split("=")[0]
                val = line.split("=")[1].strip("'\"")
                ips[sec] = val
            elif ".mac=" in line:
                sec = line.split(".")[1].split("=")[0]
                val = line.split("=")[1].strip("'\"")
                macs[sec] = val
        for sec, name in names.items():
            if sec in macs:
                for m in macs[sec].split():
                    mac_to_name[m.lower()] = name
            if sec in ips and sec in macs:
                ip_to_mac[ips[sec]] = macs[sec].split()[0].lower()
            if sec in ips:
                ip_to_name[ips[sec]] = name
    except Exception:
        pass

    # 从 /tmp/dhcp.leases 补充租约
    if os.path.exists("/tmp/dhcp.leases"):
        try:
            with open("/tmp/dhcp.leases", "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        mac, ip, name = parts[1].lower(), parts[2], parts[3]
                        ip_to_mac[ip] = mac
                        if name != "*" and mac not in mac_to_name:
                            mac_to_name[mac] = name
                        if ip not in ip_to_name and name != "*":
                            ip_to_name[ip] = name
        except Exception:
            pass

    # 2. 设备增量 = 现在的累计 - 24小时前那一笔。窗口没齐就不出数。
    devices = {}
    window_ready = bool(window.get("ready"))
    nlbw_cur_raw = collect_nlbw_cur_raw() if (enable_traffic and window_ready) else {}
    live_proxy_clients, live_proxy_sites = collect_proxy_controller_stats() if window_ready else ({}, {})

    if not window_ready:
        base_data = {}
        wan_base_data = {}
        base_ips = {}
        base_proxy_clients = {}
        base_proxy_sites = {}

    # 联合所有已知 MAC：包含基准里的历史活跃设备、当前有流量设备以及 DHCP 静态绑定设备
    all_tracked_macs = set(base_data.keys()) | set(nlbw_cur_raw.keys()) | set(mac_to_name.keys()) if window_ready else set()

    # 计算今日各终端纯净增量 (不遗漏待机、睡眠或离线设备)
    for mac in all_tracked_macs:
        cur = nlbw_cur_raw.get(mac, {"rx": 0, "tx": 0, "ip": base_data.get(mac, {}).get("ip", "")})
        base = base_data.get(mac, {"rx": 0, "tx": 0, "ip": cur["ip"]})
        
        diff_rx = cur["rx"] - base["rx"] if cur["rx"] >= base["rx"] else cur["rx"]
        diff_tx = cur["tx"] - base["tx"] if cur["tx"] >= base["tx"] else cur["tx"]

        ip = cur["ip"] or base.get("ip", "")
        # 若仍无 IP，尝试从 DHCP 映射获取
        if not ip:
            for lease_ip, l_mac in ip_to_mac.items():
                if l_mac == mac:
                    ip = lease_ip
                    break

        # 融合代理核心记录的实时流量 (若该 IP 走代理产生可观下行，平滑归并)
        if ip and ip in live_proxy_clients:
            p_cur = live_proxy_clients[ip]
            p_base = base_proxy_clients.get(ip, {"rx": 0, "tx": 0})
            p_diff_rx = p_cur["rx"] - p_base["rx"] if p_cur["rx"] >= p_base["rx"] else p_cur["rx"]
            p_diff_tx = p_cur["tx"] - p_base["tx"] if p_cur["tx"] >= p_base["tx"] else p_cur["tx"]
            if p_diff_rx > diff_rx:
                diff_rx = p_diff_rx
            if p_diff_tx > diff_tx:
                diff_tx = p_diff_tx

        unnamed_prefix = "Device" if lang == "en" else "设备"
        dev_name = mac_to_name.get(mac, ip_to_name.get(ip, f"{unnamed_prefix} [{mac.upper()}]"))
        devices[mac] = {
            "name": dev_name,
            "display_ip": ip if ip.startswith("192.168.") else mac.upper(),
            "wan_rx": diff_rx,
            "wan_tx": diff_tx,
            "total": diff_rx + diff_tx
        }

    # 轨 A：终端聚合增量求和
    dev_total_rx = sum(d["wan_rx"] for d in devices.values())
    dev_total_tx = sum(d["wan_tx"] for d in devices.values())

    # 轨 B：物理 WAN = 现在的口计数 - 同一笔快照里的口计数。窗口没齐就为 0。
    wan_if = get_wan_interface()
    cur_wrx, cur_wtx = read_wan_bytes(wan_if)

    if not window_ready or not wan_base_data:
        wan_phy_rx = 0
        wan_phy_tx = 0
    else:
        b_wrx = wan_base_data.get("rx", 0)
        b_wtx = wan_base_data.get("tx", 0)
        # 针对断线重拨、接口重置的安全处理
        wan_phy_rx = cur_wrx - b_wrx if cur_wrx >= b_wrx else cur_wrx
        wan_phy_tx = cur_wtx - b_wtx if cur_wtx >= b_wtx else cur_wtx

    # 核心总览数据 (实事求是呈现真实硬件物理值)
    wan_total_rx = wan_phy_rx if wan_phy_rx > 0 else dev_total_rx
    wan_total_tx = wan_phy_tx if wan_phy_tx > 0 else dev_total_tx
    wan_total = wan_total_rx + wan_total_tx

    # 3. 生成设备排行 HTML 与摘要列表 (严格按硬件真实底层采集，绝不人为掺水/平摊/计算分配)
    dev_table_rows = ""
    dev_sorted = []
    if devices:
        dev_sorted = sorted(devices.values(), key=lambda x: x["total"], reverse=True)
        for d in dev_sorted:
            tot = d["total"]
            # 统计周期内无流量（0 B）的设备不予体现，保持报表清爽
            if tot <= 0:
                continue

            rx_f = fmt_bytes(d["wan_rx"])
            tx_f = fmt_bytes(d["wan_tx"])
            tot_f = fmt_bytes(tot)
            disp_ip = d["display_ip"] if d["display_ip"] else "局域网设备"
            dev_table_rows += f"<tr><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;font-weight:600;color:#333;'>{d['name']}</td><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;color:#666;font-family:monospace;'>{disp_ip}</td><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;color:#1890ff;text-align:right;'>⬇️ {rx_f}</td><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;color:#52c41a;text-align:right;'>⬆️ {tx_f}</td><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;font-weight:700;color:#fa8c16;text-align:right;'>{tot_f}</td></tr>\n"

    if not dev_table_rows:
        dev_table_rows = "<tr><td colspan='5' style='padding:12px;text-align:center;color:#999;'>暂无活跃外网流量记录</td></tr>"

    # 4. 读取今日网站域名与公网目标 IP 真实流量 (结合 nftables 动态计数器 + DNS 缓存反查)
    site_table_rows = ""
    sorted_sites = []
    if enable_sites:
        # 先更新一次本地 DNS 解析缓存
        try:
            subprocess.run(["/root/dns_cache_update.sh"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        # 载入 DNS 映射字典
        ip_to_domain = {}
        dns_cache_file = "/root/dns_cache.log"
        if os.path.exists(dns_cache_file):
            try:
                with open(dns_cache_file, "r", encoding="utf-8", errors="ignore") as df:
                    for line in df:
                        parts = line.strip().split(",")
                        if len(parts) >= 2:
                            dip, dname = parts[0].strip(), parts[1].strip()
                            if dip and dname:
                                ip_to_domain[dip] = dname
            except Exception:
                pass

        # 网站/IP 与设备、总流量用同一笔 24 小时前快照。窗口没齐就不出。
        cur_ips = collect_target_ip_cur_raw() if window_ready else {}
        if not window_ready:
            base_ips = {}

        # 聚合按域名/目标 IP 计算今日净增量
        sites_delta = {}
        for ip, stats in cur_ips.items():
            b_rx = base_ips.get(ip, {}).get("rx", 0) if base_ips else 0
            b_tx = base_ips.get(ip, {}).get("tx", 0) if base_ips else 0
            d_rx = stats["rx"] - b_rx if stats["rx"] >= b_rx else stats["rx"]
            d_tx = stats["tx"] - b_tx if stats["tx"] >= b_tx else stats["tx"]

            if d_rx + d_tx <= 0:
                continue

            # 优先反查真实域名并做品牌/音视频智能归并
            domain = ip_to_domain.get(ip, ip)
            friendly_name = normalize_target_domain(domain, lang=lang)
            if friendly_name not in sites_delta:
                sites_delta[friendly_name] = [0.0, 0.0]
            sites_delta[friendly_name][0] += d_rx
            sites_delta[friendly_name][1] += d_tx

        # 融合代理核心解析出的实际目标域名流量 (如 YouTube / GitHub 等代理域名)
        for dom, stats in live_proxy_sites.items():
            b_stats = base_proxy_sites.get(dom, [0.0, 0.0])
            p_dl = stats[0] - b_stats[0] if stats[0] >= b_stats[0] else stats[0]
            p_ul = stats[1] - b_stats[1] if stats[1] >= b_stats[1] else stats[1]
            if p_dl + p_ul > 0:
                friendly_dom = normalize_target_domain(dom, lang=lang)
                if friendly_dom not in sites_delta:
                    sites_delta[friendly_dom] = [0.0, 0.0]
                sites_delta[friendly_dom][0] += p_dl
                sites_delta[friendly_dom][1] += p_ul

        # 若新机制刚生效还没有产生大量条目，合并旧 csv 备选，确保报表丰满
        if len(sites_delta) < 5 and os.path.exists("/root/site_traffic.csv"):
            try:
                with open("/root/site_traffic.csv", "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        parts = line.strip().split(",")
                        if len(parts) >= 3:
                            dom = parts[0].strip()
                            dl = float(parts[1])
                            ul = float(parts[2])
                            if dom:
                                friendly_csv = normalize_target_domain(dom, lang=lang)
                                if friendly_csv not in sites_delta:
                                    sites_delta[friendly_csv] = [dl, ul]
                                else:
                                    sites_delta[friendly_csv][0] += dl
                                    sites_delta[friendly_csv][1] += ul
            except Exception:
                pass

        # 排序并展示前 15 名
        sorted_sites = sorted(sites_delta.items(), key=lambda x: x[1][0] + x[1][1], reverse=True)[:15]
        idx = 1
        for dom, (dl, ul) in sorted_sites:
            if dl + ul < 1024:
                continue
            site_table_rows += f"<tr><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;color:#666;text-align:center;'>{idx}</td><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;font-family:monospace;color:#333;'>{dom}</td><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;color:#1890ff;text-align:right;'>⬇️ {fmt_bytes(dl)}</td><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;color:#52c41a;text-align:right;'>⬆️ {fmt_bytes(ul)}</td><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;font-weight:700;color:#fa8c16;text-align:right;'>{fmt_bytes(dl+ul)}</td></tr>\n"
            idx += 1

    if not site_table_rows:
        site_table_rows = "<tr><td colspan='4' style='padding:12px;text-align:center;color:#999;'>今日暂无网站或公网 IP 访问记录</td></tr>"

    if not site_table_rows:
        site_table_rows = "<tr><td colspan='4' style='padding:12px;text-align:center;color:#999;'>暂无访问记录</td></tr>"

    # 导出纯文本流量摘要供 Gotify / 告警通道复用
    try:
        top_dev_str = ""
        top_dev_lines = []
        if dev_sorted:
            top_d = dev_sorted[0]
            if lang == "en":
                top_dev_str = f" | Top: {top_d['name']}({fmt_bytes(top_d['wan_rx'] + top_d['wan_tx'])})"
            else:
                top_dev_str = f" | 榜首: {top_d['name']}({fmt_bytes(top_d['wan_rx'] + top_d['wan_tx'])})"
            for d in dev_sorted[:5]:
                top_dev_lines.append(f"  • {d['name']}: ⬇️{fmt_bytes(d['wan_rx'])} ⬆️{fmt_bytes(d['wan_tx'])}")
        
        top_site_lines = []
        if sorted_sites:
            for dom, (dl, ul) in sorted_sites[:5]:
                top_site_lines.append(f"  • {dom}: ⬇️{fmt_bytes(dl)} ⬆️{fmt_bytes(ul)}")

        if lang == "en":
            summary_text = f"WAN Bandwidth: ⬇️{fmt_bytes(wan_total_rx)} ⬆️{fmt_bytes(wan_total_tx)} (Clients: {fmt_bytes(dev_total_rx + dev_total_tx)}){top_dev_str}"
            if top_dev_lines:
                summary_text += "\n📱 Top 5 Devices:\n" + "\n".join(top_dev_lines)
            if top_site_lines:
                summary_text += "\n🌐 Top 5 Visited Sites:\n" + "\n".join(top_site_lines)
        else:
            summary_text = f"公网带宽: ⬇️{fmt_bytes(wan_total_rx)} ⬆️{fmt_bytes(wan_total_tx)} (终端合计: {fmt_bytes(dev_total_rx + dev_total_tx)}){top_dev_str}"
            if top_dev_lines:
                summary_text += "\n📱 设备消耗 Top 5:\n" + "\n".join(top_dev_lines)
            if top_site_lines:
                summary_text += "\n🌐 热门网站/IP Top 5:\n" + "\n".join(top_site_lines)

        with open("/tmp/dailyreport_traffic_summary.txt", "w", encoding="utf-8") as sf:
            sf.write(summary_text)
    except Exception:
        pass

    # 双语 HTML 模板定义
    if lang == "en":
        title_summary = "📶 Today's WAN Traffic Summary"
        lbl_rx = "⬇️ WAN Download"
        lbl_tx = "⬆️ WAN Upload"
        lbl_tot = "🔄 Total WAN Traffic"
        lbl_dev_sub = "Clients HW"
        title_devs = "📱 LAN Clients WAN Traffic Ranking"
        th_dev_name = "Device Name"
        th_dev_ip = "IP / MAC"
        th_dev_rx = "Download"
        th_dev_tx = "Upload"
        th_dev_tot = "Total WAN"
        title_sites = "🌐 Visited Sites & Direct IPs Ranking (Top 15)"
        th_site_domain = "Domain / IP"
        th_site_rx = "Download"
        th_site_tx = "Upload"
        th_site_tot = "Total"
    else:
        title_summary = f"📶 过去24小时外网流量总览"
        lbl_rx = "⬇️ 公网下载"
        lbl_tx = "⬆️ 公网上传"
        lbl_tot = "🔄 公网总吞吐"
        lbl_dev_sub = "终端直连"
        title_devs = "📱 局域网各设备外网流量排行"
        th_dev_name = "设备名称"
        th_dev_ip = "IP / MAC"
        th_dev_rx = "下载"
        th_dev_tx = "上传"
        th_dev_tot = "外网总计"
        title_sites = "🌐 网站访问与公网 IP 排行 (Top 15)"
        th_site_domain = "网站域名 / 公网 IP"
        th_site_rx = "下载流量"
        th_site_tx = "上传流量"
        th_site_tot = "总计"

    html = f"""
    <div style='margin-bottom:25px;'>
        <h3 style='font-size:16px; color:#495057; border-left:4px solid #1890ff; padding-left:10px; margin-bottom:15px;'>{title_summary}</h3>
        <div style='display:flex;gap:15px;margin-bottom:15px;'>
            <div style='flex:1;background:#fff1f0;border:1px solid #ffa39e;border-radius:6px;padding:12px;text-align:center;'>
                <div style='font-size:12px;color:#666;margin-bottom:4px;'>{lbl_rx}</div>
                <div style='font-size:18px;font-weight:bold;color:#cf1322;'>{fmt_bytes(wan_total_rx)}</div>
                <div style='font-size:11px;color:#888;margin-top:3px;'>{lbl_dev_sub}: {fmt_bytes(dev_total_rx)}</div>
            </div>
            <div style='flex:1;background:#f6ffed;border:1px solid #b7eb8f;border-radius:6px;padding:12px;text-align:center;'>
                <div style='font-size:12px;color:#666;margin-bottom:4px;'>{lbl_tx}</div>
                <div style='font-size:18px;font-weight:bold;color:#389e0d;'>{fmt_bytes(wan_total_tx)}</div>
                <div style='font-size:11px;color:#888;margin-top:3px;'>{lbl_dev_sub}: {fmt_bytes(dev_total_tx)}</div>
            </div>
            <div style='flex:1;background:#fff7e6;border:1px solid #ffd591;border-radius:6px;padding:12px;text-align:center;'>
                <div style='font-size:12px;color:#666;margin-bottom:4px;'>{lbl_tot}</div>
                <div style='font-size:18px;font-weight:700;color:#fa8c16;'>{fmt_bytes(wan_total)}</div>
                <div style='font-size:11px;color:#888;margin-top:3px;'>{lbl_dev_sub}: {fmt_bytes(dev_total_rx + dev_total_tx)}</div>
            </div>
        </div>

        <div style='margin-top:15px;margin-bottom:15px;'>
            <h4 style='font-size:14px;color:#333;margin-bottom:8px;'>{title_devs}</h4>
            <div style='overflow-x:auto;'>
                <table style='width:100%;border-collapse:collapse;font-size:13px;text-align:left;background:#fff;border-radius:6px;border:1px solid #eee;'>
                    <thead>
                        <tr style='background:#fafafa;color:#666;'>
                            <th style='padding:8px 10px;border-bottom:1px solid #eee;'>{th_dev_name}</th>
                            <th style='padding:8px 10px;border-bottom:1px solid #eee;'>{th_dev_ip}</th>
                            <th style='padding:8px 10px;border-bottom:1px solid #eee;text-align:right;'>{th_dev_rx}</th>
                            <th style='padding:8px 10px;border-bottom:1px solid #eee;text-align:right;'>{th_dev_tx}</th>
                            <th style='padding:8px 10px;border-bottom:1px solid #eee;text-align:right;'>{th_dev_tot}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {dev_table_rows}
                    </tbody>
                </table>
            </div>
        </div>

        <div style='margin-top:15px;'>
            <h4 style='font-size:14px;color:#333;margin-bottom:8px;'>{title_sites}</h4>
            <div style='overflow-x:auto;'>
                <table style='width:100%;border-collapse:collapse;font-size:13px;text-align:left;background:#fff;border-radius:6px;border:1px solid #eee;'>
                    <thead>
                        <tr style='background:#fafafa;color:#666;'>
                            <th style='padding:6px 10px;border-bottom:1px solid #eee;width:40px;text-align:center;'>#</th>
                            <th style='padding:6px 10px;border-bottom:1px solid #eee;'>{th_site_domain}</th>
                            <th style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right;'>{th_site_rx}</th>
                            <th style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right;'>{th_site_tx}</th>
                            <th style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right;'>{th_site_tot}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {site_table_rows}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    """
    print(html)

if __name__ == "__main__":
    main()
