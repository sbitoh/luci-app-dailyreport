#!/bin/sh
# OpenWrt Daily Report - Core Collector & Push Script
# Author: Junye / OpenWrt Community
# Trigger: daily crontab or manual click "Test Send"

CONF="dailyreport"

get_cfg() {
    uci -q get "${CONF}.config.$1"
}

ENABLED=$(get_cfg "enabled")
ALERT_TEMP=$(get_cfg "alert_temp")
[ -z "$ALERT_TEMP" ] && ALERT_TEMP=75

# 0. 检查是否跨天并重置每日流量统计
TODAY_TAG=$(date +%Y%m%d)
PERSIST_DIR="/etc/dailyreport/data"
LAST_DAY_FILE="$PERSIST_DIR/last_date.tag"
mkdir -p "$PERSIST_DIR" 2>/dev/null

if [ -f "$LAST_DAY_FILE" ]; then
    LAST_DAY=$(cat "$LAST_DAY_FILE" 2>/dev/null)
else
    LAST_DAY="$TODAY_TAG"
    echo "$TODAY_TAG" > "$LAST_DAY_FILE"
fi

if [ "$LAST_DAY" != "$TODAY_TAG" ]; then
    # 新的一天：清零每日累计，确保每天数据完全干净
    > /root/site_traffic.csv 2>/dev/null
    > /root/dns_cache.log 2>/dev/null
    rm -f "$PERSIST_DIR/site_traffic.csv" 2>/dev/null
    rm -f "$PERSIST_DIR/dns_cache.log" 2>/dev/null
    # 强制提交 nlbwmon 并由 traffic.py 生成当天的 0 点起跑线基准 (nlbw_base.json + wan_base.json)
    command -v nlbw >/dev/null 2>&1 && nlbw -c commit >/dev/null 2>&1
    [ -f /usr/share/dailyreport/dailyreport_traffic.py ] && python3 /usr/share/dailyreport/dailyreport_traffic.py --reset-base "$REPORT_LANG" >/dev/null 2>&1
    echo "$TODAY_TAG" > "$LAST_DAY_FILE"
fi

# 硬件温度采集
TEMP_RAW=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo 0)
TEMP_C=$((TEMP_RAW / 1000))

# 持久化刷盘动作 (save_snapshot)：写入带时间戳的快照，保留 48 小时，供发信用
if [ "$1" = "save_snapshot" ]; then
    mkdir -p "$PERSIST_DIR/snapshots" 2>/dev/null
    command -v nlbw >/dev/null 2>&1 && nlbw -c commit >/dev/null 2>&1
    [ -f /usr/share/dailyreport/dailyreport_traffic.py ] && python3 /usr/share/dailyreport/dailyreport_traffic.py --save-snapshot >/dev/null 2>&1 || true
    exit 0
fi

# 快速温度巡检模式 (check_temp)：仅在超温时继续触发告警，正常时直接静默退出
if [ "$1" = "check_temp" ]; then
    [ "$ENABLED" != "1" ] && exit 0
    [ "$TEMP_C" -lt "$ALERT_TEMP" ] && exit 0
fi

# 常规判断：未启用且不是测试或超温巡检时退出
[ "$ENABLED" != "1" ] && [ "$1" != "test" ] && [ "$1" != "check_temp" ] && exit 0

DATE_NOW=$(date "+%Y-%m-%d %H:%M:%S")
CURRENT_HOUR=$(date +%H)
TEMP_COLOR="#28a745"
[ "$TEMP_C" -ge 60 ] && TEMP_COLOR="#ffc107"
[ "$TEMP_C" -ge "$ALERT_TEMP" ] && TEMP_COLOR="#dc3545"

TOTAL_SECONDS=$(awk '{print int($1)}' /proc/uptime 2>/dev/null || echo 3600)
DAYS=$((TOTAL_SECONDS / 86400))
HOURS=$(( (TOTAL_SECONDS % 86400) / 3600 ))
MINS=$(( (TOTAL_SECONDS % 3600) / 60 ))
UPTIME_DAYS=$DAYS
UPTIME_HOURS=$HOURS
UPTIME_MINS=$MINS
if [ "$DAYS" -gt 0 ]; then
    UPTIME_CN="${DAYS}天 ${HOURS}小时 ${MINS}分钟"
    UPTIME_EN="${DAYS}d ${HOURS}h ${MINS}m"
elif [ "$HOURS" -gt 0 ]; then
    UPTIME_CN="${HOURS}小时 ${MINS}分钟"
    UPTIME_EN="${HOURS}h ${MINS}m"
else
    UPTIME_CN="${MINS}分钟"
    UPTIME_EN="${MINS}m"
fi

