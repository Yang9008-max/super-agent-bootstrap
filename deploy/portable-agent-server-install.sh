#!/usr/bin/env bash
# Render the HTTPS base URL before publishing this file on a private download route.
set -euo pipefail
umask 077
bundle_base='__BUNDLE_BASE_URL__'
bundle_args=()
die() { printf 'super-agent: %s\n' "$*" >&2; exit 1; }
for argument in "$@"; do
  case "$argument" in
    --skip-codex|--check-only) bundle_args+=("$argument") ;;
    --help|-h)
      printf 'Usage: install.sh [--skip-codex] [--check-only]\n'
      exit 0 ;;
    *) die "Unknown argument: $argument" ;;
  esac
done
[[ "$bundle_base" == https://* ]] || die 'HTTPS download URL has not been configured.'
[[ "$(uname -s)" == Linux ]] || die 'This installer supports Linux.'
command -v sha256sum >/dev/null 2>&1 || die 'sha256sum is required (part of Linux coreutils).'
work="$(mktemp -d "${TMPDIR:-/tmp}/super-agent-download.XXXXXXXX")"
trap 'rm -rf -- "$work"' EXIT
fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl --proto '=https' --proto-redir '=https' --fail --location --silent --show-error \
      --connect-timeout 20 --max-time 300 "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget --https-only --quiet --timeout=30 -O "$2" "$1"
  else
    die 'Please install curl or wget first.'
  fi
}
printf '\n正在下载安装包，无需登录 GitHub……\n'
fetch "$bundle_base/super-agent-personal.sh" "$work/super-agent-personal.sh"
fetch "$bundle_base/super-agent-personal.sh.sha256" "$work/super-agent-personal.sh.sha256"
checksum_line="$(cat "$work/super-agent-personal.sh.sha256")"
checksum_line="${checksum_line%$'\r'}"
[[ "$checksum_line" =~ ^[[:xdigit:]]{64}[[:space:]]+super-agent-personal\.sh$ ]] || die 'Invalid installer checksum file.'
printf '%s\n' "$checksum_line" > "$work/checksum.clean"
(cd "$work" && sha256sum --check --status checksum.clean) || die 'Personal installer checksum mismatch.'
printf '校验通过，开始安装和恢复配置。\n'
bash "$work/super-agent-personal.sh" "${bundle_args[@]}"
