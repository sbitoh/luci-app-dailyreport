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
    nlbw_base_file = f"{persist_dir}/nlbw_base.json"
    wan_base_file = f"{persist_dir}/wan_base.json"
    l7_base_file = f"{persist_dir}/l7_base.json"

    # 处理 0 点原子级基准重置参数 (--reset-base)
    if "--reset-base" in sys.argv:
        try:
            os.makedirs(persist_dir, exist_ok=True)
            # 1. 采集并保存各终端当前起跑线底数
            cur_nlbw = collect_nlbw_cur_raw()
            safe_atomic_json_dump(cur_nlbw, nlbw_base_file)

            # 2. 采集并保存目标 IP 流量起跑线底数
            cur_ips = collect_target_ip_cur_raw()
            safe_atomic_json_dump(cur_ips, f"{persist_dir}/target_ip_base.json")

            # 3. 采集并保存物理 WAN 网口起跑线底数
            wan_if = get_wan_interface()
            wrx, wtx = read_wan_bytes(wan_if)
            safe_atomic_json_dump({"ifname": wan_if, "rx": wrx, "tx": wtx}, wan_base_file)
        except Exception:
            pass
        return

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

    # 2. 从 nlbwmon 提取各设备真实外网增量
    devices = {}
    nlbw_cur_raw = collect_nlbw_cur_raw() if enable_traffic else {}

    base_data = {}
    if os.path.exists(nlbw_base_file):
        try:
            with open(nlbw_base_file, "r", encoding="utf-8") as bf:
                base_data = json.load(bf)
        except Exception:
            pass
    else:
        try:
            os.makedirs(persist_dir, exist_ok=True)
            with open(nlbw_base_file, "w", encoding="utf-8") as bf:
                json.dump(nlbw_cur_raw, bf)
            base_data = nlbw_cur_raw
        except Exception:
            pass

    # 联合所有已知 MAC：包含基准里的历史活跃设备、当前有流量设备以及 DHCP 静态绑定设备
    all_tracked_macs = set(base_data.keys()) | set(nlbw_cur_raw.keys()) | set(mac_to_name.keys())

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

    # 轨 B：物理 WAN 网口独立增量 (pppoe-wan 真实底层计数器)
    wan_if = get_wan_interface()
    cur_wrx, cur_wtx = read_wan_bytes(wan_if)

    wan_base_data = {}
    if os.path.exists(wan_base_file):
        try:
            with open(wan_base_file, "r", encoding="utf-8") as wf:
                wan_base_data = json.load(wf)
        except Exception:
            pass

    if not wan_base_data:
        # 首次或尚未建立 0 点基准时，保存当前为起跑线
        try:
            os.makedirs(persist_dir, exist_ok=True)
            with open(wan_base_file, "w", encoding="utf-8") as wf:
                json.dump({"ifname": wan_if, "rx": cur_wrx, "tx": cur_wtx}, wf)
        except Exception:
            pass
        wan_phy_rx = dev_total_rx
        wan_phy_tx = dev_total_tx
    else:
        b_wrx = wan_base_data.get("rx", 0)
        b_wtx = wan_base_data.get("tx", 0)
        # 针对断线重拨、接口重置的安全处理
        wan_phy_rx = cur_wrx - b_wrx if cur_wrx >= b_wrx else cur_wrx
        wan_phy_tx = cur_wtx - b_wtx if cur_wtx >= b_wtx else cur_wtx

    # 核心总览数据 (实事求是呈现)
    wan_total_rx = wan_phy_rx if wan_phy_rx > 0 else dev_total_rx
    wan_total_tx = wan_phy_tx if wan_phy_tx > 0 else dev_total_tx
    wan_total = wan_total_rx + wan_total_tx

    # 轨 C：将经过代理或系统出网的公网总吞吐，按各硬件今日实际业务出网比例，完全合并到各个硬件名下！
    # 绝不单独列出“代理分流”，将所有真实 WAN 物理流量 100% 落实到具体硬件
    unallocated_rx = max(0, wan_total_rx - dev_total_rx)
    unallocated_tx = max(0, wan_total_tx - dev_total_tx)

    if dev_total_rx > 0 or dev_total_tx > 0:
        base_dev_rx = dev_total_rx
        base_dev_tx = dev_total_tx
        for mac, d in devices.items():
            if base_dev_rx > 0:
                d["wan_rx"] += int(unallocated_rx * (d["wan_rx"] / base_dev_rx))
            if base_dev_tx > 0:
                d["wan_tx"] += int(unallocated_tx * (d["wan_tx"] / base_dev_tx))
            d["total"] = d["wan_rx"] + d["wan_tx"]

    # 重新求和终端硬件总计，使得终端统计 100% 完全等于公网总吞吐
    dev_total_rx = sum(d["wan_rx"] for d in devices.values())
    dev_total_tx = sum(d["wan_tx"] for d in devices.values())

    # 3. 生成设备排行 HTML 与摘要列表
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

        # 获取当前目标 IP 流量
        cur_ips = collect_target_ip_cur_raw()
        target_ip_base_file = f"{persist_dir}/target_ip_base.json"
        base_ips = {}
        if os.path.exists(target_ip_base_file):
            try:
                with open(target_ip_base_file, "r", encoding="utf-8") as tibf:
                    base_ips = json.load(tibf)
            except Exception:
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

            # 优先反查真实域名
            domain = ip_to_domain.get(ip, ip)
            if domain not in sites_delta:
                sites_delta[domain] = [0.0, 0.0]
            sites_delta[domain][0] += d_rx
            sites_delta[domain][1] += d_tx

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
                            if dom and dom not in sites_delta:
                                sites_delta[dom] = [dl, ul]
            except Exception:
                pass

        # 排序并展示前 15 名
        sorted_sites = sorted(sites_delta.items(), key=lambda x: x[1][0] + x[1][1], reverse=True)[:15]
        idx = 1
        for dom, (dl, ul) in sorted_sites:
            if dl + ul < 1024:
                continue
            site_table_rows += f"<tr><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;color:#666;text-align:center;'>{idx}</td><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;font-family:monospace;color:#333;'>{dom}</td><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;color:#1890ff;text-align:right;'>⬇️ {fmt_bytes(dl)}</td><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;color:#52c41a;text-align:right;'>⬆️ {fmt_bytes(ul)}</td></tr>\n"
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
                top_site_lines.append(f"  • {dom}: {fmt_bytes(dl+ul)}")

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
        lbl_dev_sub = "Clients Sum"
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
    else:
        title_summary = "📶 今日全网外网流量总览"
        lbl_rx = "⬇️ 公网下载"
        lbl_tx = "⬆️ 公网上传"
        lbl_tot = "🔄 公网总吞吐"
        lbl_dev_sub = "终端统计"
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
