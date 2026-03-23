#!/usr/bin/env bash
# run.sh — Run agent benchmark
# Usage: ./run.sh [--pipeline BACKEND] [--model MODEL_ID] [task_ids...]
# Example: ./run.sh --pipeline claude_cli t01 t02
# Example: ./run.sh --pipeline openai_agents --model gpt-4o t01
set -euo pipefail
cd "$(dirname "$0")"
exec uv run python main.py "$@"
