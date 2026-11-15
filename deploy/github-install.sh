#!/usr/bin/env bash
# Public entry point. Personal settings are downloaded from a private Release.
set -euo pipefail
umask 077
repository='Yang9008-max/super-agent-personal'
release_tag=''
bundle_args=()
die() { printf 'super-agent: %s\n' "$*" >&2; exit 1; }
fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --silent --show-error --connect-timeout 20 --max-time 300 "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget --quiet --timeout=30 -O "$2" "$1"
  else
    die 'Please install curl or wget first.'
  fi
}
while (($#)); do
  case "$1" in
    --repo) (($# >= 2)) || die '--repo needs OWNER/REPO'; repository="$2"; shift 2 ;;
    --tag) (($# >= 2)) || die '--tag needs a release tag'; release_tag="$2"; shift 2 ;;
    --skip-codex|--check-only) bundle_args+=("$1"); shift ;;
    --help|-h)
      printf 'Usage: install.sh [--repo OWNER/REPO] [--tag TAG] [--skip-codex] [--check-only]\n'
      printf 'Linux installer. On first use, follow the GitHub browser login instructions.\n'
      exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done
[[ "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die 'Invalid GitHub repository name.'
[[ "$(uname -s)" == Linux ]] || die 'This installer supports Linux.'
case "$(uname -m)" in
  x86_64) gh_arch=amd64 ;;
  aarch64|arm64) gh_arch=arm64 ;;
  *) die 'Supported architectures: x86_64 and aarch64.' ;;
esac
[[ -n "${HOME:-}" && -d "$HOME" ]] || die 'A user home is required.'
work="$(mktemp -d "${TMPDIR:-/tmp}/super-agent-github.XXXXXXXX")"
trap 'rm -rf -- "$work"' EXIT
data_dir="$HOME/.local/share/super-agent"
mkdir -p "$data_dir"

printf '\n[1/4] Preparing the installer runtime...\n'
python_cmd=()
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3,11))' >/dev/null 2>&1; then
  python_cmd=("$(command -v python3)")
else
  uv_bin="$(command -v uv || true)"
  if [[ -z "$uv_bin" && -x "$data_dir/runtime/uv" ]]; then uv_bin="$data_dir/runtime/uv"; fi
  if [[ -z "$uv_bin" ]]; then
    fetch 'https://astral.sh/uv/install.sh' "$work/uv-install.sh"
    UV_UNMANAGED_INSTALL="$data_dir/runtime" sh "$work/uv-install.sh"
    uv_bin="$data_dir/runtime/uv"
  fi
  python_cmd=("$uv_bin" run --no-project --no-config --python 3.11 python)
fi

gh_bin="$(command -v gh || true)"
if [[ -z "$gh_bin" && -x "$data_dir/github/bin/gh" ]]; then gh_bin="$data_dir/github/bin/gh"; fi
if [[ -z "$gh_bin" ]]; then
  printf 'Installing GitHub CLI for the current user...\n'
  fetch 'https://api.github.com/repos/cli/cli/releases/latest' "$work/gh-release.json"
  "${python_cmd[@]}" - "$work/gh-release.json" "$work" "$gh_arch" <<'PY'
import json, pathlib, re, sys
source, output, arch = sys.argv[1:]
release = json.loads(pathlib.Path(source).read_text())
tag = release['tag_name']
if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', tag):
    raise SystemExit('Unexpected GitHub CLI version')
version = tag[1:]
archive = f'gh_{version}_linux_{arch}.tar.gz'
checksums = f'gh_{version}_checksums.txt'
assets = {a['name']: a['browser_download_url'] for a in release['assets']}
for name, filename in ((archive, 'archive-url'), (checksums, 'checksums-url')):
    expected = f'https://github.com/cli/cli/releases/download/{tag}/{name}'
    if assets.get(name) != expected:
        raise SystemExit('Unexpected GitHub CLI download URL')
    pathlib.Path(output, filename).write_text(expected)
pathlib.Path(output, 'archive-name').write_text(archive)
PY
  fetch "$(cat "$work/archive-url")" "$work/gh.tar.gz"
  fetch "$(cat "$work/checksums-url")" "$work/checksums.txt"
  "${python_cmd[@]}" - "$work" "$data_dir/github/bin/gh" <<'PY'
import hashlib, pathlib, sys, tarfile, tempfile, os
work, target = map(pathlib.Path, sys.argv[1:])
name = (work / 'archive-name').read_text()
checksums = dict((line.split()[1].lstrip('*'), line.split()[0])
                 for line in (work / 'checksums.txt').read_text().splitlines() if len(line.split()) == 2)
if hashlib.sha256((work / 'gh.tar.gz').read_bytes()).hexdigest() != checksums.get(name):
    raise SystemExit('GitHub CLI checksum mismatch')
for path in (target, *target.parents):
    if path.is_symlink():
        raise SystemExit('Refusing a symlink in the GitHub CLI installation path')
target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
with tarfile.open(work / 'gh.tar.gz', 'r:gz') as archive:
    member = archive.getmember(name.removesuffix('.tar.gz') + '/bin/gh')
    if not member.isfile() or member.size > 200*1024*1024:
        raise SystemExit('Invalid GitHub CLI executable')
    content = archive.extractfile(member).read()
fd, temporary = tempfile.mkstemp(dir=target.parent)
try:
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)
    os.chmod(temporary, 0o700)
    os.replace(temporary, target)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
  gh_bin="$data_dir/github/bin/gh"
fi

printf '\n[2/4] Checking your GitHub sign-in...\n'
if ! "$gh_bin" auth status --hostname github.com >/dev/null 2>&1; then
  printf 'Follow the next instructions to sign in to your GitHub account in a browser.\n'
  printf 'Your personal configuration is private and requires your account.\n'
  if ! { true </dev/tty; } 2>/dev/null; then
    die 'No interactive terminal. Run in a terminal, or provide GH_TOKEN with access to your private repository.'
  fi
  "$gh_bin" auth login --hostname github.com --web --git-protocol https --skip-ssh-key </dev/tty
fi

printf '\n[3/4] Downloading your personal configuration...\n'
download_args=(release download)
if [[ -n "$release_tag" ]]; then download_args+=("$release_tag"); fi
"$gh_bin" "${download_args[@]}" --repo "$repository" --dir "$work" \
  --pattern 'super-agent-personal.sh' --pattern 'super-agent-personal.sh.sha256'
"${python_cmd[@]}" - "$work" <<'PY'
import hashlib, pathlib, sys
work = pathlib.Path(sys.argv[1])
checksums = (work / 'super-agent-personal.sh.sha256').read_text().split()
if len(checksums) != 2 or checksums[1] != 'super-agent-personal.sh':
    raise SystemExit('Invalid personal installer checksum file')
if hashlib.sha256((work / 'super-agent-personal.sh').read_bytes()).hexdigest() != checksums[0]:
    raise SystemExit('Personal installer checksum mismatch')
PY

printf '\n[4/4] Restoring your AI environment...\n'
bash "$work/super-agent-personal.sh" "${bundle_args[@]}"
