#!/bin/sh
set -eu

exec uvicorn civitas_api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
