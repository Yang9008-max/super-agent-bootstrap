#!/usr/bin/env python3
"""Private, portable Codex profiles. Python 3.11+, standard library only."""
import argparse
import base64
import copy
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from urllib.parse import parse_qsl, urlsplit
import zipfile

VERSION = 1
PORTABLE_KEYS = {'model', 'model_provider', 'model_reasoning_effort',
                 'model_reasoning_summary', 'model_verbosity', 'personality'}
PROVIDER_KEYS = {'name', 'base_url', 'wire_api', 'env_key', 'env_key_instructions',
                 'requires_openai_auth', 'request_max_retries', 'stream_max_retries',
                 'stream_idle_timeout_ms', 'supports_websockets', 'query_params',
                 'env_http_headers'}
IGNORED_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.system', '.cache'}
SENSITIVE_NAME = re.compile(r'(^\.env($|\.)|auth\.json$|credentials|^id_(rsa|ed25519)|\.(pem|key|pfx)$)', re.I)
TOKEN = re.compile(rb'\b(?:sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{25,})\b')
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024


def load_toml(path):
    return tomllib.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def validate_url(value):
    if not isinstance(value, str):
        raise ValueError('Provider URL must be a string')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('Provider URL must use HTTP or HTTPS')
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Remove credentials/fragments from provider URLs; use an environment variable')
    if any(re.search(r'key|token|secret|auth|password|signature', key, re.I)
           for key, _ in parse_qsl(parsed.query)):
        raise ValueError('Remove credential query parameters from provider URLs')


def encoded_file(content, executable=False):
    return {'content': base64.b64encode(content).decode('ascii'), 'executable': executable}


