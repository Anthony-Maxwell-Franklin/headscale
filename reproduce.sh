#!/usr/bin/env bash
# Linux entry point. Offline by default; no credentials are embedded.
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
command -v git >/dev/null || { echo 'git is required' >&2; exit 1; }
exec bash "$task_root/run-research.sh" python "$task_root/Paper/scripts/analyze.py" "$@"
