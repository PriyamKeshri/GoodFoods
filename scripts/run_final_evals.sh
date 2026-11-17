#!/bin/sh
# Runs each eval configuration separately (so an interruption costs one configuration, not the whole run),
# restarts the local Ollama server if it has stopped, then rebuilds the report and the README sections.
cd "$(dirname "$0")/.." || exit 1
LOG=evals/results/run.log
: > "$LOG"
for c in ollama-llama3.1-8b:on ollama-llama3.1-8b:off ollama-qwen3-8b:on ollama-qwen3-8b:off; do
  for attempt in 1 2 3; do
    curl -s -m 3 localhost:11434/api/version >/dev/null || { (nohup ollama serve >/dev/null 2>&1 &); sleep 8; }
    if caffeinate -dims .venv/bin/python -m evals.run_evals --configs "$c" --repeats "${REPEATS:-2}" >> "$LOG" 2>&1; then break; fi
    echo "RETRY $c (attempt $attempt failed)" >> "$LOG"
  done
done
.venv/bin/python -m evals.run_evals --report-only >> "$LOG" 2>&1 && .venv/bin/python scripts/update_readme.py >> "$LOG" 2>&1
echo "ALL DONE" >> "$LOG"
