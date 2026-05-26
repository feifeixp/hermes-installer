#!/usr/bin/env bash
# Wrapper to launch synced webui from worktree root with the existing build venv.
# Used by .claude/launch.json "webui-synced" config for post-sync smoke verification.
set -euo pipefail

SYNC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="/Users/ff/hermes-installer/.build_venv/bin/python"

export HERMES_HOME="${SYNC_ROOT}/.smoke-hermes-home"
export HERMES_WEBUI_STATE_DIR="${HERMES_HOME}/webui"
export HERMES_WEBUI_HOST="${HERMES_WEBUI_HOST:-127.0.0.1}"
export HERMES_WEBUI_PORT="${HERMES_WEBUI_PORT:-8787}"
# Skip the "needs Hermes agent installed" probes for a pure UI smoke test.
export HERMES_WEBUI_DISABLE_AGENT_CHECKS="${HERMES_WEBUI_DISABLE_AGENT_CHECKS:-1}"
# Match production: main.py (installer entry) sets HERMES_NEOWOW_ONLY=1 so the
# WebUI shows only the Neowow Coding Plan provider, not the full upstream
# openai/anthropic/openrouter list. We bypass main.py here, so set it ourselves.
export HERMES_NEOWOW_ONLY="${HERMES_NEOWOW_ONLY:-1}"

mkdir -p "${HERMES_HOME}" "${HERMES_WEBUI_STATE_DIR}"

cd "${SYNC_ROOT}/webui"
exec "${VENV_PY}" server.py
