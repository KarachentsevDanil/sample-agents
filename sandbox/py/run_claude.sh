#!/usr/bin/env bash
# run_claude.sh — Run with Claude pipeline
# Usage: ./run_claude.sh [--model MODEL_ID] [task_ids...]
set -euo pipefail
cd "$(dirname "$0")"
exec ./run.sh --pipeline claude "$@"
