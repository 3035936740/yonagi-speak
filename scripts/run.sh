#!/bin/sh
set -eu
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=${QQ_BOT_PYTHON:-}
if [ -z "$python_bin" ]; then
  for candidate in "$project_root/.venv/bin/python" "$project_root/qq-mcp/.venv/bin/python"; do
    if [ -x "$candidate" ]; then python_bin=$candidate; break; fi
  done
fi
if [ -z "$python_bin" ]; then
  printf '%s\n' 'Run sh ./Install-Environment.sh first, or set QQ_BOT_PYTHON.' >&2
  exit 1
fi
exec "$python_bin" -X utf8 "$project_root/scripts/manage.py" "$@"
