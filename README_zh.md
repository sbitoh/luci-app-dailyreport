# luci-app-dailyreport

[English](README.md) | [中文说明](README_zh.md)

OpenWrt 智能系统状态与网络流量日报 LuCI 插件 (Daily Report & Security Audit for OpenWrt)。

## ✨ 功能特性

- 📶 **公网流量全景**：基于 `nlbwmon` 精准统计今日全网公网吞吐，彻底过滤局域网内部海量备份与无端口裸流，准确呈现各终端真实公网消耗。
- 📱 **客户端公平统计**：全网设备平权统计（NAS、手机、PC 与新接入设备动态识别）。
- 🌐 **热门网站访问排行**：精准统计访问流量 Top 15 热门网站。
- 🛡️ **安全审计与联动处决**：
  - 实时捕获 **banIP** 当日新增恶意 IP 封禁。
  - 联动 **nftables 防火墙** 捕获端口防扫（NAS-Port-Ban）动态黑名单增量。
- 🌡️ **硬件监控与高温保护**：实时采集 CPU 温度，支持超温（可自定义阈值，默认 75°C）邮件/Gotify 即时告警与可选 3 分钟倒计时保护关机。
- 📬 **双通道富文本推送**：支持自定义 SMTP 邮件推送（HTML 卡片布局）与 Gotify 消息通知。
- 🧪 **一键测试**：后台支持「立即生成并发送日报」实时调试。

## 📦 安装说明

本插件采用纯脚本（JavaScript/Shell/Python3）架构，架构标识为 `all`，**全平台通用**（无论 x86_64、ARM64 还是 MIPS 等架构路由器均可直接安装使用）。

### 1. 下载预编译安装包

你可以直接从本仓库下载预编译好的 IPK 包：
- [点击下载 luci-app-dailyreport_1.0.0-1_all.ipk](https://raw.githubusercontent.com/sbitoh/luci-app-dailyreport/main/bin/packages/luci-app-dailyreport_1.0.0-1_all.ipk)

或者直接在 OpenWrt 终端一键下载安装：
```bash
wget -O /tmp/luci-app-dailyreport.ipk https://raw.githubusercontent.com/sbitoh/luci-app-dailyreport/main/bin/packages/luci-app-dailyreport_1.0.0-1_all.ipk
opkg install /tmp/luci-app-dailyreport.ipk
/etc/init.d/rpcd reload
```

## ⚙️ 配置说明

安装后进入 OpenWrt Web 界面：
**【服务】 -> 【系统与流量日报】** (Services -> System & Traffic Daily Report)

1. 开启日报服务并选择每日发送时间（如 `08:00`）。
2. 按需配置邮件推送（SMTP）或 Gotify 服务器凭据。
3. 点击 **保存并应用**，点击 **立即生成并发送日报** 即可立即测试。

## 📄 许可证

Licensed under the Apache-2.0 License.
