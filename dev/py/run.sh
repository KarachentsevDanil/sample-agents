#!/usr/bin/env bash
# run.sh — Run dev agent pipeline benchmark (bitgn/pac1-dev)
# Usage: ./run.sh [--pipeline BACKEND] [--model MODEL_ID] [task_ids...]
# Example: ./run.sh --pipeline claude t01 t02
# Example: ./run.sh --pipeline openai --model gpt-4o t01
set -euo pipefail
cd "$(dirname "$0")"
exec uv run python main_pipeline.py "$@"
