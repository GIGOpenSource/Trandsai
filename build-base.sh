#!/bin/bash

# 构建基础镜像
echo "构建后端基础镜像..."
docker build -f Dockerfile.base -t trandsai-backend-base:latest .

echo "基础镜像构建完成"
echo ""
echo "使用方式："
echo "  启动服务: docker-compose up -d"
echo "  查看日志: docker-compose logs -f"
echo "  停止服务: docker-compose down"