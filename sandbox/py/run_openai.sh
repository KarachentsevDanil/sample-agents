#!/usr/bin/env bash
# run_openai.sh — Run with OpenAI Agents pipeline
# Usage: ./run_openai.sh [--model MODEL_ID] [task_ids...]
set -euo pipefail
cd "$(dirname "$0")"
exec ./run.sh --pipeline openai "$@"
