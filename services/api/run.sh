#!/bin/sh
set -eu

APP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONPATH="${APP_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

exec python -m uvicorn civitas_api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
