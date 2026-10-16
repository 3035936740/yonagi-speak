#!/bin/sh
set -eu
project_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "${1:-}" = '--dry-run' ]; then
  printf '%s\n' 'Plan: local uv; managed Python 3.12; .venv; requirements; optional qq-mcp; initialize missing settings.'
  exit 0
fi
uv_dir="$project_root/tools/uv"
uv="$uv_dir/uv"
if [ ! -x "$uv" ]; then
  mkdir -p "$uv_dir"
  curl --proto '=https' --tlsv1.2 -fL --connect-timeout 20 --max-time 300 https://astral.sh/uv/install.sh -o "$uv_dir/uv-install.sh"
  UV_INSTALL_DIR="$uv_dir" UV_NO_MODIFY_PATH=1 sh "$uv_dir/uv-install.sh"
fi
"$uv" python install 3.12
if [ ! -d "$project_root/.venv" ]; then "$uv" venv --python 3.12 --seed "$project_root/.venv"; fi
python_bin="$project_root/.venv/bin/python"
"$uv" pip install --python "$python_bin" -r "$project_root/requirements.txt"
if [ -f "$project_root/qq-mcp/pyproject.toml" ]; then "$uv" pip install --python "$python_bin" -e "$project_root/qq-mcp"; fi
"$uv" pip check --python "$python_bin"
"$python_bin" -X utf8 "$project_root/scripts/manage.py" init
printf '%s\n' 'Environment ready. Configure group-bot/settings.json and NapCat connection before starting.'
