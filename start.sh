#!/bin/bash

# 启动服务
echo "启动 Trandsai 服务..."
docker-compose up -d

echo ""
echo "服务状态："
docker-compose ps

echo ""
echo "常用命令："
echo "  查看日志: docker-compose logs -f"
echo "  停止服务: docker-compose down"
echo "  重启服务: docker-compose restart"
echo "  进入后端: docker exec -it trandsai-backend bash"