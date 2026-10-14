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

# 硬件温度采集
TEMP_RAW=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo 0)
TEMP_C=$((TEMP_RAW / 1000))

# 持久化刷盘动作 (save_snapshot)
if [ "$1" = "save_snapshot" ]; then
    PERSIST_DIR="/etc/dailyreport/data"
    mkdir -p "$PERSIST_DIR" 2>/dev/null
    command -v nlbw >/dev/null 2>&1 && nlbw -c commit >/dev/null 2>&1
    [ -f "/tmp/usage.db" ] && cp -f "/tmp/usage.db" "$PERSIST_DIR/usage.db" 2>/dev/null
    [ -f "/root/site_traffic.csv" ] && cp -f "/root/site_traffic.csv" "$PERSIST_DIR/site_traffic.csv" 2>/dev/null
    [ -f "/root/dns_cache.log" ] && cp -f "/root/dns_cache.log" "$PERSIST_DIR/dns_cache.log" 2>/dev/null
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
if [ "$DAYS" -gt 0 ]; then
    UPTIME_CN="${DAYS}天 ${HOURS}小时 ${MINS}分钟"
elif [ "$HOURS" -gt 0 ]; then
    UPTIME_CN="${HOURS}小时 ${MINS}分钟"
else
    UPTIME_CN="${MINS}分钟"
fi

