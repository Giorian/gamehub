#!/bin/bash
# GameHub 本地自动更新脚本
# 用法: ./run_local.sh [输出目录]
# 配合 crontab 或 launchd 使用，定时刷新本地数据

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 激活虚拟环境
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# 输出目录（默认 dist/data）
OUTPUT_DIR="${1:-dist/data}"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始抓取资讯..."
python export_data.py --output "$OUTPUT_DIR"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 完成"
