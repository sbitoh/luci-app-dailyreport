#!/bin/sh
# LuCI「保存并应用」的后端动作：
#   1) 把 /tmp/.uci 里的暂存改动真正提交到 /etc/config/dailyreport
#   2) 重启服务，让推送时间等设置立刻重写 crontab 并生效
# 正常输出 "applied"；失败返回非 0，由前端提示。

uci -q commit dailyreport || {
	echo "commit failed" >&2
	exit 1
}

/etc/init.d/dailyreport reload >/dev/null 2>&1 || /etc/init.d/dailyreport restart >/dev/null 2>&1

echo "applied"
exit 0