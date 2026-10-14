# luci-app-dailyreport

OpenWrt 智能系统状态与网络流量日报 LuCI 插件 (Daily Report & Security Audit for OpenWrt)。

## ✨ 功能特性

- 📶 **公网流量全景**：基于 `nlbwmon` 精准统计今日全网公网吞吐，彻底过滤局域网内部海量备份与无端口裸流，准确呈现各终端真实公网消耗。
- 🌐 **热门网站访问排行**：精准统计访问流量 Top 15 热门网站。
- 🛡️ **安全审计与联动处决**：
  - 实时捕获 **banIP** 当日新增恶意 IP 封禁。
  - 联动 **nftables 防火墙** 捕获端口防扫（NAS-Port-Ban）动态黑名单增量。
- 🌡️ **硬件监控与高温保护**：实时采集 CPU 温度，支持超温（可自定义阈值，默认 75°C）邮件/Gotify 即时告警与可选 3 分钟倒计时保护关机。
- 📬 **双通道富文本推送**：支持自定义 SMTP 邮件推送（HTML 卡片布局）与 Gotify 消息通知。
- 🧪 **一键测试**：后台支持「立即生成并发送日报」实时调试。

## 📦 安装说明

下载编译好的 `.ipk` 包，上传至 OpenWrt 后执行：

```bash
opkg update
opkg install luci-app-dailyreport_1.0.0-1_all.ipk
/etc/init.d/rpcd reload
```

## ⚙️ 配置说明

安装后进入 OpenWrt Web 界面：
**【状态】 -> 【系统日报】**，按需开启并配置邮件/Gotify 推送凭据即可。

## 📄 许可证

Licensed under the Apache-2.0 License.
