#!/usr/bin/env python3
"""Install an isolated private download route on the authorized Caddy host."""
import datetime
import grp
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time


def write(path, content, mode=0o640, group=None):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('Refusing a symlink in a deployment destination')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
        os.chmod(temporary, mode)
        if group is not None:
            os.chown(temporary, 0, group)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def active(name):
    return subprocess.run(['systemctl', 'is-active', '--quiet', name]).returncode == 0


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run this deployment through sudo')
    staging = Path(sys.argv[1]).resolve(strict=True)
    if staging.parent != Path('/home/ubuntu/.cache') or not re.fullmatch(r'portable-agent-upload-[0-9a-f]{12}', staging.name):
        raise SystemExit('Unexpected staging directory')
    state = json.loads((staging / 'state.json').read_text())
    hostname, token = state['hostname'], state['token']
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]+[a-z0-9]', hostname) or not re.fullmatch(r'[A-Za-z0-9_-]{32,64}', token):
        raise SystemExit('Invalid site hostname or download token')
    artifacts = {name: (staging / name).read_bytes() for name in ('install.sh', 'super-agent-personal.sh', 'super-agent-personal.sh.sha256')}
    digest = hashlib.sha256(artifacts['super-agent-personal.sh']).hexdigest()
    if digest != state['sha256'] or artifacts['super-agent-personal.sh.sha256'].decode().split() != [digest, 'super-agent-personal.sh']:
        raise SystemExit('Artifact checksum mismatch')
    caddy_group = grp.getgrnam('caddy').gr_gid
    main_config = Path('/etc/caddy/Caddyfile')
    site_config = Path('/etc/caddy/portable-agent.caddy')
    original = main_config.read_bytes()
    original_mode = stat.S_IMODE(main_config.stat().st_mode)
    original_site = site_config.read_bytes() if site_config.exists() else None
    if original_site is not None and f'/{token}/install.sh'.encode() not in original_site:
        raise SystemExit('A different portable-agent site already exists; inspect it before replacing')
    if not active('caddy'):
        raise SystemExit('Existing Caddy is not active; deployment stopped')
    qq_before = subprocess.check_output(['systemctl', 'show', 'qq-agent', '-p', 'MainPID', '--value'], text=True).strip()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('/var/backups/portable-agent') / stamp
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup.parent, 0o700)
    write(backup / 'Caddyfile', original, 0o600)
    if original_site is not None:
        write(backup / 'portable-agent.caddy', original_site, 0o600)
    data_root = Path('/srv/portable-agent')
    directory = data_root / token
    directory.mkdir(parents=True, exist_ok=True, mode=0o750)
    for path in (data_root, directory):
        os.chown(path, 0, caddy_group)
        os.chmod(path, 0o750)
    for name, content in artifacts.items():
        write(directory / name, content, group=caddy_group)
    paths = ' '.join('/' + token + '/' + name for name in artifacts)
    site = f'''{hostname} {{
    @download path {paths}
    handle @download {{
        root * /srv/portable-agent
        header Cache-Control "private, no-store"
        header X-Content-Type-Options "nosniff"
        file_server
    }}
    handle {{
        respond "Not found" 404
    }}
    log {{
        output discard
    }}
}}
'''
    import_line = b'import /etc/caddy/portable-agent.caddy'
    revised = original if import_line in original else original.rstrip() + b'\n\n' + import_line + b'\n'
    restarted = False
    try:
        write(site_config, site.encode(), group=caddy_group)
        write(main_config, revised, original_mode)
        result = subprocess.run(['runuser', '-u', 'caddy', '--', 'caddy', 'validate', '--config', str(main_config), '--adapter', 'caddyfile'], capture_output=True)
        write(backup / 'validation.log', result.stdout + result.stderr, 0o600)
        if result.returncode:
            raise RuntimeError('Caddy validation failed; details are in the root-only backup directory')
        # This host runs Caddy 2.6.2 with admin disabled; it cannot reload configuration.
        start = time.monotonic()
        restarted = True
        subprocess.run(['systemctl', 'restart', 'caddy'], check=True, capture_output=True)
        if not active('caddy'):
            raise RuntimeError('Caddy failed its post-start check')
        elapsed = round(time.monotonic() - start, 3)
    except Exception:
        write(main_config, original, original_mode)
        if original_site is None:
            site_config.unlink(missing_ok=True)
        else:
            write(site_config, original_site, group=caddy_group)
        if restarted:
            subprocess.run(['systemctl', 'restart', 'caddy'], check=True, capture_output=True)
        raise
    qq_after = subprocess.check_output(['systemctl', 'show', 'qq-agent', '-p', 'MainPID', '--value'], text=True).strip()
    print(json.dumps({'deployed': True, 'hostname': hostname, 'backup': str(backup),
                      'caddy_active': active('caddy'), 'activation_seconds': elapsed,
                      'qq_agent_active': active('qq-agent'), 'qq_process_preserved': qq_before == qq_after}))


if __name__ == '__main__':
    main()
