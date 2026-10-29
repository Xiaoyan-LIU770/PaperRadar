# 在仓库根目录执行：source scripts/env.sh（bash / zsh）
if [ -n "${ZSH_VERSION:-}" ]; then
  PAPERRADAR_ENV_FILE="${(%):-%x}"
else
  PAPERRADAR_ENV_FILE="${BASH_SOURCE[0]}"
fi
export PAPERRADAR_ROOT="$(cd "$(dirname "$PAPERRADAR_ENV_FILE")/.." && pwd)"
export PATH="$PAPERRADAR_ROOT/.venv/bin:$PATH"
export PYTHONPATH="$PAPERRADAR_ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR="${PAPERRADAR_TMPDIR:-/tmp}"
unset PAPERRADAR_ENV_FILE
