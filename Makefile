include $(TOPDIR)/rules.mk

LUCI_TITLE:=LuCI support for Daily Status & Traffic Report
LUCI_DEPENDS:=+python3 +curl +nlbwmon
LUCI_PKGARCH:=all

PKG_NAME:=luci-app-dailyreport
PKG_VERSION:=1.6.0
PKG_RELEASE:=1
PKG_MAINTAINER:=OpenWrt Community
PKG_LICENSE:=GPL-3.0

include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
