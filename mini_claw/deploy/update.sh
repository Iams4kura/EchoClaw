#!/bin/bash
# Mini Claw — 更新并重启
# 使用: cd mini_claw/deploy && bash update.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "拉取最新代码..."
cd "$REPO_ROOT"
git pull

echo "重新构建镜像..."
cd "$SCRIPT_DIR"
docker compose build

echo "重启服务..."
docker compose up -d

echo ""
echo "Mini Claw 已更新并重启!"
echo "  查看日志: docker compose logs -f"
