#!/usr/bin/env bash
# run_langchain.sh — Run with LangChain pipeline
# Usage: ./run_langchain.sh [--model MODEL_ID] [task_ids...]
set -euo pipefail
cd "$(dirname "$0")"
exec ./run.sh --pipeline langchain "$@"
