#!/usr/bin/env bash
# run_langgraph.sh — Run with LangGraph pipeline
# Usage: ./run_langgraph.sh [--model MODEL_ID] [task_ids...]
set -euo pipefail
cd "$(dirname "$0")"
exec ./run.sh --pipeline langgraph "$@"
