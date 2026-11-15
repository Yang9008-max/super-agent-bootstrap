#!/usr/bin/env bash
# Standalone bundles append a base64 ZIP after the payload marker below.
set -euo pipefail
umask 077
PAYLOAD_SHA256=""

die() { printf 'super-agent: %s\n' "$*" >&2; exit 1; }
fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --silent --show-error --connect-timeout 20 --max-time 300 "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget --quiet --timeout=30 -O "$2" "$1"
  else
    die 'curl or wget is required. Install one through your Linux package manager.'
  fi
}

usage() {
  cat <<'USAGE'
Usage: bash super-agent-personal.sh [--skip-codex] [--check-only]
   or: bash deploy/portable-agent-bootstrap.sh --profile /path/profile.json

Installs for the current Linux user. Existing files are backed up.
  --skip-codex  Restore configuration without downloading/installing Codex.
  --check-only Validate the embedded/external profile; do not apply it or install Codex.
               A usable Python may still be bootstrapped through uv.
USAGE
}

profile=""
skip_codex=0
check_only=0
while (($#)); do
  case "$1" in
    --profile) (($# >= 2)) || die '--profile needs a path'; profile="$2"; shift 2 ;;
    --skip-codex) skip_codex=1; shift ;;
    --check-only) check_only=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

[[ "$(uname -s)" == Linux ]] || die 'This bootstrap targets Linux. Exporting profiles is also supported on Windows.'
case "$(uname -m)" in x86_64|aarch64|arm64) ;; *) die 'Supported architectures: x86_64 and aarch64.' ;; esac
[[ -n "${HOME:-}" && -d "$HOME" ]] || die 'A writable user home is required.'
work="$(mktemp -d "${TMPDIR:-/tmp}/super-agent.XXXXXXXX")"
trap 'rm -rf -- "$work"' EXIT
data_dir="$HOME/.local/share/super-agent"
bin_dir="$HOME/.local/bin"
mkdir -p "$data_dir" "$bin_dir"

python_cmd=()
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3,11))' >/dev/null 2>&1; then
  python_cmd=("$(command -v python3)")
else
  uv_bin="$(command -v uv || true)"
  if [[ -z "$uv_bin" && -x "$data_dir/runtime/uv" ]]; then uv_bin="$data_dir/runtime/uv"; fi
  if [[ -z "$uv_bin" ]]; then
    printf 'Preparing a user-local Python runtime with uv...\n'
    fetch 'https://astral.sh/uv/install.sh' "$work/uv-install.sh"
    UV_UNMANAGED_INSTALL="$data_dir/runtime" sh "$work/uv-install.sh"
    uv_bin="$data_dir/runtime/uv"
  fi
  python_cmd=("$uv_bin" run --no-project --no-config --python 3.11 python)
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "$PAYLOAD_SHA256" ]]; then
  "${python_cmd[@]}" - "${BASH_SOURCE[0]}" "$work" "$PAYLOAD_SHA256" <<'PY'
import base64, hashlib, io, pathlib, sys, zipfile
source, destination, expected = sys.argv[1:]
text = pathlib.Path(source).read_text(encoding='utf-8')
payload = base64.b64decode(text.split('\n__SUPER_AGENT_PAYLOAD__\n', 1)[1])
if hashlib.sha256(payload).hexdigest() != expected:
    raise SystemExit('super-agent: embedded payload checksum mismatch')
with zipfile.ZipFile(io.BytesIO(payload)) as archive:
    required = {'portable_agent.py', 'portable-agent-bootstrap.sh', 'profile.json'}
    if set(archive.namelist()) != required or any(i.file_size > 256*1024*1024 for i in archive.infolist()):
        raise SystemExit('super-agent: invalid bundle contents')
    for name in required:
        pathlib.Path(destination, name).write_bytes(archive.read(name))
PY
  tool="$work/portable_agent.py"
  bootstrap="$work/portable-agent-bootstrap.sh"
  if [[ -z "$profile" ]]; then profile="$work/profile.json"; fi
else
  [[ -n "$profile" ]] || die 'Supply --profile or use a generated personal bundle.'
  tool="$script_dir/../tools/portable_agent.py"
  bootstrap="$script_dir/portable-agent-bootstrap.sh"
fi
[[ -f "$tool" && -f "$profile" ]] || die 'Profile or management script is missing.'
"${python_cmd[@]}" - "$tool" "$profile" "$data_dir" "$bin_dir/super-agent" <<'PY'
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location('portable_agent', sys.argv[1])
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
p = tool.read_profile(pathlib.Path(sys.argv[2]))
for name in ('portable_agent.py', 'portable-agent-bootstrap.sh', 'profile.json'):
    tool.check_destination(pathlib.Path(sys.argv[3], name))
tool.check_destination(pathlib.Path(sys.argv[4]))
print('Profile valid; personal skills:', len(p['skills']))
for warning in p.get('warnings', []):
    print('Note:', warning)
PY
if ((check_only)); then exit 0; fi

# Download/install before touching the user's Codex configuration.
export PATH="$bin_dir:$PATH"
if ! ((skip_codex)); then
  if ! command -v codex >/dev/null 2>&1; then
    printf 'Installing Codex CLI with its official installer...\n'
    fetch 'https://chatgpt.com/codex/install.sh' "$work/codex-install.sh"
    sh "$work/codex-install.sh"
    command -v codex >/dev/null 2>&1 || die 'Codex installation finished but codex is not on PATH; inspect installer output.'
  fi
  codex --version
fi

"${python_cmd[@]}" "$tool" apply "$profile"
"${python_cmd[@]}" - "$tool" "$profile" "$bootstrap" "$data_dir" "$bin_dir/super-agent" "${python_cmd[@]}" <<'PY'
import importlib.util, pathlib, shlex, sys
source, profile, bootstrap, data, launcher, *interpreter = sys.argv[1:]
spec = importlib.util.spec_from_file_location('portable_agent', source)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
root = pathlib.Path(data)
for name, path in (('portable_agent.py', source), ('profile.json', profile), ('portable-agent-bootstrap.sh', bootstrap)):
    tool.atomic_write(root / name, pathlib.Path(path).read_bytes())
saved_script = str(root / 'portable_agent.py')
if sys.platform == 'win32':
    # Enables isolated Git Bash integration tests; production uses POSIX paths.
    saved_script = saved_script.replace('\\', '/')
text = '#!/usr/bin/env bash\nexec ' + shlex.join([*interpreter, saved_script]) + ' "$@"\n'
tool.atomic_write(pathlib.Path(launcher), text.encode(), executable=True)
PY
"$bin_dir/super-agent" doctor
printf '\nEnvironment restored. Start Codex with: codex\n'
printf 'If ~/.local/bin is absent from PATH, run: export PATH="$HOME/.local/bin:$PATH"\n'
printf 'Check again: ~/.local/bin/super-agent doctor\n'
printf 'For custom providers, set the environment variables shown above. For OpenAI account access, run codex login.\n'
exit 0
