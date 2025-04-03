# 使用多阶段构建
FROM ubuntu:20.04 AS builder

# 避免交互式提示
ENV DEBIAN_FRONTEND=noninteractive

# 设置工作目录
WORKDIR /app

# 更换apt源为中科大源
RUN sed -i 's/archive.ubuntu.com/mirrors.ustc.edu.cn/g' /etc/apt/sources.list && \
    sed -i 's/security.ubuntu.com/mirrors.ustc.edu.cn/g' /etc/apt/sources.list

# 安装Python、JDK和必要的构建工具
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3.9 \
    python3-pip \
    python3.9-dev \
    openjdk-8-jdk \
    python3.9-distutils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 创建python命令的软链接
RUN ln -s /usr/bin/python3.9 /usr/bin/python

# 更换pip源为中科大源
RUN python -m pip install --upgrade pip && \
    pip config set global.index-url https://pypi.mirrors.ustc.edu.cn/simple/

# 复制必要的文件
COPY requirements.txt .
COPY server.py .
COPY dlineage.py .
COPY jar/ ./jar/
COPY widget/ ./widget/
COPY test.sql .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 暴露端口
EXPOSE 8000

# 设置环境变量
ENV JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64
ENV PYTHONUNBUFFERED=1

# 创建非root用户
RUN useradd -m -s /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# 启动命令
CMD ["python", "server.py"]
