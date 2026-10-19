import tarfile, os, io, time

pkg_dir = '/home/sbitoh/luci-app-dailyreport'
bin_dir = os.path.join(pkg_dir, 'bin/packages')
os.makedirs(bin_dir, exist_ok=True)

version = "1.3.2"
release = "1"
out_ipk = os.path.join(bin_dir, f'luci-app-dailyreport_{version}-{release}_all.ipk')
out_apk = os.path.join(bin_dir, f'luci-app-dailyreport-{version}-r{release}.apk')

deb_binary = b'2.0\n'

control_content = f'''Package: luci-app-dailyreport
Version: {version}-{release}
Depends: python3, curl, nlbwmon
Section: luci
Architecture: all
Maintainer: OpenWrt Community
Description: OpenWrt Daily Status & Traffic Report with LuCI Web UI
'''

conffiles_content = '''/etc/config/dailyreport
'''

postinst_content = '''#!/bin/sh
[ -n "${IPKG_INSTROOT}" ] || {
	/etc/init.d/rpcd reload 2>/dev/null || /etc/init.d/rpcd restart 2>/dev/null
	/etc/init.d/dailyreport enable 2>/dev/null || true
	/etc/init.d/dailyreport start 2>/dev/null || true
	/etc/init.d/dailyreport reload 2>/dev/null || true
	rm -rf /tmp/luci-indexcache* /tmp/luci-modulecache*
	exit 0
}
'''

prerm_content = '''#!/bin/sh
[ -n "${IPKG_INSTROOT}" ] || {
	/etc/init.d/dailyreport stop 2>/dev/null || true
	/etc/init.d/dailyreport disable 2>/dev/null || true
	exit 0
}
'''

# 1. 构建 IPK
control_buf = io.BytesIO()
with tarfile.open(fileobj=control_buf, mode='w:gz', format=tarfile.GNU_FORMAT) as tar:
    def add_mem_file(name, data, mode=0o644):
        ti = tarfile.TarInfo(name=name)
        ti.size = len(data)
        ti.mode = mode
        tar.addfile(ti, io.BytesIO(data))
    
    add_mem_file('./control', control_content.encode('utf-8'))
    add_mem_file('./conffiles', conffiles_content.encode('utf-8'))
    add_mem_file('./postinst', postinst_content.encode('utf-8'), mode=0o755)
    add_mem_file('./prerm', prerm_content.encode('utf-8'), mode=0o755)

control_tar_gz = control_buf.getvalue()

data_buf = io.BytesIO()
with tarfile.open(fileobj=data_buf, mode='w:gz', format=tarfile.GNU_FORMAT) as data_tar:
    dirs_added = set()
    def ensure_parents(p):
        parts = p.split('/')
        cur = '.'
        for part in parts[:-1]:
            if part:
                cur += '/' + part
                if cur not in dirs_added:
                    ti = tarfile.TarInfo(name=cur)
                    ti.type = tarfile.DIRTYPE
                    ti.mode = 0o755
                    data_tar.addfile(ti)
                    dirs_added.add(cur)

    root_path = os.path.join(pkg_dir, 'root')
    for root, dirs, files in os.walk(root_path):
        for f in files:
            if f.endswith('.bak') or '.bak.' in f:
                continue
            full_p = os.path.join(root, f)
            rel_p = os.path.relpath(full_p, root_path)
            tar_name = './' + rel_p
            ensure_parents(tar_name)
            mode = 0o755 if (f.endswith('.sh') or f.endswith('.py') or 'init.d' in root) else 0o644
            ti = tarfile.TarInfo(name=tar_name)
            ti.size = os.path.getsize(full_p)
            ti.mode = mode
            with open(full_p, 'rb') as fp:
                data_tar.addfile(ti, fp)

    htdocs_path = os.path.join(pkg_dir, 'htdocs')
    for root, dirs, files in os.walk(htdocs_path):
        for f in files:
            full_p = os.path.join(root, f)
            tar_name = './www/' + os.path.relpath(full_p, htdocs_path)
            ensure_parents(tar_name)
            ti = tarfile.TarInfo(name=tar_name)
            ti.size = os.path.getsize(full_p)
            ti.mode = 0o644
            with open(full_p, 'rb') as fp:
                data_tar.addfile(ti, fp)