WAN_IP=$(ifconfig pppoe-wan 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d: -f2)
[ -z "$WAN_IP" ] && WAN_IP=$(curl -s -m 2 http://ifconfig.me 2>/dev/null)
[ -z "$WAN_IP" ] && WAN_IP="获取失败"

DEVICE_COUNT=$(cat /tmp/dhcp.leases 2>/dev/null | wc -l)
DEVICE_TABLE_ROWS=""
while read -r line; do
    D_NAME=$(echo "$line" | awk '{print $4}')
    D_IP=$(echo "$line" | awk '{print $3}')
    [ "$D_NAME" = "*" ] && D_NAME="未知设备"
    DEVICE_TABLE_ROWS="${DEVICE_TABLE_ROWS}<tr><td style='border:1px solid #eee;padding:8px;'>$D_NAME</td><td style='border:1px solid #eee;padding:8px;font-family:monospace;'>$D_IP</td></tr>"
done < /tmp/dhcp.leases

# 2. 流量统计与网站排行 (执行前先触发一次实时增量采集更新)
[ -x /root/dns_cache_update.sh ] && /root/dns_cache_update.sh 2>/dev/null
[ -x /root/site_traffic_update.sh ] && /root/site_traffic_update.sh 2>/dev/null

TRAFFIC_HTML=""
TRAFFIC_SUMMARY_FILE="/tmp/dailyreport_traffic_summary.txt"
TRAFFIC_SCRIPT="/usr/share/dailyreport/dailyreport_traffic.py"
if [ -x "$TRAFFIC_SCRIPT" ]; then
    TRAFFIC_HTML=$(python3 "$TRAFFIC_SCRIPT" 2>/dev/null)
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
LOGIN_HTML="<span style='color:#52c41a;'>✅ 暂无新增登录</span>"
if [ -n "$NEW_LOGINS" ]; then
    FORMATTED_LOGINS=$(echo "$NEW_LOGINS" | awk '{
        if ($0 ~ /dropbear.*succeeded/) print "<div style=\"color:#1890ff; margin-bottom:4px;\">🔑 <b>SSH 成功：</b>" $0 "</div>";
        else if ($0 ~ /luci.*accepted/) print "<div style=\"color:#52c41a; margin-bottom:4px;\">🌐 <b>Web 登录：</b>" $0 "</div>";
        else print "<div style=\"color:#ffc107; margin-bottom:4px;\">🟡 <b>异常中断：</b>" $0 "</div>";
    }')
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

    # 4.1 被 banIP 拦截 (严格统计当天的增量：检查当天加入 blocklist 的 IP)
    TODAY_DATE=$(date "+%Y-%m-%d")
    awk -v d="$TODAY_DATE" '$0 ~ d {print $1}' /etc/banip/banip.blocklist 2>/dev/null > "$CURR_BANIP_FILE"
    
    # 增量比对 (测试模式下直接展示当前已捕获的当天增量)
    if [ "$1" = "test" ] || [ ! -f "$LAST_BANIP_FILE" ]; then
        NEW_BANIP=$(cat "$CURR_BANIP_FILE" 2>/dev/null)
    else
        NEW_BANIP=$(grep -vFf "$LAST_BANIP_FILE" "$CURR_BANIP_FILE" 2>/dev/null)
    fi

    BANIP_PART=""
    if [ -n "$NEW_BANIP" ]; then
        BANIP_ROWS=$(echo "$NEW_BANIP" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | sort | uniq -c | sort -nr | sed 's/^[[:space:]]*//; s/[[:space:]]/ 次 | IP: /')
        BANIP_COUNT=$(echo "$NEW_BANIP" | grep -c -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$')
        if [ "$BANIP_COUNT" -gt 0 ] && [ -n "$BANIP_ROWS" ]; then
            BANIP_PART="<div style='margin-bottom:12px;'><div style='font-size:13px;font-weight:600;color:#cf1322;margin-bottom:6px;'>🛡️ 被 banIP 拦截 (今日新增 ${BANIP_COUNT} 个恶意 IP)：</div><pre style='background:#fff1f0;border:1px solid #ffa39e;border-radius:6px;padding:10px;font-family:monospace;font-size:12px;color:#cf1322;margin:0;'>$BANIP_ROWS</pre></div>"
            echo "[SECURITY AUDIT] 成功捕获今日 banIP 新增封禁: $BANIP_COUNT 个"
        fi
    fi
    [ -z "$BANIP_PART" ] && BANIP_PART="<div style='margin-bottom:12px;font-size:13px;color:#52c41a;'>🛡️ banIP 拦截：✅ 今日暂无新增封禁</div>"

    # 4.2 进了防火墙黑名单的恶意扫描 (nftables / NAS-Port-Ban 动态防御)
    logread | grep -E 'NAS-Port-Ban' > "$CURR_NFT_FILE"
    if [ "$1" = "test" ] || [ ! -f "$LAST_NFT_FILE" ]; then
        NEW_NFT=$(cat "$CURR_NFT_FILE" 2>/dev/null)
    else
        NEW_NFT=$(grep -vFf "$LAST_NFT_FILE" "$CURR_NFT_FILE" 2>/dev/null)
    fi
    NFT_PART=""
    if [ -n "$NEW_NFT" ]; then
        NFT_ROWS=$(echo "$NEW_NFT" | sed -n 's/.*SRC=\([^ ]*\).*/\1/p' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | sort | uniq -c | sort -nr | sed 's/^[[:space:]]*//; s/[[:space:]]/ 次 | 恶意源: /')
        NFT_COUNT=$(echo "$NEW_NFT" | grep -c 'NAS-Port-Ban')
        if [ "$NFT_COUNT" -gt 0 ] && [ -n "$NFT_ROWS" ]; then
            NFT_PART="<div><div style='font-size:13px;font-weight:600;color:#d4380d;margin-bottom:6px;'>🚫 进防火墙黑名单/端口防扫 (今日新增 ${NFT_COUNT} 次拦截)：</div><pre style='background:#fff2e8;border:1px solid #ffbb96;border-radius:6px;padding:10px;font-family:monospace;font-size:12px;color:#d4380d;margin:0;'>$NFT_ROWS</pre></div>"
            BAN_SUMMARY="拦截: 发现新增恶意扫描"
            echo "[SECURITY AUDIT] 成功捕获今日防火墙黑名单新增扫描: $NFT_COUNT 次"
        fi
    fi
    [ -z "$NFT_PART" ] && NFT_PART="<div style='font-size:13px;color:#52c41a;'>🚫 防火墙黑名单：✅ 今日暂无新增入侵探测</div>"

    BAN_IP_HTML="<div style='padding:5px;'>${BANIP_PART}${NFT_PART}</div>"
fi

# 5. 构建邮件主题与正文
STATUS_TEXT="系统运行正常"
STATUS_ICON="🟢"
MAIL_SUBJECT="OpenWrt 状态与网络流量日报 - $DATE_NOW"

if [ "$TEMP_C" -ge "$ALERT_TEMP" ]; then
    STATUS_TEXT="🔥 严重过热告警！"
    STATUS_ICON="🔴"
    MAIL_SUBJECT="⚠️ 紧急：OpenWrt 过热报警 (${TEMP_C}°C) - $DATE_NOW"
elif [ "$1" = "test" ]; then
    MAIL_SUBJECT="OpenWrt 状态与网络流量测试报告 - $DATE_NOW"
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
                <div style='font-size:16px; margin-bottom:10px; border-bottom:1px dashed #ddd; padding-bottom:8px;'>📅 <b>当前时间：</b> <span style='color:#555;'>$DATE_NOW</span></div>
                <div style='font-size:16px; margin-bottom:10px; border-bottom:1px dashed #ddd; padding-bottom:8px;'>🌡️ <b>CPU 温度：</b> <strong style='color:$TEMP_COLOR;'>$TEMP_C°C</strong></div>
                <div style='font-size:16px; margin-bottom:10px; border-bottom:1px dashed #ddd; padding-bottom:8px;'>⏱️ <b>运行时间：</b> <span style='color:#555;'>$UPTIME_CN</span></div>
                <div style='font-size:16px;'>🏠 <b>公网 IP：</b> <strong style='color:#007bff; font-family:monospace;'>$WAN_IP</strong></div>
            </div>

            <h3 style='font-size:16px; color:#495057; border-left:4px solid #17a2b8; padding-left:10px; margin-bottom:15px;'>📱 在线设备 ($DEVICE_COUNT 台)</h3>
            <table style='width:100%; border-collapse:collapse; font-size:14px; margin-bottom:25px; text-align:left;'>
                <tr style='background:#e9ecef;'><th style='padding:8px; border:1px solid #dee2e6;'>设备名</th><th style='padding:8px; border:1px solid #dee2e6;'>IP 地址</th></tr>
                $DEVICE_TABLE_ROWS
            </table>

            <h3 style='font-size:16px; color:#495057; border-left:4px solid #28a745; padding-left:10px; margin-bottom:15px;'>👤 登录审计 (增量)</h3>
            <div style='margin-bottom:25px;'>$LOGIN_HTML</div>

            $TRAFFIC_HTML

            $([ -n "$BAN_IP_HTML" ] && echo "<h3 style='font-size:16px; color:#dc3545; border-left:4px solid #dc3545; padding-left:10px; margin-bottom:15px;'>🛡️ 联动处决报告</h3><div style='margin-bottom:25px;'>$BAN_IP_HTML</div>")
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
        # 常规状态推送 (Priority 5) — 提供与邮件对齐的全面多模块摘要
        MSG_TEXT=$(printf "📊 OpenWrt 状态与网络流量日报\n━━━━━━━━━━━━━━━\n⏱️ 运行: %s\n🌡️ CPU: %s°C | WAN IP: %s\n📱 在线设备: %s 台\n" "${UPTIME_CN}" "${TEMP_C}" "${WAN_IP}" "${DEVICE_COUNT}")
        if [ -n "$TRAFFIC_SUMMARY" ]; then
            MSG_TEXT=$(printf "%s\n%s\n" "${MSG_TEXT}" "${TRAFFIC_SUMMARY}")
        fi
        
        # 附带安全审计与处决摘要
        BAN_COUNT_BRIEF="拦截: 0"
        if [ -f "$CURR_BANIP_FILE" ] || [ -f "$CURR_NFT_FILE" ]; then
            B_CNT=$(cat "$CURR_BANIP_FILE" "$CURR_NFT_FILE" 2>/dev/null | wc -l)
            [ "$B_CNT" -gt 0 ] && BAN_COUNT_BRIEF="拦截: ${B_CNT} 次恶意探测"
        fi
        MSG_TEXT=$(printf "%s\n🛡️ 安全审计: %s | %s\n" "${MSG_TEXT}" "${LOGIN_SUMMARY:-登录审计正常}" "${BAN_COUNT_BRIEF}")

        G_RES=$(curl -s -w "\n%{http_code}" -X POST "${CLEAN_GOTIFY_URL}/message" \
             -H "X-Gotify-Key: ${GOTIFY_TOKEN}" \
             -F "title=${MAIL_SUBJECT}" \
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
