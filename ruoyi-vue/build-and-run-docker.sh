#!/bin/bash
set -e

echo "========================================================"
echo " 若依自动化测试平台 - Docker 容器化启动脚本"
echo "========================================================"

cd "$(dirname "$0")"

echo "[1/3] 检查 Docker 运行环境..."
docker --version

echo "[2/3] 构建并启动 Docker 容器群 (MySQL + Redis + RuoYi 后端)..."
docker-compose up --build -d

echo "[3/3] 容器启动完成！服务状态："
docker-compose ps

echo "========================================================"
echo " 后端接口地址: http://localhost:8080"
echo " 闸机接收通道: ws://localhost:8765/gate"
echo " MySQL 端口: 3306 (账号: root / 密码: password)"
echo " Redis 端口: 6379"
echo "========================================================"
