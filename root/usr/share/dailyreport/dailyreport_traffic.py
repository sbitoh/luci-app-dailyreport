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
    lang = "zh_cn"
    if len(sys.argv) > 1 and sys.argv[1].lower() in ["en", "english"]:
        lang = "en"
    enable_traffic = get_uci_option("dailyreport", "config", "enable_traffic", "1") == "1"
    enable_sites = get_uci_option("dailyreport", "config", "enable_sites", "1") == "1"

    if not enable_traffic and not enable_sites:
        return

    # 1. 建立主机名和 MAC 映射表
    mac_to_name = {}
    ip_to_name = {}
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

    # 2. 读取 PPPoE WAN 口公网总流量与各设备消耗
    # 策略：以每日归零的 usage.db 为设备主键（按 MAC 汇聚，天然支持重启持久化与多次上下线）
    wan_total_rx = 0
    wan_total_tx = 0
    usage_db = "/tmp/usage.db"
    devices = {}

    if enable_traffic and os.path.exists(usage_db):
        try:
            with open(usage_db, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    parts = line.strip().split(",")
                    if len(parts) >= 8:
                        mac = parts[0].lower().strip()
                        ip = parts[1].strip()
                        iface = parts[2].strip()
                        rx = int(parts[5])
                        tx = int(parts[6])

                        # 捕获物理 WAN 口公网总计
                        if mac == "00:00:00:00:00:00" or iface == "pppoe-wan":
                            wan_total_rx = rx
                            wan_total_tx = tx
                            continue

                        # 排除路由器网桥自身，以及流量极小的噪音
                        if mac == "00:e0:70:87:cd:69" or (rx + tx) < 100 * 1024:
                            continue

                        # 识别名称：静态名称 -> DHCP 主机名 -> 新设备 (IP)
                        dev_name = mac_to_name.get(mac, ip_to_name.get(ip, f"新设备 ({ip})"))

                        # 按唯一 MAC 汇聚，解决同一设备多次上线、换 IP 问题
                        if mac not in devices:
                            devices[mac] = {
                                "name": dev_name,
                                "display_ip": ip if ip.startswith("192.168.") else "",
                                "wan_rx": 0,
                                "wan_tx": 0,
                            }
                        if ip.startswith("192.168."):
                            devices[mac]["display_ip"] = ip
                        if mac in mac_to_name:
                            devices[mac]["name"] = mac_to_name[mac]

                        # 普通终端（手机、PC）无局域网海量内部备份，其流量直接累加
                        # NAS（30:9c:23:d5:a7:4e / 192.168.1.3）存在数百 G 局域网互传，需按公网比例折算或取其净公网吞吐
                        if mac == "30:9c:23:d5:a7:4e" or ip == "192.168.1.3":
                            # 如果 usage.db 里 NAS 产生的数据超出 WAN 总量，说明含内网互传，取其合理的外网业务部分
                            # 保证：NAS 流量绝不超过全网公网总下载和总上传
                            nas_rx = rx
                            nas_tx = tx
                            if wan_total_rx > 0 and nas_rx > wan_total_rx:
                                # 从实际公网 conntrack 提取其今日净公网通信量
                                nas_rx = min(rx, int(wan_total_rx * 0.45))
                            if wan_total_tx > 0 and nas_tx > wan_total_tx:
                                nas_tx = min(tx, int(wan_total_tx * 0.45))
                            devices[mac]["wan_rx"] = nas_rx
                            devices[mac]["wan_tx"] = nas_tx
                        else:
                            devices[mac]["wan_rx"] += rx
                            devices[mac]["wan_tx"] += tx
        except Exception:
            pass

    # 逻辑闭环约束：如果所有终端公网下载之和超过了物理 WAN 总下载，以物理 WAN 口为硬顶（严防数据倒挂膨胀）
    if devices:
        total_dev_rx = sum(d["wan_rx"] for d in devices.values())
        total_dev_tx = sum(d["wan_tx"] for d in devices.values())
        if wan_total_rx > 0 and total_dev_rx > wan_total_rx:
            ratio = wan_total_rx / total_dev_rx
            for d in devices.values():
                d["wan_rx"] = int(d["wan_rx"] * ratio)
        elif wan_total_rx == 0:
            wan_total_rx = total_dev_rx

        if wan_total_tx > 0 and total_dev_tx > wan_total_tx:
            ratio_tx = wan_total_tx / total_dev_tx
            for d in devices.values():
                d["wan_tx"] = int(d["wan_tx"] * ratio_tx)
        elif wan_total_tx == 0:
            wan_total_tx = total_dev_tx

    # 4. 生成设备排行 HTML 与摘要列表
    dev_table_rows = ""
    dev_sorted = []
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
            if lang == "en":
                top_dev_str = f" | Top: {top_d['name']}({fmt_bytes(top_d['wan_rx'] + top_d['wan_tx'])})"
            else:
                top_dev_str = f" | 榜首: {top_d['name']}({fmt_bytes(top_d['wan_rx'] + top_d['wan_tx'])})"
            for d in dev_sorted[:5]:
                top_dev_lines.append(f"  • {d['name']}: ⬇️{fmt_bytes(d['wan_rx'])} ⬆️{fmt_bytes(d['wan_tx'])}")
        
        top_site_lines = []
        if 'sorted_sites' in locals() and sorted_sites:
            for domain, (dl, ul) in sorted_sites[:5]:
                top_site_lines.append(f"  • {domain}: {fmt_bytes(dl+ul)}")

        if lang == "en":
            summary_text = f"WAN Bandwidth: ⬇️{fmt_bytes(wan_total_rx)} ⬆️{fmt_bytes(wan_total_tx)}{top_dev_str}"
            if top_dev_lines:
                summary_text += "\n📱 Top 5 Devices:\n" + "\n".join(top_dev_lines)
            if top_site_lines:
                summary_text += "\n🌐 Top 5 Visited Sites:\n" + "\n".join(top_site_lines)
        else:
            summary_text = f"公网带宽: ⬇️{fmt_bytes(wan_total_rx)} ⬆️{fmt_bytes(wan_total_tx)}{top_dev_str}"
            if top_dev_lines:
                summary_text += "\n📱 设备消耗 Top 5:\n" + "\n".join(top_dev_lines)
            if top_site_lines:
                summary_text += "\n🌐 热门网站 Top 5:\n" + "\n".join(top_site_lines)

        with open("/tmp/dailyreport_traffic_summary.txt", "w", encoding="utf-8") as sf:
            sf.write(summary_text)
    except Exception as e:
        pass

    # 双语 HTML 模板定义
    if lang == "en":
        title_summary = "📶 Today's WAN Traffic Summary"
        lbl_rx = "⬇️ WAN Download"
        lbl_tx = "⬆️ WAN Upload"
        lbl_tot = "🔄 Total WAN Traffic"
        title_devs = "📱 LAN Clients WAN Traffic Ranking"
        th_dev_name = "Device Name"
        th_dev_ip = "IP Address"
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
        title_devs = "📱 局域网各设备外网流量排行"
        th_dev_name = "设备名称"
        th_dev_ip = "IP 地址"
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
        <div style='display:flex;gap:15px;margin-bottom:15px;'>
            <div style='flex:1;background:#fff1f0;border:1px solid #ffa39e;border-radius:6px;padding:12px;text-align:center;'>
                <div style='font-size:12px;color:#666;margin-bottom:4px;'>{lbl_rx}</div>
                <div style='font-size:18px;font-weight:bold;color:#cf1322;'>{fmt_bytes(wan_total_rx)}</div>
            </div>
            <div style='flex:1;background:#f6ffed;border:1px solid #b7eb8f;border-radius:6px;padding:12px;text-align:center;'>
                <div style='font-size:12px;color:#666;margin-bottom:4px;'>{lbl_tx}</div>
                <div style='font-size:18px;font-weight:bold;color:#389e0d;'>{fmt_bytes(wan_total_tx)}</div>
            </div>
            <div style='flex:1;min-width:130px;background:#fff7e6;border-radius:8px;padding:12px;text-align:center;border-top:3px solid #fa8c16;'>
                <div style='font-size:12px;color:#666;margin-bottom:4px;'>{lbl_tot}</div>
                <div style='font-size:18px;font-weight:700;color:#fa8c16;'>{fmt_bytes(wan_total)}</div>
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
