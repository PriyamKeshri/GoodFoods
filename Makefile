PY := .venv/bin/python

.PHONY: help setup models seed run cli mcp mcp-http test eval reset

help:  ## list commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-9s %s\n", $$1, $$2}'

setup:  ## create the virtualenv and install dependencies (Python 3.11+)
	python3 -m venv .venv && $(PY) -m pip install -q --upgrade pip && $(PY) -m pip install -q -r requirements.txt

models:  ## pull the local 8B models and create 12k-context variants
	ollama pull llama3.1:8b && ollama pull qwen3:8b && $(PY) -m goodfoods.agent.ollama_setup

seed:  ## (re)build the demo database
	$(PY) -m goodfoods.core.seed --force

run:  ## start the web app on http://localhost:8501
	.venv/bin/streamlit run streamlit_app.py

cli:  ## chat in the terminal with the agent trace
	$(PY) -m goodfoods.cli --trace

mcp:  ## run the MCP server on stdio (for MCP hosts)
	$(PY) -m goodfoods.mcp_server

mcp-http:  ## run the MCP server over streamable HTTP on :8765
	$(PY) -m goodfoods.mcp_server --http --port 8765

test:  ## unit, protocol and agent-loop tests
	$(PY) -m pytest -q

eval:  ## scenario evals: Llama and Qwen, guardrails on/off, 2 runs each (~45 min locally)
	./scripts/run_final_evals.sh

reset: seed  ## fresh demo data and empty telemetry
	rm -f data/telemetry.db data/telemetry.db-wal data/telemetry.db-shm
