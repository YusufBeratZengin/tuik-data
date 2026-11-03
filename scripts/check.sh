#!/usr/bin/env bash
# Project gate: lint + type check + tests + coverage threshold.
# Usage: bash scripts/check.sh
# Exit code 0 only when every step passes.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PYTHONPATH="${PYTHONPATH:-src}"
COVERAGE_MIN="${COVERAGE_MIN:-85}"

# --- interpreter discovery --------------------------------------------------
# An explicitly exported PYTHON always wins. Otherwise prefer the project
# virtualenv (./.venv) and any active virtualenv over the system interpreter,
# so `bash scripts/check.sh` works without the caller remembering to activate.
if [ -z "${PYTHON:-}" ]; then
    if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "${VIRTUAL_ENV}/bin/python" ]; then
        PYTHON="${VIRTUAL_ENV}/bin/python"
    elif [ -x ".venv/bin/python" ]; then
        PYTHON=".venv/bin/python"
    elif [ -x ".venv/Scripts/python.exe" ]; then
        PYTHON=".venv/Scripts/python.exe"
    else
        PYTHON="python3"
    fi
fi

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "error: $PYTHON not found" >&2
    exit 1
fi

# --- tool discovery ---------------------------------------------------------
# Prefer `python -m <tool>` (works in venvs and on CI) then PATH.
find_tool() {
    local name="$1"
    if "$PYTHON" -m "$name" --version >/dev/null 2>&1; then
        echo "module"
        return 0
    fi
    if command -v "$name" >/dev/null 2>&1; then
        echo "path"
        return 0
    fi
    return 1
}

run_tool() {
    local name="$1"
    shift
    local mode
    mode="$(find_tool "$name" || true)"
    case "$mode" in
        module) "$PYTHON" -m "$name" "$@" ;;
        path) "$name" "$@" ;;
        *) echo "  ($name not installed: pip install -r requirements-dev.txt)" >&2; return 127 ;;
    esac
}

missing=0
echo "== python: $("$PYTHON" --version 2>&1)"
for tool in ruff mypy pytest; do
    if ! find_tool "$tool" >/dev/null; then
        echo "error: $tool is not available; install the dev requirements:" >&2
        echo "  $PYTHON -m pip install pytest pytest-cov ruff mypy" >&2
        missing=1
    fi
done
if ! "$PYTHON" -c "import pytest_cov" >/dev/null 2>&1; then
    echo "error: pytest-cov is not available; install the dev requirements" >&2
    missing=1
fi
if [ "$missing" -ne 0 ]; then
    exit 1
fi

# --- 1. lint ----------------------------------------------------------------
echo "== lint (ruff)"
run_tool ruff check src tests

echo "== format (ruff format --check)"
run_tool ruff format --check src tests

# --- 2. types ---------------------------------------------------------------
echo "== types (mypy --strict)"
run_tool mypy

# --- 3. tests + coverage ----------------------------------------------------
echo "== tests (pytest, network tests skipped by default)"
run_tool pytest -q \
    --cov=tuik_data \
    --cov-report=term-missing \
    --cov-fail-under="$COVERAGE_MIN"

echo
echo "OK: lint, types, tests and coverage (>= ${COVERAGE_MIN}%) all passed."