def export_profile(codex_home, skills_roots):
    if not (codex_home / 'config.toml').is_file():
        raise ValueError('No config.toml found in the supplied Codex home')
    source = load_toml(codex_home / 'config.toml')
    config = {k: source[k] for k in PORTABLE_KEYS if k in source}
    warnings = []
    provider_id = config.get('model_provider', 'openai')
    if provider_id in source.get('model_providers', {}):
        original = source['model_providers'][provider_id]
        provider = {k: v for k, v in original.items() if k in PROVIDER_KEYS}
        if 'base_url' in provider:
            validate_url(provider['base_url'])
            if urlsplit(provider['base_url']).hostname != 'api.openai.com':
                provider['requires_openai_auth'] = False
                provider['env_key'] = provider.get('env_key') or 'OPENAI_API_KEY'
                warnings.append('Custom provider authentication uses an environment variable; credentials were not exported.')
        if set(original) - PROVIDER_KEYS:
            warnings.append('Non-portable provider fields (including literal headers/auth) were excluded.')
        config['model_providers'] = {provider_id: provider}
    elif provider_id != 'openai':
        raise ValueError('Selected custom provider is missing from model_providers')
    if source.get('mcp_servers'):
        warnings.append('MCP servers were not exported: their commands, paths and credentials need Linux-specific configuration.')
    if source.get('profiles'):
        warnings.append('Named Codex profiles were not exported; only the current default model/provider is included.')
    skills = {}
    total = 0
    for root in skills_roots:
        if not root.is_dir():
            continue
        for skill in sorted(root.iterdir()):
            if skill.name.startswith('.') or skill.is_symlink() or not skill.is_dir() or not (skill / 'SKILL.md').is_file():
                continue
            files = {}
            for parent, dirs, names in os.walk(skill, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not (Path(parent) / d).is_symlink())
                for name in sorted(names):
                    path = Path(parent) / name
                    relative = path.relative_to(skill).as_posix()
                    if path.is_symlink() or not path.is_file() or SENSITIVE_NAME.search(name):
                        warnings.append(f'Skipped credential-like file or symlink in skill {skill.name}.')
                        continue
                    if path.stat().st_size > MAX_FILE:
                        raise ValueError(f'A file in skill {skill.name} exceeds the 16 MiB limit')
                    data = path.read_bytes()
                    if TOKEN.search(data):
                        raise ValueError(f'Credential-like content found in skill {skill.name}; remove it before exporting')
                    total += len(data)
                    if total > MAX_TOTAL:
                        raise ValueError('Skills exceed the 128 MiB total limit')
                    executable = bool(path.stat().st_mode & stat.S_IXUSR) if os.name != 'nt' else path.suffix in ('.sh', '.bash')
                    files[relative] = encoded_file(data, executable)
            if skill.name in skills:
                warnings.append(f'Duplicate skill {skill.name}: the later skills root takes precedence.')
            skills[skill.name] = files
            if any(re.search(rb'[A-Za-z]:[\\/]|\.exe\b', base64.b64decode(record['content'])) for record in files.values()):
                warnings.append(f'Skill {skill.name} contains Windows-specific references; check its Linux prerequisites before use.')
    instructions = None
    if (codex_home / 'AGENTS.md').is_file():
        data = (codex_home / 'AGENTS.md').read_bytes()
        if TOKEN.search(data):
            raise ValueError('Credential-like content found in global instructions')
        instructions = encoded_file(data)
    required = sorted({p['env_key'] for p in config.get('model_providers', {}).values() if p.get('env_key')}
                      | {v for p in config.get('model_providers', {}).values() for v in p.get('env_http_headers', {}).values()})
    profile = {'version': VERSION, 'config': config, 'skills': skills,
               'instructions': instructions, 'required_env': required,
               'warnings': sorted(set(warnings))}
    validate_profile(profile)
    return profile


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value or '\x00' in value:
        raise ValueError('Invalid portable file path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError('Profile contains an unsafe file path')
    return path


def decode_file(record):
    if not isinstance(record, dict) or not isinstance(record.get('content'), str) or type(record.get('executable')) is not bool:
        raise ValueError('Invalid file record')
    if len(record['content']) > (MAX_FILE * 4 // 3 + 8):
        raise ValueError('File exceeds the 16 MiB limit')
    try:
        data = base64.b64decode(record['content'], validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError('Invalid base64 file content') from error
    if len(data) > MAX_FILE:
        raise ValueError('File exceeds the 16 MiB limit')
    return data


def validate_profile(profile):
    if not isinstance(profile, dict) or profile.get('version') != VERSION:
        raise ValueError('Unsupported portable profile version')
    config = profile.get('config')
    if not isinstance(config, dict) or set(config) - (PORTABLE_KEYS | {'model_providers'}):
        raise ValueError('Profile contains non-portable configuration keys')
    for key in PORTABLE_KEYS:
        if key in config and not isinstance(config[key], str):
            raise ValueError('Portable model settings must be strings')
    providers = config.get('model_providers', {})
    if not isinstance(providers, dict):
        raise ValueError('Invalid model_providers')
    for provider in providers.values():
        if not isinstance(provider, dict) or set(provider) - PROVIDER_KEYS:
            raise ValueError('Provider contains unsupported or literal secret fields')
        if 'base_url' in provider:
            validate_url(provider['base_url'])
        if 'env_key' in provider and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', provider['env_key']):
            raise ValueError('Invalid authentication environment variable name')
        for field in ('query_params', 'env_http_headers'):
            if field in provider:
                mapping = provider[field]
                if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
                    raise ValueError('Invalid provider parameter mapping')
                if field == 'query_params' and any(re.search(r'key|token|secret|auth|password|signature', k, re.I) for k in mapping):
                    raise ValueError('Credential query parameters cannot be exported')
                if field == 'env_http_headers' and any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', v) for v in mapping.values()):
                    raise ValueError('Headers must reference environment variable names')
    provider_id = config.get('model_provider', 'openai')
    if provider_id != 'openai' and provider_id not in providers:
        raise ValueError('Selected custom provider is missing')
    skills = profile.get('skills')
    if not isinstance(skills, dict):
        raise ValueError('Invalid skills mapping')
    total = 0
    paths = set()
    for name, files in skills.items():
        if len(safe_relative(name).parts) != 1 or name.startswith('.') or not isinstance(files, dict) or 'SKILL.md' not in files:
            raise ValueError('Invalid skill name or missing SKILL.md')
        for filename, record in files.items():
            relative = f'{name}/{safe_relative(filename).as_posix()}'
            parts = PurePosixPath(relative).parts
            if any('/'.join(parts[:i]) in paths for i in range(1, len(parts))):
                raise ValueError('Conflicting file/directory paths in skills')
            paths.add(relative)
            total += len(decode_file(record))
    for path in paths:
        if any(other.startswith(path + '/') for other in paths):
            raise ValueError('Conflicting file/directory paths in skills')
    if profile.get('instructions') is not None:
        total += len(decode_file(profile['instructions']))
    if total > MAX_TOTAL:
        raise ValueError('Profile exceeds the 128 MiB limit')
    if not isinstance(profile.get('required_env', []), list) or any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', v) for v in profile.get('required_env', [])):
        raise ValueError('Invalid required environment variable list')
    tomllib.loads(toml_dump(config))


def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value).lower()
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(v) for v in value) + ']'
    if isinstance(value, dict):
        return '{ ' + ', '.join(json.dumps(k, ensure_ascii=False) + ' = ' + toml_value(v) for k, v in value.items()) + ' }'
    raise ValueError('Unsupported TOML value type')


def toml_dump(doc):
    return ''.join(json.dumps(k, ensure_ascii=False) + ' = ' + toml_value(v) + '\n' for k, v in doc.items())


def merge_config(target, imported):
    result = copy.deepcopy(target)
    for key, value in imported.items():
        result[key] = merge_config(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else copy.deepcopy(value)
    return result


def check_destination(path):
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('Refusing to write through an existing symbolic link')
        if item != path and item.exists() and not item.is_dir():
            raise ValueError('A destination parent is not a directory')
    if path.exists() and not path.is_file():
        raise ValueError('A destination is not a regular file')


def atomic_write(path, content, executable=False):
    check_destination(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.super-agent-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
        os.chmod(temporary, 0o700 if executable else 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_profile(profile, home, codex_home):
    validate_profile(profile)
    home = Path(os.path.abspath(home))
    codex_home = Path(os.path.abspath(codex_home))
    config_path = codex_home / 'config.toml'
    check_destination(config_path)
    current = load_toml(config_path)
    merged = merge_config(current, profile['config'])
    writes = []
    if merged != current:
        writes.append((config_path, toml_dump(merged).encode('utf-8'), False, Path('codex/config.toml')))
    if profile.get('instructions'):
        writes.append((codex_home / 'AGENTS.md', decode_file(profile['instructions']), False, Path('codex/AGENTS.md')))
    for name, files in profile['skills'].items():
        for filename, record in files.items():
            relative = Path(name, *safe_relative(filename).parts)
            writes.append((home / '.agents/skills' / relative, decode_file(record), record['executable'], Path('skills') / relative))
    pending = []
    for path, data, executable, backup_relative in writes:
        check_destination(path)
        previous = path.read_bytes() if path.exists() else None
        previous_mode = stat.S_IMODE(path.stat().st_mode) if previous is not None else None
        mode_ok = os.name == 'nt' or previous_mode == (0o700 if executable else 0o600)
        if previous != data or not mode_ok:
            pending.append((path, data, executable, backup_relative, previous, previous_mode))
    if not pending:
        return {'changed': 0, 'backup': None, 'skills': len(profile['skills'])}
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup = home / '.local/state/super-agent/backups' / stamp
    # Complete validation and backup before modifying any configuration files.
    for path, data, executable, relative, previous, mode in pending:
        if previous is not None:
            atomic_write(backup / relative, previous, bool(mode & stat.S_IXUSR))
    manifest = [{'target': str(p), 'backup': str(r) if old is not None else None} for p, _, _, r, old, _ in pending]
    atomic_write(backup / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode())
    applied = []
    try:
        for path, data, executable, relative, previous, mode in pending:
            atomic_write(path, data, executable)
            applied.append((path, previous, mode))
    except Exception:
        for path, previous, mode in reversed(applied):
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, previous, bool(mode & stat.S_IXUSR))
                os.chmod(path, mode)
        raise
    return {'changed': len(pending), 'backup': str(backup), 'skills': len(profile['skills'])}


def doctor(codex_home, env=None):
    env = os.environ if env is None else env
    config = load_toml(codex_home / 'config.toml')
    provider = config.get('model_providers', {}).get(config.get('model_provider', 'openai'), {})
    required = {provider['env_key']} if provider.get('env_key') else set()
    required.update(provider.get('env_http_headers', {}).values())
    codex = shutil.which('codex')
    result = {'config_exists': (codex_home / 'config.toml').is_file(), 'codex_installed': bool(codex),
              'missing_env': sorted(k for k in required if not env.get(k)),
              'authentication': 'environment' if required else 'run codex login',
              'network_tested': False}
    if codex:
        try:
            version = subprocess.run([codex, '--version'], capture_output=True, text=True, timeout=15)
            result['codex_runs'] = version.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            result['codex_runs'] = False
    return result


def bundle(profile, destination):
    validate_profile(profile)
    root = Path(__file__).resolve().parents[1]
    bootstrap = root / 'deploy/portable-agent-bootstrap.sh'
    if not bootstrap.is_file():
        bootstrap = Path(__file__).with_name('portable-agent-bootstrap.sh')
    script = bootstrap.read_text(encoding='utf-8')
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('profile.json', json.dumps(profile, ensure_ascii=False))
        archive.writestr('portable_agent.py', Path(__file__).read_bytes())
        archive.writestr('portable-agent-bootstrap.sh', script)
    payload = memory.getvalue()
    script = script.replace('PAYLOAD_SHA256=""', 'PAYLOAD_SHA256="' + hashlib.sha256(payload).hexdigest() + '"', 1)
    output = script.rstrip() + '\n__SUPER_AGENT_PAYLOAD__\n' + base64.encodebytes(payload).decode('ascii')
    if destination.exists():
        raise ValueError('Bundle already exists; choose a new output path')
    atomic_write(destination, output.encode('utf-8'), executable=True)


def read_profile(path):
    if path.stat().st_size > MAX_TOTAL * 2:
        raise ValueError('Profile file is too large')
    profile = json.loads(path.read_text(encoding='utf-8-sig'))
    validate_profile(profile)
    return profile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    default_codex = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    export = commands.add_parser('export', help='Export personal settings and skills without login credentials')
    export.add_argument('--codex-home', type=Path, default=default_codex)
    export.add_argument('--skills-root', type=Path, action='append', help='Replace the default personal skills search roots')
    export.add_argument('--output', type=Path, required=True)
    apply = commands.add_parser('apply', help='Restore a profile, with backups and no credential export')
    apply.add_argument('profile', type=Path)
    apply.add_argument('--home', type=Path, default=Path.home())
    apply.add_argument('--codex-home', type=Path)
    bundler = commands.add_parser('bundle', help='Make a self-contained Linux installer')
    bundler.add_argument('profile', type=Path)
    bundler.add_argument('--output', type=Path, required=True)
    check = commands.add_parser('doctor', help='Check local configuration and required environment variable names')
    check.add_argument('--codex-home', type=Path, default=default_codex)
    args = parser.parse_args(argv)
    try:
        if args.command == 'export':
            if args.output.exists():
                raise ValueError('Output already exists; choose a new output path')
            roots = args.skills_root or [args.codex_home / 'skills', Path.home() / '.agents/skills']
            profile = export_profile(args.codex_home, roots)
            atomic_write(args.output, (json.dumps(profile, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
            result = {'output': str(args.output), 'skills': len(profile['skills']),
                      'required_env': profile['required_env'], 'warnings': profile['warnings']}
        elif args.command == 'apply':
            codex_home = args.codex_home or (Path(os.environ['CODEX_HOME']) if 'CODEX_HOME' in os.environ else args.home / '.codex')
            result = apply_profile(read_profile(args.profile), args.home, codex_home)
        elif args.command == 'bundle':
            bundle(read_profile(args.profile), args.output)
            result = {'output': str(args.output), 'bytes': args.output.stat().st_size}
        else:
            result = doctor(args.codex_home)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, tomllib.TOMLDecodeError) as error:
        # TOML parser errors can include configuration text; never echo their context.
        if isinstance(error, tomllib.TOMLDecodeError):
            message = 'Invalid TOML configuration; repair config.toml before continuing'
        elif isinstance(error, OSError):
            message = f'Filesystem operation failed ({type(error).__name__}); check paths and permissions'
        else:
            message = str(error)
        print('super-agent: ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
