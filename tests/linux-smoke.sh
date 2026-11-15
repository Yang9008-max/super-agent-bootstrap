#!/usr/bin/env bash
# Network installation smoke test for a disposable Linux CI runner.
set -euo pipefail
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
python3 - "$work/profile.json" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    'version': 1, 'config': {'model_reasoning_effort': 'medium'},
    'skills': {}, 'instructions': None, 'required_env': [], 'warnings': []
}))
PY
python3 tools/portable_agent.py bundle "$work/profile.json" --output "$work/smoke.sh"
bash "$work/smoke.sh" --check-only
bash "$work/smoke.sh"
"$HOME/.local/bin/super-agent" doctor
"$HOME/.local/bin/codex" --version
