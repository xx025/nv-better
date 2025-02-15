FROM ubuntu:22.04

# 安装 Python3 及 venv
RUN apt update && apt install -y --no-install-recommends \
    python3 python3-pip python3-venv build-essential --fix-missing && \
    apt clean && rm -rf /var/lib/apt/lists/*

# 复制代码
COPY . /app
WORKDIR /app

# 创建 Python 虚拟环境并安装依赖
RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install -r req.txt

# 设置环境变量，确保后续 Python 命令使用虚拟环境
ENV PATH="/opt/venv/bin:$PATH"

# 设置默认启动模式
ENV MODE=server

# 运行时根据 MODE 变量决定执行哪个程序
CMD if [ "$MODE" = "server" ]; then python server.py; else python client.py; fi
