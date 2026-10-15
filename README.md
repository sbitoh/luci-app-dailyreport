# luci-app-dailyreport

[English](README.md) | [中文说明](README_zh.md)

OpenWrt Intelligent System Health, WAN Traffic & Security Audit Daily Report LuCI Application.

## 📸 Screenshots

| Email HTML Report Card (Rich Visual Layout) | Gotify Push Notification (Real-time Alert) |
| :---: | :---: |
| <img src="assets/email_preview.png" width="450" /> | <img src="assets/gotify_preview.jpg" width="350" /> |

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
- [Download luci-app-dailyreport_1.1.0-1_all.ipk](https://raw.githubusercontent.com/sbitoh/luci-app-dailyreport/main/bin/packages/luci-app-dailyreport_1.1.0-1_all.ipk)

Or install directly via OpenWrt terminal:
```bash
wget -O /tmp/luci-app-dailyreport.ipk https://raw.githubusercontent.com/sbitoh/luci-app-dailyreport/main/bin/packages/luci-app-dailyreport_1.1.0-1_all.ipk
opkg install /tmp/luci-app-dailyreport.ipk
/etc/init.d/rpcd reload
```

## ⚙️ Configuration

In OpenWrt Web interface, navigate to:
**【Services】 -> 【System & Traffic Daily Report】** (服务 -> 系统与流量日报)

1. Enable the daily report service and choose your preferred daily dispatch hour (e.g., `08:00`).
2. Configure your Email (SMTP) and/or Gotify server credentials.
3. Click **Save & Apply**, then click **Generate & Send Report Now** to run an immediate test.

## 📝 Release Notes

### v1.1.0 (2026-10-15)
* 🌐 **Top-level Language Switch**: Added an explicit language switcher at the top of the General Settings tab. Both the Web UI and outgoing reports (HTML Email & Gotify notifications) fully adapt to Simplified Chinese or English.
* 💾 **Configuration Persistence**: Integrated `conffiles` packaging support to ensure existing credentials and custom settings are preserved during upgrades or reinstalls.
* 📱 **Client Ranking & Lifecycle Architecture**:
  * **MAC-based Identity**: Tracks clients by physical MAC addresses rather than volatile IPs, preventing metric fragmentation across disconnects and IP renegotiations.
  * **Dynamic Device Discovery**: Automatically catches new active clients (guests, new hardware) and gracefully resolves hostnames via DHCP leases.
  * **Unified Online Device Detection**: Combines kernel ARP neighbor cache (`ip neigh`) with static lease definitions to accurately report online status for servers (e.g. NAS) and static devices.
* 📶 **WAN Traffic Convergence & Reset Guard**:
  * Implemented WAN hardware boundary capping to prevent intra-LAN backup traffic from skewing client summaries.
  * Added automated midnight (00:00) counter rollbacks to eliminate cross-day traffic accumulation.
  * Enhanced Flash snapshot persistence to ensure data continuity across router reboots.

---

## 📄 License

Licensed under the Apache-2.0 License.
