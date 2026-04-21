# Mini Claw — Codeclaw cloud automation platform
# Build context: Codeclaw 根目录 (包含 mini_claude/ 和 mini_claw/)
#
# docker build -f mini_claw/deploy/Dockerfile -t mini_claw .

FROM python:3.11-slim

WORKDIR /app

# 系统依赖：git（mini_claude 工具需要）
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# 先装 mini_claude（执行引擎，依赖较重，利用缓存）
COPY mini_claude/pyproject.toml mini_claude/setup.cfg* mini_claude/setup.py* mini_claude/
COPY mini_claude/src/ mini_claude/src/
RUN pip install --no-cache-dir ./mini_claude/

# 再装 mini_claw（云平台）
COPY mini_claw/pyproject.toml mini_claw/
COPY mini_claw/script/ mini_claw/script/
RUN pip install --no-cache-dir ./mini_claw/

# 复制配置模板和预置分身
COPY mini_claw/config/ mini_claw/config/

# 创建运行时目录
RUN mkdir -p mini_claw/data/memory mini_claw/logs /workspace

# 工作目录切到 mini_claw，让相对路径 config/settings.yaml 生效
WORKDIR /app/mini_claw

EXPOSE 8080

# 健康检查（不依赖 curl）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# 默认环境变量
ENV MINI_CLAW_WORKING_DIR=/workspace \
    MINI_CLAW_PORT=8080

CMD ["mini_claw"]
