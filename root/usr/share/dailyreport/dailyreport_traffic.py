#!/usr/bin/env python3
"""
OpenWrt Daily Report - Traffic Analysis Engine
Extracts WAN-only traffic per LAN host and top visited sites.
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

def main():
    enable_traffic = get_uci_option("dailyreport", "config", "enable_traffic", "1") == "1"
    enable_sites = get_uci_option("dailyreport", "config", "enable_sites", "1") == "1"

    if not enable_traffic and not enable_sites:
        return

    # 1. 建立主机名和 MAC 映射表
    mac_to_name = {}
    ip_to_mac = {}

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
        except Exception:
            pass

    # 2. 读取 PPPoE WAN 口公网总流量 (支持 usage.db 或 nlbwmon 自动求和，完全脱离任何外部私有脚本)
    wan_total_rx = 0
    wan_total_tx = 0
    usage_db = "/tmp/usage.db"
    if os.path.exists(usage_db):
        try:
            with open(usage_db, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    if "pppoe-wan" in line or line.startswith("00:00:00:00:00:00"):
                        parts = line.strip().split(",")
                        if len(parts) >= 8:
                            try:
                                wan_total_rx = int(parts[5])
                                wan_total_tx = int(parts[6])
                                break
                            except ValueError:
                                pass
        except Exception:
            pass

    # 3. 读取各设备流量：采用 OpenWrt 官方标配 nlbwmon 探针 (零私有脚本依赖，所有人安装通用)
    # 通用规则：严格过滤非公网服务及未分配端口流量 (port 0 局域网广播与直连)，所有设备平权处理，包括 NAS、手机、PC、新设备
    devices = {}
    if enable_traffic:
        try:
            out = subprocess.check_output(["nlbw", "-c", "json"]).decode("utf-8")
            data = json.loads(out)
            cols = data["columns"]
            rows = data["data"]

            rx_col = cols.index("rx_bytes")
            tx_col = cols.index("tx_bytes")
            mac_col = cols.index("mac")
            ip_col = cols.index("ip")
            proto_col = cols.index("proto")
            port_col = cols.index("port")
            l7_col = cols.index("layer7")

            for r in rows:
                mac = str(r[mac_col]).lower() if r[mac_col] else ""
                ip = str(r[ip_col]) if r[ip_col] else ""
                rx = int(r[rx_col])
                tx = int(r[tx_col])
                port = int(r[port_col])
                l7 = str(r[l7_col]) if r[l7_col] else ""

                # 排除无效 MAC、局域网本地回环或 Docker 桥接子网
                if not mac or mac in ["00:00:00:00:00:00", "00:e0:70:87:cd:69"]:
                    continue
                if ip == "127.0.0.1" or ip.startswith("172."):
                    continue

                # 通用公网协议过滤：局域网内部海量备份与无端口裸流在 port=0，正常公网交互必有具体端口
                if port == 0:
                    continue

                # 识别通用公网网络业务 (Web、DNS、邮件、推送、远程桌面、代理等通用端口)
                is_wan = (port in [80, 443, 853, 53, 993, 143, 25, 5228, 3389, 7890, 7892, 10001, 3328]) or (l7 in ["HTTPS", "HTTP", "QUIC", "DNS", "DNS-over-TLS", "IMAPS", "Google Cloud Messaging"])
                if not is_wan:
                    continue

                resolved_mac = mac if (mac and mac != "00:00:00:00:00:00") else ip_to_mac.get(ip, "")
                dev_id = resolved_mac if resolved_mac else ip
                if not dev_id:
                    continue

                # 动态识别设备名：优先已知名字，未知设备自动显示「新设备 (IP)」
                dev_name = mac_to_name.get(resolved_mac, f"新设备 ({ip})")

                if dev_id not in devices:
                    devices[dev_id] = {
                        "name": dev_name,
                        "display_ip": ip if ip.startswith("192.168.") else "",
                        "wan_rx": 0,
                        "wan_tx": 0,
                    }

                if ip.startswith("192.168."):
                    devices[dev_id]["display_ip"] = ip
                    if resolved_mac in mac_to_name:
                        devices[dev_id]["name"] = mac_to_name[resolved_mac]

                devices[dev_id]["wan_rx"] += rx
                devices[dev_id]["wan_tx"] += tx
        except Exception:
            pass

    # 若未获取到 wan_total，则以各客户端公网消耗之和作为总外网吞吐 (逻辑严密闭环)
    client_rx_sum = sum(d["wan_rx"] for d in devices.values())
    client_tx_sum = sum(d["wan_tx"] for d in devices.values())
    if wan_total_rx < client_rx_sum:
        wan_total_rx = client_rx_sum
    if wan_total_tx < client_tx_sum:
        wan_total_tx = client_tx_sum

    # 4. 生成设备排行 HTML
    dev_table_rows = ""
    if devices:
        dev_sorted = sorted(devices.values(), key=lambda x: x["wan_rx"] + x["wan_tx"], reverse=True)
        for d in dev_sorted:
            tot = d["wan_rx"] + d["wan_tx"]
            rx_f = fmt_bytes(d["wan_rx"])
            tx_f = fmt_bytes(d["wan_tx"])
            tot_f = fmt_bytes(tot)
            disp_ip = d["display_ip"] if d["display_ip"] else "局域网设备"
            dev_table_rows += f"<tr><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;font-weight:600;color:#333;'>{d['name']}</td><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;color:#666;font-family:monospace;'>{disp_ip}</td><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;color:#1890ff;text-align:right;'>⬇️ {rx_f}</td><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;color:#52c41a;text-align:right;'>⬆️ {tx_f}</td><td style='padding:8px 10px;border-bottom:1px solid #f0f0f0;font-weight:700;color:#fa8c16;text-align:right;'>{tot_f}</td></tr>\n"

    if not dev_table_rows:
        dev_table_rows = "<tr><td colspan='5' style='padding:12px;text-align:center;color:#999;'>暂无活跃外网流量记录</td></tr>"

    # 5. 读取网站流量
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

    wan_total = wan_total_rx + wan_total_tx

    # 导出纯文本流量摘要供 Gotify / 告警通道复用
    try:
        top_dev_str = ""
        top_dev_lines = []
        if 'dev_sorted' in locals() and dev_sorted:
            top_d = dev_sorted[0]
            top_dev_str = f" | 榜首: {top_d['name']}({fmt_bytes(top_d['wan_rx'] + top_d['wan_tx'])})"
            for d in dev_sorted[:5]:
                top_dev_lines.append(f"  • {d['name']}: ⬇️{fmt_bytes(d['wan_rx'])} ⬆️{fmt_bytes(d['wan_tx'])}")
        
        top_site_lines = []
        if 'sorted_sites' in locals() and sorted_sites:
            for domain, (dl, ul) in sorted_sites[:5]:
                top_site_lines.append(f"  • {domain}: {fmt_bytes(dl+ul)}")

        summary_text = f"公网带宽: ⬇️{fmt_bytes(wan_total_rx)} ⬆️{fmt_bytes(wan_total_tx)}{top_dev_str}"
        if top_dev_lines:
            summary_text += "\n📱 设备消耗 Top 5:\n" + "\n".join(top_dev_lines)
        if top_site_lines:
            summary_text += "\n🌐 热门网站 Top 5:\n" + "\n".join(top_site_lines)

        with open("/tmp/dailyreport_traffic_summary.txt", "w", encoding="utf-8") as sf:
            sf.write(summary_text)
    except Exception as e:
        pass

    html = f"""
    <div style='margin-bottom:25px;'>
        <h3 style='font-size:16px; color:#495057; border-left:4px solid #1890ff; padding-left:10px; margin-bottom:15px;'>📶 今日全网外网流量总览</h3>
        <div style='display:flex;gap:15px;margin-bottom:15px;'>
            <div style='flex:1;background:#fff1f0;border:1px solid #ffa39e;border-radius:6px;padding:12px;text-align:center;'>
                <div style='font-size:12px;color:#666;margin-bottom:4px;'>⬇️ 公网下载</div>
                <div style='font-size:18px;font-weight:bold;color:#cf1322;'>{fmt_bytes(wan_total_rx)}</div>
            </div>
            <div style='flex:1;background:#f6ffed;border:1px solid #b7eb8f;border-radius:6px;padding:12px;text-align:center;'>
                <div style='font-size:12px;color:#666;margin-bottom:4px;'>⬆️ 公网上传</div>
                <div style='font-size:18px;font-weight:bold;color:#389e0d;'>{fmt_bytes(wan_total_tx)}</div>
            </div>
            <div style='flex:1;min-width:130px;background:#fff7e6;border-radius:8px;padding:12px;text-align:center;border-top:3px solid #fa8c16;'>
                <div style='font-size:12px;color:#666;margin-bottom:4px;'>🔄 公网总吞吐</div>
                <div style='font-size:18px;font-weight:700;color:#fa8c16;'>{fmt_bytes(wan_total)}</div>
            </div>
        </div>

        <div style='margin-top:15px;margin-bottom:15px;'>
            <h4 style='font-size:14px;color:#333;margin-bottom:8px;'>📱 局域网各设备外网流量排行</h4>
            <div style='overflow-x:auto;'>
                <table style='width:100%;border-collapse:collapse;font-size:13px;text-align:left;background:#fff;border-radius:6px;border:1px solid #eee;'>
                    <thead>
                        <tr style='background:#fafafa;color:#666;'>
                            <th style='padding:8px 10px;border-bottom:1px solid #eee;'>设备名称</th>
                            <th style='padding:8px 10px;border-bottom:1px solid #eee;'>IP 地址</th>
                            <th style='padding:8px 10px;border-bottom:1px solid #eee;text-align:right;'>下载</th>
                            <th style='padding:8px 10px;border-bottom:1px solid #eee;text-align:right;'>上传</th>
                            <th style='padding:8px 10px;border-bottom:1px solid #eee;text-align:right;'>外网总计</th>
                        </tr>
                    </thead>
                    <tbody>
                        {dev_table_rows}
                    </tbody>
                </table>
            </div>
        </div>

        <div style='margin-top:15px;'>
            <h4 style='font-size:14px;color:#333;margin-bottom:8px;'>🌐 网站访问流量排行 (Top 15)</h4>
            <div style='overflow-x:auto;'>
                <table style='width:100%;border-collapse:collapse;font-size:13px;text-align:left;background:#fff;border-radius:6px;border:1px solid #eee;'>
                    <thead>
                        <tr style='background:#fafafa;color:#666;'>
                            <th style='padding:6px 10px;border-bottom:1px solid #eee;width:40px;text-align:center;'>#</th>
                            <th style='padding:6px 10px;border-bottom:1px solid #eee;'>域名 / 服务</th>
                            <th style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right;'>下载流量</th>
                            <th style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right;'>上传流量</th>
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