data_tar_gz = data_buf.getvalue()

with tarfile.open(out_ipk, 'w:gz', format=tarfile.GNU_FORMAT) as ipk_tar:
    def add_tar_item(name, data):
        ti = tarfile.TarInfo(name=name)
        ti.size = len(data)
        ti.mode = 0o644
        ipk_tar.addfile(ti, io.BytesIO(data))

    add_tar_item('debian-binary', deb_binary)
    add_tar_item('control.tar.gz', control_tar_gz)
    add_tar_item('data.tar.gz', data_tar_gz)

print(f"IPK built successfully: {out_ipk} ({os.path.getsize(out_ipk)} bytes)")

# 2. 构建 APK
apk_pkginfo = f'''pkgname = luci-app-dailyreport
pkgver = {version}-r{release}
pkgdesc = System status, daily traffic report and security audit LuCI app for OpenWrt
url = https://github.com/sbitoh/luci-app-dailyreport
builddate = {int(time.time())}
packager = OpenWrt Community
size = 70000
arch = noarch
license = Apache-2.0
depend = python3 curl
'''

apk_post_install = '''#!/bin/sh
/etc/init.d/rpcd reload 2>/dev/null || /etc/init.d/rpcd restart 2>/dev/null
/etc/init.d/dailyreport enable 2>/dev/null || true
/etc/init.d/dailyreport start 2>/dev/null || true
rm -rf /tmp/luci-indexcache* /tmp/luci-modulecache*
exit 0
'''

with tarfile.open(out_apk, 'w:gz', format=tarfile.GNU_FORMAT) as apk_tar:
    def add_apk_mem(name, data, mode=0o644):
        ti = tarfile.TarInfo(name=name)
        ti.size = len(data)
        ti.mode = mode
        apk_tar.addfile(ti, io.BytesIO(data))

    add_apk_mem('.PKGINFO', apk_pkginfo.encode('utf-8'))
    add_apk_mem('.post-install', apk_post_install.encode('utf-8'), mode=0o755)

    apk_dirs_added = set()
    def ensure_apk_parents(p):
        parts = p.split('/')
        cur = ''
        for part in parts[:-1]:
            if part:
                cur = (cur + '/' + part) if cur else part
                if cur not in apk_dirs_added:
                    ti = tarfile.TarInfo(name=cur)
                    ti.type = tarfile.DIRTYPE
                    ti.mode = 0o755
                    apk_tar.addfile(ti)
                    apk_dirs_added.add(cur)

    for root, dirs, files in os.walk(root_path):
        for f in files:
            if f.endswith('.bak') or '.bak.' in f:
                continue
            full_p = os.path.join(root, f)
            rel_p = os.path.relpath(full_p, root_path)
            ensure_apk_parents(rel_p)
            mode = 0o755 if (f.endswith('.sh') or f.endswith('.py') or 'init.d' in root) else 0o644
            ti = tarfile.TarInfo(name=rel_p)
            ti.size = os.path.getsize(full_p)
            ti.mode = mode
            with open(full_p, 'rb') as fp:
                apk_tar.addfile(ti, fp)

    for root, dirs, files in os.walk(htdocs_path):
        for f in files:
            full_p = os.path.join(root, f)
            rel_p = 'www/' + os.path.relpath(full_p, htdocs_path)
            ensure_apk_parents(rel_p)
            ti = tarfile.TarInfo(name=rel_p)
            ti.size = os.path.getsize(full_p)
            ti.mode = 0o644
            with open(full_p, 'rb') as fp:
                apk_tar.addfile(ti, fp)

print(f"APK built successfully: {out_apk} ({os.path.getsize(out_apk)} bytes)")