WAN_IP=$(ifconfig pppoe-wan 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d: -f2)
[ -z "$WAN_IP" ] && WAN_IP=$(curl -s -m 2 http://ifconfig.me 2>/dev/null)
[ -z "$WAN_IP" ] && WAN_IP="获取失败"

# 1. 采集在线设备：ping 所有已知 LAN 设备，有响应才算在线
ONLINE_DEVS_TMP="/tmp/dailyreport_online_devs.txt"
python3 - << 'PYEOF' > "$ONLINE_DEVS_TMP" 2>/dev/null
import subprocess, os

# 读取 UCI 静态绑定
known = {}  # ip -> name
try:
    out = subprocess.check_output(["uci", "show", "dhcp"], text=True)
    names, ips, macs = {}, {}, {}
    for line in out.splitlines():
        if ".name=" in line:
            sec = line.split(".")[1].split("=")[0]; val = line.split("=",1)[1].strip("'\"\n"); names[sec] = val
        elif ".ip=" in line:
            sec = line.split(".")[1].split("=")[0]; val = line.split("=",1)[1].strip("'\"\n"); ips[sec] = val
        elif ".mac=" in line:
            sec = line.split(".")[1].split("=")[0]; val = line.split("=",1)[1].strip("'\"\n"); macs[sec] = val
    for sec, name in names.items():
        if sec in ips:
            known[ips[sec]] = name
except Exception:
    pass

# 读取 DHCP 动态租约补充
if os.path.exists("/tmp/dhcp.leases"):
    try:
        with open("/tmp/dhcp.leases", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 4:
                    ip, name = parts[2], parts[3]
                    if ip not in known:
                        known[ip] = name if name != "*" else parts[1]
    except Exception:
        pass

# ping 探测，有响应才算在线，无响应不显示
for ip in sorted(known.keys(), key=lambda x: [int(c) for c in x.split('.') if c.isdigit()]):
    if ip in ("192.168.1.1",):
        continue
    r = subprocess.run(["ping", "-c", "1", "-W", "1", ip],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode == 0:
        print(f"{ip}\t{known[ip]}")
PYEOF

DEVICE_COUNT=$(wc -l < "$ONLINE_DEVS_TMP" 2>/dev/null || echo 0)
DEVICE_TABLE_ROWS=""
while IFS=$'\t' read -r D_IP D_NAME; do
    [ -z "$D_IP" ] && continue
    [ "$D_NAME" = "*" ] && D_NAME="未知设备"
    DEVICE_TABLE_ROWS="${DEVICE_TABLE_ROWS}<tr><td style='border:1px solid #eee;padding:8px;font-weight:600;'>$D_NAME</td><td style='border:1px solid #eee;padding:8px;font-family:monospace;'>$D_IP</td></tr>"
done < "$ONLINE_DEVS_TMP"

# 2. 流量统计与网站排行 (执行前先触发一次实时增量采集更新)
[ -x /root/dns_cache_update.sh ] && /root/dns_cache_update.sh 2>/dev/null
[ -x /root/site_traffic_update.sh ] && /root/site_traffic_update.sh 2>/dev/null
command -v nlbw >/dev/null 2>&1 && nlbw -c commit >/dev/null 2>&1 || true

TRAFFIC_HTML=""
TRAFFIC_SUMMARY_FILE="/tmp/dailyreport_traffic_summary.txt"
TRAFFIC_SCRIPT="/usr/share/dailyreport/dailyreport_traffic.py"
REPORT_LANG=$(get_cfg "lang")
[ -z "$REPORT_LANG" ] && REPORT_LANG="zh_cn"

if [ -f "$TRAFFIC_SCRIPT" ]; then
    TRAFFIC_HTML=$(python3 "$TRAFFIC_SCRIPT" "$REPORT_LANG" 2>/dev/null)
fi
TRAFFIC_SUMMARY=$(cat "$TRAFFIC_SUMMARY_FILE" 2>/dev/null)

# 3. 登录审计与日志 (持久化今日增量)
DATA_DIR="/etc/dailyreport/data"
mkdir -p "$DATA_DIR" 2>/dev/null
LAST_ERR_FILE="$DATA_DIR/last_sent_errors.log"
CURR_ERR_FILE="/tmp/dailyreport_curr_errors.log"
LAST_LOGIN_FILE="$DATA_DIR/last_sent_logins.log"
CURR_LOGIN_FILE="/tmp/dailyreport_curr_logins.log"

logread | tail -n 200 | grep -iE "error|warn" | grep -v "banIP" > "$CURR_ERR_FILE"
if [ -f "$LAST_ERR_FILE" ]; then
    NEW_ERRORS=$(grep -vFf "$LAST_ERR_FILE" "$CURR_ERR_FILE" 2>/dev/null)
else
    NEW_ERRORS=$(cat "$CURR_ERR_FILE" 2>/dev/null)
fi
ERR_HTML="<span style='color:#52c41a;'>✅ 暂无新增异常</span>"
if [ -n "$NEW_ERRORS" ]; then
    FORMATTED_ERRORS=$(echo "$NEW_ERRORS" | sed "s/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g" | awk '{print "<div style=\"color:#cf1322; margin-bottom:4px;\">⚠️ " $0 "</div>"}')
    ERR_HTML="<div style='background:#fff1f0; border:1px solid #ffa39e; border-radius:6px; padding:12px; font-family:monospace; font-size:12px;'>$FORMATTED_ERRORS</div>"
fi

logread | tail -n 200 | grep -E "dropbear.*succeeded|luci.*accepted" > "$CURR_LOGIN_FILE"
if [ -f "$LAST_LOGIN_FILE" ]; then
    NEW_LOGINS=$(grep -vFf "$LAST_LOGIN_FILE" "$CURR_LOGIN_FILE" 2>/dev/null)
else
    NEW_LOGINS=$(cat "$CURR_LOGIN_FILE" 2>/dev/null)
fi
if [ "$REPORT_LANG" = "en" ]; then
    LOGIN_HTML="<span style='color:#52c41a;'>✅ No new logins</span>"
else
    LOGIN_HTML="<span style='color:#52c41a;'>✅ 暂无新增登录</span>"
fi
if [ -n "$NEW_LOGINS" ]; then
    if [ "$REPORT_LANG" = "en" ]; then
        FORMATTED_LOGINS=$(echo "$NEW_LOGINS" | awk '{
            if ($0 ~ /dropbear.*succeeded/) print "<div style=\"color:#1890ff; margin-bottom:4px;\">🔑 <b>SSH Success:</b> " $0 "</div>";
            else if ($0 ~ /luci.*accepted/) print "<div style=\"color:#52c41a; margin-bottom:4px;\">🌐 <b>Web Login:</b> " $0 "</div>";
            else print "<div style=\"color:#ffc107; margin-bottom:4px;\">🟡 <b>Abnormal:</b> " $0 "</div>";
        }')
    else
        FORMATTED_LOGINS=$(echo "$NEW_LOGINS" | awk '{
            if ($0 ~ /dropbear.*succeeded/) print "<div style=\"color:#1890ff; margin-bottom:4px;\">🔑 <b>SSH 成功：</b>" $0 "</div>";
            else if ($0 ~ /luci.*accepted/) print "<div style=\"color:#52c41a; margin-bottom:4px;\">🌐 <b>Web 登录：</b>" $0 "</div>";
            else print "<div style=\"color:#ffc107; margin-bottom:4px;\">🟡 <b>异常中断：</b>" $0 "</div>";
        }')
    fi
    LOGIN_HTML="<div style='background:#f8f9fa; border:1px solid #dee2e6; border-radius:6px; padding:12px; font-family:monospace; font-size:12px;'>$FORMATTED_LOGINS</div>"
fi

# 4. banIP 与防火墙黑名单处决报告 (清晰拆分为：被banIP拦截 + 进防火墙黑名单两部分，严格统计今日增量)
ENABLE_BANIP=$(get_cfg "enable_banip")
BAN_IP_HTML=""
if [ "$ENABLE_BANIP" = "1" ]; then
    LAST_BANIP_FILE="$DATA_DIR/last_sent_banip.log"
    LAST_NFT_FILE="$DATA_DIR/last_sent_nft.log"
    CURR_BANIP_FILE="/tmp/dailyreport_curr_banip.tmp"
    CURR_NFT_FILE="/tmp/dailyreport_curr_nft.tmp"

    # 4.1 被 banIP 拦截 (去除死板日期过滤，提取当前 blocklist 中的所有有效 IP 并做增量比对)
    grep -E -o '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' /etc/banip/banip.blocklist 2>/dev/null > "$CURR_BANIP_FILE"
    
    # 增量比对 (若无历史记录或强制测试且无历史记录，则取全部；否则仅提取比上次新增的 IP)
    if [ ! -f "$LAST_BANIP_FILE" ]; then
        NEW_BANIP=$(cat "$CURR_BANIP_FILE" 2>/dev/null)
    else
        NEW_BANIP=$(grep -vFf "$LAST_BANIP_FILE" "$CURR_BANIP_FILE" 2>/dev/null)
    fi

    BANIP_PART=""
    if [ -n "$NEW_BANIP" ]; then
        if [ "$REPORT_LANG" = "en" ]; then
            BANIP_ROWS=$(echo "$NEW_BANIP" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | sort | uniq -c | sort -nr | sed 's/^[[:space:]]*//; s/[[:space:]]/ times | IP: /')
        else
            BANIP_ROWS=$(echo "$NEW_BANIP" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | sort | uniq -c | sort -nr | sed 's/^[[:space:]]*//; s/[[:space:]]/ 次 | IP: /')
        fi
        BANIP_COUNT=$(echo "$NEW_BANIP" | grep -c -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$')
        if [ "$BANIP_COUNT" -gt 0 ] && [ -n "$BANIP_ROWS" ]; then
            if [ "$REPORT_LANG" = "en" ]; then
                BANIP_PART="<div style='margin-bottom:12px;'><div style='font-size:13px;font-weight:600;color:#cf1322;margin-bottom:6px;'>🛡️ Blocked by banIP (${BANIP_COUNT} new malicious IPs today):</div><pre style='background:#fff1f0;border:1px solid #ffa39e;border-radius:6px;padding:10px;font-family:monospace;font-size:12px;color:#cf1322;margin:0;'>$BANIP_ROWS</pre></div>"
            else
                BANIP_PART="<div style='margin-bottom:12px;'><div style='font-size:13px;font-weight:600;color:#cf1322;margin-bottom:6px;'>🛡️ 被 banIP 拦截 (今日新增 ${BANIP_COUNT} 个恶意 IP)：</div><pre style='background:#fff1f0;border:1px solid #ffa39e;border-radius:6px;padding:10px;font-family:monospace;font-size:12px;color:#cf1322;margin:0;'>$BANIP_ROWS</pre></div>"
            fi
            echo "[SECURITY AUDIT] 成功捕获今日 banIP 新增封禁: $BANIP_COUNT 个"
        fi
    fi
    if [ -z "$BANIP_PART" ]; then
        if [ "$REPORT_LANG" = "en" ]; then
            BANIP_PART="<div style='margin-bottom:12px;font-size:13px;color:#52c41a;'>🛡️ banIP Interceptions: ✅ No new blocked IPs today</div>"
        else
            BANIP_PART="<div style='margin-bottom:12px;font-size:13px;color:#52c41a;'>🛡️ banIP 拦截：✅ 今日暂无新增封禁</div>"
        fi
    fi

    # 4.2 进了防火墙黑名单的恶意扫描 (nftables / NAS-Port-Ban 动态防御)
    logread | grep -E 'NAS-Port-Ban' > "$CURR_NFT_FILE"
    if [ "$1" = "test" ] || [ ! -f "$LAST_NFT_FILE" ]; then
        NEW_NFT=$(cat "$CURR_NFT_FILE" 2>/dev/null)
    else
        NEW_NFT=$(grep -vFf "$LAST_NFT_FILE" "$CURR_NFT_FILE" 2>/dev/null)
    fi
    NFT_PART=""
    if [ -n "$NEW_NFT" ]; then
        if [ "$REPORT_LANG" = "en" ]; then
            NFT_ROWS=$(echo "$NEW_NFT" | sed -n 's/.*SRC=\([^ ]*\).*/\1/p' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | sort | uniq -c | sort -nr | sed 's/^[[:space:]]*//; s/[[:space:]]/ times | Source: /')
        else
            NFT_ROWS=$(echo "$NEW_NFT" | sed -n 's/.*SRC=\([^ ]*\).*/\1/p' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | sort | uniq -c | sort -nr | sed 's/^[[:space:]]*//; s/[[:space:]]/ 次 | 恶意源: /')
        fi
        NFT_COUNT=$(echo "$NEW_NFT" | grep -c 'NAS-Port-Ban')
        if [ "$NFT_COUNT" -gt 0 ] && [ -n "$NFT_ROWS" ]; then
            if [ "$REPORT_LANG" = "en" ]; then
                NFT_PART="<div><div style='font-size:13px;font-weight:600;color:#d4380d;margin-bottom:6px;'>🚫 Firewall Port Scan Blacklist (${NFT_COUNT} new blocks today):</div><pre style='background:#fff2e8;border:1px solid #ffbb96;border-radius:6px;padding:10px;font-family:monospace;font-size:12px;color:#d4380d;margin:0;'>$NFT_ROWS</pre></div>"
            else
                NFT_PART="<div><div style='font-size:13px;font-weight:600;color:#d4380d;margin-bottom:6px;'>🚫 进防火墙黑名单/端口防扫 (今日新增 ${NFT_COUNT} 次拦截)：</div><pre style='background:#fff2e8;border:1px solid #ffbb96;border-radius:6px;padding:10px;font-family:monospace;font-size:12px;color:#d4380d;margin:0;'>$NFT_ROWS</pre></div>"
            fi
            BAN_SUMMARY="拦截: 发现新增恶意扫描"
            echo "[SECURITY AUDIT] 成功捕获今日防火墙黑名单新增扫描: $NFT_COUNT 次"
        fi
    fi
    if [ -z "$NFT_PART" ]; then
        if [ "$REPORT_LANG" = "en" ]; then
            NFT_PART="<div style='font-size:13px;color:#52c41a;'>🚫 Firewall Blacklist: ✅ No new intrusion probes today</div>"
        else
            NFT_PART="<div style='font-size:13px;color:#52c41a;'>🚫 防火墙黑名单：✅ 今日暂无新增入侵探测</div>"
        fi
    fi

    BAN_IP_HTML="<div style='padding:5px;'>${BANIP_PART}${NFT_PART}</div>"
fi

# 5. 构建邮件主题与正文 (根据 REPORT_LANG 自适应中英文)
if [ "$REPORT_LANG" = "en" ]; then
    STATUS_TEXT="System Status Healthy"
    STATUS_ICON="🟢"
    MAIL_SUBJECT="OpenWrt System & Traffic Daily Report - $DATE_NOW"
    LBL_TIME="Current Time"
    LBL_TEMP="CPU Temperature"
    LBL_UPTIME="System Uptime"
    LBL_WAN_IP="Public WAN IP"
    LBL_ONLINE_DEVS="Online Devices"
    LBL_DEV_NAME="Device Name"
    LBL_DEV_IP="IP Address"
    LBL_LOGIN_AUDIT="Login Audit (Incremental)"
    LBL_SECURITY_TITLE="🛡️ Security & Defense Report"
    UPTIME_DISP="$UPTIME_EN"
    if [ "$TEMP_C" -ge "$ALERT_TEMP" ]; then
        STATUS_TEXT="🔥 Critical Overheat Alert!"
        STATUS_ICON="🔴"
        MAIL_SUBJECT="⚠️ Urgent: OpenWrt Overheat Alert (${TEMP_C}°C) - $DATE_NOW"
    elif [ "$1" = "test" ]; then
        MAIL_SUBJECT="OpenWrt System & Traffic Test Report - $DATE_NOW"
    fi
else
    STATUS_TEXT="系统运行正常"
    STATUS_ICON="🟢"
    MAIL_SUBJECT="OpenWrt 状态与网络流量日报 - $DATE_NOW"
    LBL_TIME="当前时间"
    LBL_TEMP="CPU 温度"
    LBL_UPTIME="运行时间"
    LBL_WAN_IP="公网 IP"
    LBL_ONLINE_DEVS="在线设备"
    LBL_DEV_NAME="设备名"
    LBL_DEV_IP="IP 地址"
    LBL_LOGIN_AUDIT="登录审计 (增量)"
    LBL_SECURITY_TITLE="🛡️ 联动处决报告"
    UPTIME_DISP="$UPTIME_CN"
    if [ "$TEMP_C" -ge "$ALERT_TEMP" ]; then
        STATUS_TEXT="🔥 严重过热告警！"
        STATUS_ICON="🔴"
        MAIL_SUBJECT="⚠️ 紧急：OpenWrt 过热报警 (${TEMP_C}°C) - $DATE_NOW"
    elif [ "$1" = "test" ]; then
        MAIL_SUBJECT="OpenWrt 状态与网络流量测试报告 - $DATE_NOW"
    fi
fi

HTML_BODY="
<html>
<body style='font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,sans-serif; color:#333; background:#f0f2f5; padding:20px; margin:0;'>
    <div style='max-width:680px; margin:0 auto; background:#fff; border-radius:12px; box-shadow:0 4px 12px rgba(0,0,0,0.05); overflow:hidden;'>
        <div style='background:#007bff; padding:20px; color:#fff; text-align:center;'>
            <h2 style='margin:0; font-size:22px;'>$STATUS_ICON $STATUS_TEXT</h2>
        </div>
        <div style='padding:25px;'>
            <div style='background:#f8f9fa; border:1px solid #e9ecef; border-radius:8px; padding:20px; margin-bottom:25px;'>
                $([ "$TEMP_C" -ge "$ALERT_TEMP" ] && echo "<div style='background:#fff1f0;border:1px solid #ffa39e;border-radius:6px;padding:12px;margin-bottom:15px;color:#cf1322;font-weight:bold;'>⚠️ 严重过热报警：当前 CPU 温度已达 ${TEMP_C}°C，超过设定的安全阈值 ${ALERT_TEMP}°C！$([ \"$AUTO_POWEROFF\" = \"1\" ] && echo \"系统已启动 3 分钟紧急关机保护！\")</div>")
                <div style='font-size:16px; margin-bottom:10px; border-bottom:1px dashed #ddd; padding-bottom:8px;'>📅 <b>${LBL_TIME}：</b> <span style='color:#555;'>$DATE_NOW</span></div>
                <div style='font-size:16px; margin-bottom:10px; border-bottom:1px dashed #ddd; padding-bottom:8px;'>🌡️ <b>${LBL_TEMP}：</b> <strong style='color:$TEMP_COLOR;'>$TEMP_C°C</strong></div>
                <div style='font-size:16px; margin-bottom:10px; border-bottom:1px dashed #ddd; padding-bottom:8px;'>⏱️ <b>${LBL_UPTIME}：</b> <span style='color:#555;'>$UPTIME_DISP</span></div>
                <div style='font-size:16px;'>🏠 <b>${LBL_WAN_IP}：</b> <strong style='color:#007bff; font-family:monospace;'>$WAN_IP</strong></div>
            </div>

            <h3 style='font-size:16px; color:#495057; border-left:4px solid #17a2b8; padding-left:10px; margin-bottom:15px;'>📱 ${LBL_ONLINE_DEVS} ($DEVICE_COUNT)</h3>
            <table style='width:100%; border-collapse:collapse; font-size:14px; margin-bottom:25px; text-align:left;'>
                <tr style='background:#e9ecef;'><th style='padding:8px; border:1px solid #dee2e6;'>${LBL_DEV_NAME}</th><th style='padding:8px; border:1px solid #dee2e6;'>${LBL_DEV_IP}</th></tr>
                $DEVICE_TABLE_ROWS
            </table>

            <h3 style='font-size:16px; color:#495057; border-left:4px solid #28a745; padding-left:10px; margin-bottom:15px;'>👤 ${LBL_LOGIN_AUDIT}</h3>
            <div style='margin-bottom:25px;'>$LOGIN_HTML</div>

            $TRAFFIC_HTML

            $([ -n "$BAN_IP_HTML" ] && echo "<h3 style='font-size:16px; color:#dc3545; border-left:4px solid #dc3545; padding-left:10px; margin-bottom:15px;'>${LBL_SECURITY_TITLE}</h3><div style='margin-bottom:25px;'>$BAN_IP_HTML</div>")
        </div>
        <div style='background:#f8f9fa; text-align:center; padding:15px; color:#adb5bd; font-size:12px; border-top:1px solid #e9ecef;'>
            Generated securely by luci-app-dailyreport for OpenWrt
        </div>
    </div>
</body>
</html>
"

# 6. 推送分发 (邮件 / Gotify / Webhook)
EMAIL_ENABLED=$(get_cfg "email_enabled")
MAIL_TO=$(get_cfg "mail_to")
MAIL_FROM=$(get_cfg "mail_from")
SMTP_SERVER=$(get_cfg "smtp_server")
SMTP_PORT=$(get_cfg "smtp_port")
SMTP_USER=$(get_cfg "smtp_user")
SMTP_PASS=$(get_cfg "smtp_pass")
SMTP_SSL=$(get_cfg "smtp_ssl")

# 保存一份最新生成的 HTML 邮件用于预览或截图 (完全脱敏)
echo "$HTML_BODY" > /tmp/dailyreport_email_preview.html 2>/dev/null

if [ "$EMAIL_ENABLED" = "1" ] && [ -n "$MAIL_TO" ]; then
    # 智能 Base64 编码主题（优先 ucode / python3 / openssl / base64，彻底避免精简版 BusyBox 无 base64 命令的问题）
    if command -v ucode >/dev/null 2>&1; then
        SUBJ_B64=$(ucode -e "print(b64enc('$MAIL_SUBJECT'))" 2>/dev/null)
    elif command -v python3 >/dev/null 2>&1; then
        SUBJ_B64=$(python3 -c "import base64, sys; sys.stdout.write(base64.b64encode(sys.argv[1].encode('utf-8')).decode('utf-8'))" "$MAIL_SUBJECT" 2>/dev/null)
    elif command -v openssl >/dev/null 2>&1; then
        SUBJ_B64=$(printf "%s" "$MAIL_SUBJECT" | openssl base64 -A 2>/dev/null)
    elif command -v base64 >/dev/null 2>&1; then
        SUBJ_B64=$(printf "%s" "$MAIL_SUBJECT" | base64 | tr -d '\r\n')
    else
        SUBJ_B64=""
    fi

    SEND_TMP="/tmp/dailyreport_send.tmp"
    
    # 严格按照 RFC 822 / 5322 标准格式写入邮件头（包含 Date/Message-ID 等必填头，防止被反垃圾邮件网关拦截退信）
    RFC_DATE=$(date -R 2>/dev/null || date "+%a, %d %b %Y %H:%M:%S %z")
    MSG_ID="<dailyreport-$(date +%s)-$RANDOM@$(uname -n 2>/dev/null || echo OpenWrt)>"

    {
        printf "Date: %s\r\n" "$RFC_DATE"
        printf "Message-ID: %s\r\n" "$MSG_ID"
        printf "To: %s\r\n" "$MAIL_TO"
        [ -n "$MAIL_FROM" ] && printf "From: %s\r\n" "$MAIL_FROM"
        if [ -n "$SUBJ_B64" ]; then
            printf "Subject: =?UTF-8?B?%s?=\r\n" "$SUBJ_B64"
        else
            printf "Subject: %s\r\n" "$MAIL_SUBJECT"
        fi
        printf "MIME-Version: 1.0\r\n"
        printf "Content-Type: text/html; charset=UTF-8\r\n"
        printf "\r\n"
        printf "%s\r\n" "$HTML_BODY"
    } > "$SEND_TMP"

    # 保存一份最新生成的 HTML 邮件用于预览或截图 (完全脱敏)
    cp -f "$SEND_TMP" /tmp/dailyreport_email_preview.html 2>/dev/null

    # 成功构建报文后，落盘更新增量基准文件（确保下次发送只统计最新增量）
    cp "$CURR_ERR_FILE" "$LAST_ERR_FILE" 2>/dev/null
    cp "$CURR_LOGIN_FILE" "$LAST_LOGIN_FILE" 2>/dev/null
    [ -f "$CURR_BANIP_FILE" ] && cp "$CURR_BANIP_FILE" "$LAST_BANIP_FILE" 2>/dev/null
    [ -f "$CURR_NFT_FILE" ] && cp "$CURR_NFT_FILE" "$LAST_NFT_FILE" 2>/dev/null

    # 优先使用 Python 内置的原生 smtplib 发送（零系统命令依赖，100% 自带且稳定支持 SSL/TLS）
    if [ -n "$SMTP_SERVER" ] && [ -n "$SMTP_USER" ] && [ -n "$SMTP_PASS" ] && command -v python3 >/dev/null 2>&1; then
        echo "[INFO] 正在通过 SMTP 服务器 ($SMTP_SERVER:$SMTP_PORT) 发送邮件..."
        PY_SEND_RES=$(python3 - "$SMTP_SERVER" "$SMTP_PORT" "$SMTP_USER" "$SMTP_PASS" "$MAIL_FROM" "$MAIL_TO" "$SMTP_SSL" "$SEND_TMP" << 'PYEOF'
import sys, smtplib, ssl

host = sys.argv[1]
port = int(sys.argv[2]) if sys.argv[2] else 465
user = sys.argv[3]
password = sys.argv[4]
mail_from = sys.argv[5] or user
mail_to = sys.argv[6]
use_ssl = sys.argv[7] == "1"
mail_file = sys.argv[8]

try:
    with open(mail_file, "rb") as f:
        msg_bytes = f.read()

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    if use_ssl or port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as server:
            server.login(user, password)
            server.sendmail(mail_from, [mail_to], msg_bytes)
    else:
        with smtplib.SMTP(host, port, timeout=15) as server:
            try:
                server.starttls(context=ctx)
            except Exception:
                pass
            server.login(user, password)
            server.sendmail(mail_from, [mail_to], msg_bytes)

    print("SUCCESS")
except Exception as e:
    print(f"ERROR: {e}")
PYEOF
)
        if echo "$PY_SEND_RES" | grep -q "SUCCESS"; then
            echo "[SUCCESS] 邮件发送成功！"
        else
            echo "[ERROR] 邮件发送失败，详细原因："
            echo "$PY_SEND_RES"
        fi
    elif [ -n "$SMTP_SERVER" ] && [ -n "$SMTP_USER" ] && [ -n "$SMTP_PASS" ] && command -v msmtp >/dev/null 2>&1; then
        MSMTP_CFG="/tmp/dailyreport_msmtp.cfg"
        [ -z "$SMTP_PORT" ] && SMTP_PORT=465
        cat << EOF > "$MSMTP_CFG"
defaults
auth on
tls on
tls_starttls off
tls_certcheck off
logfile /tmp/dailyreport_msmtp.log

account dailyreport
host $SMTP_SERVER
port $SMTP_PORT
from $MAIL_FROM
user $SMTP_USER
password $SMTP_PASS

account default : dailyreport
EOF
        chmod 600 "$MSMTP_CFG"
        echo "[INFO] 正在通过 msmtp ($SMTP_SERVER:$SMTP_PORT) 发送邮件..."
        msmtp -C "$MSMTP_CFG" -t < "$SEND_TMP"
        MSMTP_EXIT=$?
        if [ "$MSMTP_EXIT" -eq 0 ]; then
            echo "[SUCCESS] 邮件发送成功！"
        else
            echo "[ERROR] 邮件发送失败 (退出码 $MSMTP_EXIT)，详细日志："
            cat /tmp/dailyreport_msmtp.log 2>/dev/null
        fi
        rm -f "$MSMTP_CFG"
    elif command -v msmtp >/dev/null 2>&1; then
        echo "[INFO] 使用系统默认 msmtp 配置发送邮件..."
        msmtp -t < "$SEND_TMP"
        [ $? -eq 0 ] && echo "[SUCCESS] 邮件发送成功！" || echo "[ERROR] 邮件发送失败！"
    else
        echo "[ERROR] 未检测到可用的邮件发信组件 (Python smtplib 或 msmtp)，请检查依赖！"
    fi
    rm -f "$SEND_TMP"
elif [ "$1" = "test" ] && [ "$EMAIL_ENABLED" != "1" ]; then
    echo "[INFO] 邮件推送未启用，跳过邮件发送测试。"
elif [ "$1" = "test" ]; then
    echo "[WARN] 邮件已启用，但收件人地址为空，无法发送邮件。"
fi

# 7. Gotify 推送与高温紧急关机保护
GOTIFY_ENABLED=$(get_cfg "gotify_enabled")
GOTIFY_URL=$(get_cfg "gotify_url")
GOTIFY_TOKEN=$(get_cfg "gotify_token")
AUTO_POWEROFF=$(get_cfg "auto_poweroff")

if [ "$GOTIFY_ENABLED" = "1" ] && [ -n "$GOTIFY_URL" ] && [ -n "$GOTIFY_TOKEN" ]; then
    CLEAN_GOTIFY_URL=$(echo "$GOTIFY_URL" | sed 's:/*$::')
    echo "[INFO] 正在向 Gotify 发送通知 ($CLEAN_GOTIFY_URL)..."
    
    if [ "$TEMP_C" -ge "$ALERT_TEMP" ]; then
        # 紧急高温告警 (Priority 10)
        ALERT_MSG="🔥 紧急：CPU 温度达到 ${TEMP_C}°C（已达或超过阈值 ${ALERT_TEMP}°C）！"
        if [ "$AUTO_POWEROFF" = "1" ]; then
            ALERT_MSG="${ALERT_MSG} 系统将在 3 分钟后执行安全关机以保护硬件！"
        fi
        G_RES=$(curl -s -w "\n%{http_code}" -X POST "${CLEAN_GOTIFY_URL}/message" \
             -H "X-Gotify-Key: ${GOTIFY_TOKEN}" \
             -F "title=🔥 OpenWrt 严重高温告警" \
             -F "message=${ALERT_MSG}" \
             -F "priority=10" 2>&1)
    else
        # Gotify 标题与正文 (根据 REPORT_LANG 自适应)
        if [ "$REPORT_LANG" = "en" ]; then
            GOTIFY_TITLE="OpenWrt Daily Report"
            [ "$1" = "test" ] && GOTIFY_TITLE="OpenWrt Daily Report (Test)"
            ACTIVE_CLIENTS_COUNT=$(grep -c '•' "$TRAFFIC_SUMMARY_FILE" 2>/dev/null || echo "$DEVICE_COUNT")
            MSG_TEXT=$(printf "📊 OpenWrt System & WAN Traffic Report\n━━━━━━━━━━━━━━━\n⏱️ Uptime: %s\n🌡️ CPU: %s°C | WAN IP: %s\n📱 Active Clients: %s (Online: %s)\n" "${UPTIME_EN}" "${TEMP_C}" "${WAN_IP}" "${ACTIVE_CLIENTS_COUNT}" "${DEVICE_COUNT}")
            if [ -n "$TRAFFIC_SUMMARY" ]; then
                MSG_TEXT=$(printf "%s\n%s\n" "${MSG_TEXT}" "${TRAFFIC_SUMMARY}")
            fi
            BAN_COUNT_BRIEF="Drops: 0"
            B_CNT=0
            [ -n "$NEW_BANIP" ] && B_CNT=$((B_CNT + $(echo "$NEW_BANIP" | grep -c -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$')))
            [ -n "$NEW_NFT" ] && B_CNT=$((B_CNT + $(echo "$NEW_NFT" | grep -c 'NAS-Port-Ban')))
            [ "$B_CNT" -gt 0 ] && BAN_COUNT_BRIEF="Drops: ${B_CNT} events"
            MSG_TEXT=$(printf "%s\n🛡️ Security: %s | %s\n" "${MSG_TEXT}" "Audited" "${BAN_COUNT_BRIEF}")
        else
            GOTIFY_TITLE="OpenWrt 状态与流量日报"
            [ "$1" = "test" ] && GOTIFY_TITLE="OpenWrt 状态与流量测试报告"
            ACTIVE_CLIENTS_COUNT=$(grep -c '•' "$TRAFFIC_SUMMARY_FILE" 2>/dev/null || echo "$DEVICE_COUNT")
            MSG_TEXT=$(printf "📊 OpenWrt 状态与网络流量日报\n━━━━━━━━━━━━━━━\n⏱️ 运行: %s\n🌡️ CPU: %s°C | WAN IP: %s\n📱 今日活跃客户端: %s 台 (当前在线: %s 台)\n" "${UPTIME_CN}" "${TEMP_C}" "${WAN_IP}" "${ACTIVE_CLIENTS_COUNT}" "${DEVICE_COUNT}")
            if [ -n "$TRAFFIC_SUMMARY" ]; then
                MSG_TEXT=$(printf "%s\n%s\n" "${MSG_TEXT}" "${TRAFFIC_SUMMARY}")
            fi
            BAN_COUNT_BRIEF="拦截: 0"
            B_CNT=0
            [ -n "$NEW_BANIP" ] && B_CNT=$((B_CNT + $(echo "$NEW_BANIP" | grep -c -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$')))
            [ -n "$NEW_NFT" ] && B_CNT=$((B_CNT + $(echo "$NEW_NFT" | grep -c 'NAS-Port-Ban')))
            [ "$B_CNT" -gt 0 ] && BAN_COUNT_BRIEF="拦截: ${B_CNT} 次恶意探测"
            MSG_TEXT=$(printf "%s\n🛡️ 安全审计: %s | %s\n" "${MSG_TEXT}" "${LOGIN_SUMMARY:-登录审计正常}" "${BAN_COUNT_BRIEF}")
        fi
        G_RES=$(curl -s -w "\n%{http_code}" -X POST "${CLEAN_GOTIFY_URL}/message" \
             -H "X-Gotify-Key: ${GOTIFY_TOKEN}" \
             -F "title=${GOTIFY_TITLE}" \
             -F "message=${MSG_TEXT}" \
             -F "priority=5" 2>&1)
    fi
    G_CODE=$(echo "$G_RES" | tail -n 1)
    if [ "$G_CODE" = "200" ]; then
        echo "[SUCCESS] Gotify 消息推送成功！"
    else
        echo "[ERROR] Gotify 推送失败 (HTTP 状态码: $G_CODE)！响应："
        echo "$G_RES" | sed '$d'
    fi
elif [ "$1" = "test" ] && [ "$GOTIFY_ENABLED" != "1" ]; then
    echo "[INFO] Gotify 推送未启用，跳过 Gotify 测试。"
elif [ "$1" = "test" ]; then
    echo "[WARN] Gotify 已启用，但未配置服务器地址或 Token，无法推送。"
fi

# 执行安全关机倒计时 (当开启保护且超温时)
if [ "$AUTO_POWEROFF" = "1" ] && [ "$TEMP_C" -ge "$ALERT_TEMP" ] && [ "$1" != "test" ]; then
    echo "[CRITICAL] CPU 过热 (${TEMP_C}°C)，将在 3 分钟后安全关机..."
    (sleep 180 && poweroff) >/dev/null 2>&1 &
fi

