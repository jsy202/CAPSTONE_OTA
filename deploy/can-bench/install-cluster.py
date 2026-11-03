"""Run as root on the Cluster after Qt/Xvfb and the OTA venv are installed.

Installs only the explicitly named CAPSTONE bench services, not digital-dash.
Does not replace images, user keys, CAN wiring or an existing device journal.
"""
import grp
import os
from pathlib import Path
import pwd
import secrets
import shutil
import subprocess
import sys


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo using the installed OTA venv Python')
    source = Path(__file__).resolve().parent
    root = Path('/opt/capstone-cluster')
    if (root / 'selected-device').exists():
        raise SystemExit('Existing selected device detected; inspect before provisioning again')
    for name in ('capstone-can.service', 'capstone-cluster.service', 'capstone-cluster-display.service'):
        target = Path('/etc/systemd/system') / name
        if target.exists() and target.read_bytes() != (source / name).read_bytes():
            raise SystemExit('Refusing to replace a different unit: ' + name)
    try:
        account = pwd.getpwnam('capstone-app')
    except KeyError:
        subprocess.run(['useradd', '--system', '--user-group', '--home-dir', '/var/lib/capstone-app',
                        '--shell', '/usr/sbin/nologin', 'capstone-app'], check=True)
        account = pwd.getpwnam('capstone-app')
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    (root / 'runs').mkdir(mode=0o755, exist_ok=True)
    home = Path('/var/lib/capstone-app')
    home.mkdir(mode=0o750, exist_ok=True)
    os.chown(home, account.pw_uid, account.pw_gid)
    authority = home / 'Xauthority'
    if not authority.exists():
        subprocess.run(['xauth', '-f', str(authority), 'add', ':93', '.', secrets.token_hex(16)], check=True)
        authority.chmod(0o600)
        os.chown(authority, account.pw_uid, account.pw_gid)
    shutil.copy2(source / 'run-cluster', root / 'run-cluster')
    (root / 'run-cluster').chmod(0o755)
    for name in ('capstone-can.service', 'capstone-cluster.service', 'capstone-cluster-display.service'):
        shutil.copy2(source / name, Path('/etc/systemd/system') / name)
    wrapper = root / 'root-python'
    import shlex
    wrapper.write_text('#!/bin/sh\nexec sudo -n ' + shlex.quote(sys.executable) + ' "$@"\n')
    wrapper.chmod(0o755)
    subprocess.run(['systemd-analyze', 'verify', '/etc/systemd/system/capstone-can.service',
                    '/etc/systemd/system/capstone-cluster.service',
                    '/etc/systemd/system/capstone-cluster-display.service'], check=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'capstone-can.service',
                    'capstone-cluster-display.service'], check=True)
    # App service is started by the first real OTA trial, only after a slot exists.
    print('CAN and authenticated Xvfb ready; Cluster application awaits first OTA.')


if __name__ == '__main__':
    main()
