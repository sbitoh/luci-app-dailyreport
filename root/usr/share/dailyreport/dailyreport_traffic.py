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

def collect_nlbw_cur_raw():
    """从 nlbwmon 提取当前所有设备的外网业务累计 (剔除 port=0 内部大流量与局域网广播)"""
    nlbw_cur_raw = {}
    try:
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

        for r in rows:
            mac = str(r[mac_col]).lower() if r[mac_col] else ""
            ip = str(r[ip_col]) if r[ip_col] else ""
            port = int(r[port_col])
            rx = int(r[rx_col])
            tx = int(r[tx_col])

            # 排除无效 MAC、路由器网桥、Docker 虚拟网段以及 port=0 内部无端口直连与海量局域网备份
            if not mac or mac in ["00:00:00:00:00:00", "00:e0:70:87:cd:69"]:
                continue
            if ip == "127.0.0.1" or ip.startswith("172."):
                continue
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

    # 处理 0 点原子级基准重置参数 (--reset-base)
    if "--reset-base" in sys.argv:
        try:
            os.makedirs(persist_dir, exist_ok=True)
            # 1. 采集并保存各终端当前起跑线底数
            cur_nlbw = collect_nlbw_cur_raw()
            with open(nlbw_base_file, "w", encoding="utf-8") as bf:
                json.dump(cur_nlbw, bf)
            # 2. 采集并保存物理 WAN 网口起跑线底数
            wan_if = get_wan_interface()
            wrx, wtx = read_wan_bytes(wan_if)
            with open(wan_base_file, "w", encoding="utf-8") as wf:
                json.dump({"ifname": wan_if, "rx": wrx, "tx": wtx}, wf)
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

        dev_name = mac_to_name.get(mac, ip_to_name.get(ip, f"设备 [{mac.upper()}]"))
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

    # 轨 C：代理分流与路由系统自身流量 (包含 OpenClash 旁路/透明代理、系统 DNS 及后台更新)
    proxy_system_total = wan_total - (dev_total_rx + dev_total_tx) if wan_total > (dev_total_rx + dev_total_tx) else 0

    # 3. 生成设备排行 HTML 与摘要列表
    dev_table_rows = ""
    dev_sorted = []
    if devices:
        dev_sorted = sorted(devices.values(), key=lambda x: x["total"], reverse=True)
        for d in dev_sorted:
            tot = d["total"]
            rx_f = fmt_bytes(d["wan_rx"])
            tx_f = fmt_bytes(d["wan_tx"])
            if tot > 0:
                tot_f = fmt_bytes(tot)
                status_badge = ""
            else:
                tot_f = "0 B"
                status_badge = " <span style='font-size:11px;color:#999;font-weight:normal;'>(待机/离线)</span>"
            disp_ip = d["display_ip"] if d["display_ip"] else "局域网设备"
            dev_table_rows += f"<tr><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;font-weight:600;color:#333;'>{d['name']}{status_badge}</td><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;color:#666;font-family:monospace;'>{disp_ip}</td><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;color:#1890ff;text-align:right;'>⬇️ {rx_f}</td><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;color:#52c41a;text-align:right;'>⬆️ {tx_f}</td><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;font-weight:700;color:#fa8c16;text-align:right;'>{tot_f}</td></tr>\n"

    if not dev_table_rows:
        dev_table_rows = "<tr><td colspan='5' style='padding:12px;text-align:center;color:#999;'>暂无活跃外网流量记录</td></tr>"

    # 4. 读取网站流量
    site_table_rows = ""
    if enable_sites:
        site_csv = "/root/site_traffic.csv"
        if os.path.exists(site_csv):
            sites = {}
            try:
                with open(site_csv, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        parts = line.strip().split(",")
                        if len(parts) >= 3:
                            domain, dl, ul = parts[0].strip(), float(parts[1]), float(parts[2])
                            if any(c.isalpha() for c in domain):
                                sites[domain] = sites.get(domain, [0, 0])
                                sites[domain][0] += dl
                                sites[domain][1] += ul
                sorted_sites = sorted(sites.items(), key=lambda x: x[1][0] + x[1][1], reverse=True)[:15]
                idx = 1
                for domain, (dl, ul) in sorted_sites:
                    if dl + ul < 1024:
                        continue
                    site_table_rows += f"<tr><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;color:#666;text-align:center;'>{idx}</td><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;font-family:monospace;color:#333;'>{domain}</td><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;color:#1890ff;text-align:right;'>⬇️ {fmt_bytes(dl)}</td><td style='padding:6px 10px;border-bottom:1px solid #f0f0f0;color:#52c41a;text-align:right;'>⬆️ {fmt_bytes(ul)}</td></tr>\n"
                    idx += 1
            except Exception:
                pass

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
            for domain, (dl, ul) in sorted_sites[:5]:
                top_site_lines.append(f"  • {domain}: {fmt_bytes(dl+ul)}")

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
                summary_text += "\n🌐 热门网站 Top 5:\n" + "\n".join(top_site_lines)

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
        lbl_proxy_title = "🌐 Proxy / System Route"
        lbl_proxy_sub = "OpenClash / Router"
        title_devs = "📱 LAN Clients WAN Traffic Ranking"
        th_dev_name = "Device Name"
        th_dev_ip = "IP / MAC"
        th_dev_rx = "Download"
        th_dev_tx = "Upload"
        th_dev_tot = "Total WAN"
        title_sites = "🌐 Top 15 Visited Websites"
        th_site_domain = "Domain / Service"
        th_site_rx = "Download"
        th_site_tx = "Upload"
    else:
        title_summary = "📶 今日全网外网流量总览"
        lbl_rx = "⬇️ 公网下载"
        lbl_tx = "⬆️ 公网上传"
        lbl_tot = "🔄 公网总吞吐"
        lbl_dev_sub = "终端统计"
        lbl_proxy_title = "🌐 代理/路由系统分流"
        lbl_proxy_sub = "OpenClash/系统"
        title_devs = "📱 局域网各设备外网流量排行"
        th_dev_name = "设备名称"
        th_dev_ip = "IP / MAC"
        th_dev_rx = "下载"
        th_dev_tx = "上传"
        th_dev_tot = "外网总计"
        title_sites = "🌐 网站访问流量排行 (Top 15)"
        th_site_domain = "域名 / 服务"
        th_site_rx = "下载流量"
        th_site_tx = "上传流量"

    html = f"""
    <div style='margin-bottom:25px;'>
        <h3 style='font-size:16px; color:#495057; border-left:4px solid #1890ff; padding-left:10px; margin-bottom:15px;'>{title_summary}</h3>
        <div style='display:flex;gap:12px;margin-bottom:15px;'>
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
            <div style='flex:1;background:#f0f5ff;border:1px solid #adc6ff;border-radius:6px;padding:12px;text-align:center;'>
                <div style='font-size:12px;color:#666;margin-bottom:4px;'>{lbl_proxy_title}</div>
                <div style='font-size:18px;font-weight:700;color:#2f54eb;'>{fmt_bytes(proxy_system_total)}</div>
                <div style='font-size:11px;color:#888;margin-top:3px;'>{lbl_proxy_sub}</div>
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
