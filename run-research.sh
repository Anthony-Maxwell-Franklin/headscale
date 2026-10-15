#!/usr/bin/env bash
# Run an analysis command with the locked uv-managed research environment.
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
task_uv="$task_root/.tools/uv/uv"
if [[ ! -x "$task_uv" ]]; then
  task_uv="$(command -v uv || true)"
fi
if [[ -z "$task_uv" ]]; then
  echo 'uv is required; see Paper/README.md for environment setup.' >&2
  exit 1
fi
if [[ $# -eq 0 ]]; then
  echo 'Usage: bash run-research.sh python Paper/scripts/research_metrics.py' >&2
  exit 2
fi
cd -- "$task_root"
export UV_CACHE_DIR="$task_root/.cache/uv"
export UV_PYTHON_DOWNLOADS=never
export MPLCONFIGDIR="$task_root/.cache/matplotlib"
exec "$task_uv" run --project "$task_root/Paper" --locked --offline "$@"
