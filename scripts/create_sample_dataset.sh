#!/usr/bin/env bash
# 902 sample dataset: generate fixtures and sync data/sample_dataset/.
# Does not write backend/tests/fixtures/中文路径/. Does not use MySQL or parsers.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/scripts/create_sample_dataset.py" "$@"
