import tarfile, os, io, time

pkg_dir = '/home/sbitoh/luci-app-dailyreport'
bin_dir = os.path.join(pkg_dir, 'bin/packages')
os.makedirs(bin_dir, exist_ok=True)

version = "1.6.0"
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
        if '__pycache__' in root:
            continue
        for f in files:
            if f.endswith('.bak') or '.bak.' in f or f.endswith('.pyc'):
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
            if f.endswith('.bak') or '.bak.' in f or f.startswith('config_v') or f == 'config_view.js':
                continue
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

# 2. 构建兼容 APK v2 / v3 的标准 APK 包 (使用官方 apk mkpkg 工具)
import subprocess, shutil, tempfile

apk_post_install = '''#!/bin/sh
/etc/init.d/rpcd reload 2>/dev/null || /etc/init.d/rpcd restart 2>/dev/null
/etc/init.d/dailyreport enable 2>/dev/null || true
/etc/init.d/dailyreport start 2>/dev/null || true
rm -rf /tmp/luci-indexcache* /tmp/luci-modulecache*
exit 0
'''

staging_dir = os.path.join(tempfile.gettempdir(), 'apk_staging_dailyreport')
if os.path.exists(staging_dir):
    shutil.rmtree(staging_dir)
os.makedirs(staging_dir, exist_ok=True)

# 复制 root
for root, dirs, files in os.walk(root_path):
    if '__pycache__' in root:
        continue
    for f in files:
        if f.endswith('.bak') or '.bak.' in f or f.endswith('.pyc'):
            continue
        src = os.path.join(root, f)
        rel = os.path.relpath(src, root_path)
        dst = os.path.join(staging_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        mode = 0o755 if (f.endswith('.sh') or f.endswith('.py') or 'init.d' in root) else 0o644
        os.chmod(dst, mode)

# 复制 htdocs
for root, dirs, files in os.walk(htdocs_path):
    for f in files:
        if f.endswith('.bak') or '.bak.' in f or f.startswith('config_v') or f == 'config_view.js':
            continue
        src = os.path.join(root, f)
        rel = os.path.relpath(src, htdocs_path)
        dst = os.path.join(staging_dir, 'www', rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o644)

post_script_path = os.path.join(tempfile.gettempdir(), 'apk_post_install.sh')
with open(post_script_path, 'w') as f:
    f.write(apk_post_install)
os.chmod(post_script_path, 0o755)

docker_cmd = [
    'docker', 'run', '--rm',
    '-v', f'{staging_dir}:/staging:ro',
    '-v', f'{post_script_path}:/post.sh:ro',
    '-v', f'{bin_dir}:/out',
    'alpine:latest',
    'apk', 'mkpkg',
    '--compat', '2',
    '--output', f'/out/luci-app-dailyreport-{version}-r{release}.apk',
    '--files', '/staging',
    '--info', 'name:luci-app-dailyreport',
    '--info', f'version:{version}-r{release}',
    '--info', 'description:OpenWrt Daily Status & Traffic Report with LuCI Web UI',
    '--info', 'arch:noarch',
    '--info', 'license:GPL-3.0',
    '--info', 'depends:python3 curl',
    '--script', 'post-install:/post.sh'
]

res = subprocess.run(docker_cmd, capture_output=True, text=True)
if res.returncode != 0:
    print("APK build error:", res.stderr)
else:
    print(f"APK built successfully: {out_apk} ({os.path.getsize(out_apk)} bytes)")

# 清理临时文件
shutil.rmtree(staging_dir, ignore_errors=True)
if os.path.exists(post_script_path):
    os.remove(post_script_path)
