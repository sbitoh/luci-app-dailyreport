# luci-app-dailyreport

[English](README.md) | [中文说明](README_zh.md)

OpenWrt Intelligent System Health, WAN Traffic & Security Audit Daily Report LuCI Application.

## ✨ Features

- 📶 **Comprehensive WAN Traffic**: Powered by `nlbwmon`. Accurately records total daily public WAN throughput while completely isolating LAN intra-network transfers and raw/portless streams.
- 📱 **Client Breakdown**: Fair and universal accounting for all LAN devices (NAS, phones, PCs, and dynamically detected new devices).
- 🌐 **Top 15 Visited Websites**: Identifies and ranks top bandwidth-consuming external domains.
- 🛡️ **Security Audit & Execution Report**:
  - Real-time tracking of new daily **banIP** blocklist drops.
  - Integration with **nftables firewall** to catch port-scanning attempts against exposed services (`NAS-Port-Ban`).
- 🌡️ **Hardware Health & Overheat Protection**: Monitors CPU temperature, triggers instant critical alerts if exceeding threshold (default: 75°C), with optional safe 3-minute automatic shutdown.
- 📬 **Dual-Channel Rich Notifications**: Formatted HTML email cards via standard SMTP (using Python native `smtplib`) and instant Gotify push alerts.
- 🧪 **One-Click Test**: Built-in test trigger in LuCI Web UI for instant end-to-end diagnostics.

## 📦 Installation

This package is pure JavaScript/Shell/Python3 and uses architecture `all` (compatible with **x86_64, ARM64, MIPS, and all OpenWrt routers**).

### Download Pre-built IPK

Download directly from the repository:
- [Download luci-app-dailyreport_1.0.0-1_all.ipk](https://raw.githubusercontent.com/sbitoh/luci-app-dailyreport/main/bin/packages/luci-app-dailyreport_1.0.0-1_all.ipk)

Or install directly via OpenWrt terminal:
```bash
wget -O /tmp/luci-app-dailyreport.ipk https://raw.githubusercontent.com/sbitoh/luci-app-dailyreport/main/bin/packages/luci-app-dailyreport_1.0.0-1_all.ipk
opkg install /tmp/luci-app-dailyreport.ipk
/etc/init.d/rpcd reload
```

## ⚙️ Configuration

In OpenWrt Web interface, navigate to:
**【Services】 -> 【System & Traffic Daily Report】** (服务 -> 系统与流量日报)

1. Enable the daily report service and choose your preferred daily dispatch hour (e.g., `08:00`).
2. Configure your Email (SMTP) and/or Gotify server credentials.
3. Click **Save & Apply**, then click **Generate & Send Report Now** to run an immediate test.

## 📄 License

Licensed under the Apache-2.0 License.
