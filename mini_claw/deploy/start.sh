#!/bin/bash
# Mini Claw — 一键启动
# 使用: cd mini_claw/deploy && bash start.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 检查 .env
if [ ! -f .env ]; then
    echo "未找到 .env 文件，从模板创建..."
    cp .env.example .env
    echo "请编辑 .env 填入 API keys 后重新运行此脚本。"
    exit 1
fi

echo "构建镜像..."
docker compose build

echo "启动服务..."
docker compose up -d

echo ""
echo "Mini Claw 已启动!"
echo "  健康检查: curl http://localhost:${MINI_CLAW_PORT:-8080}/health"
echo "  查看日志: docker compose logs -f"
echo "  停止服务: docker compose down"
