# TrandsAI（开源示例）

后端 FastAPI + PostgreSQL + Redis，并附带 `page/` 静态前端示例目录。不含对接文档 `docs/`、真实密钥和 TLS 证书。

## 架构

```
浏览器 ──▶ Nginx(可选, page/) ──▶ FastAPI (server/) ──▶ PostgreSQL
                                      │
                                      └──▶ Redis
```

| 服务 | 默认端口 | 说明 |
|------|----------|------|
| Backend | 8000 | FastAPI |
| Nginx（可选） | 80 | 托管 `page/` + 反代 API |
| PostgreSQL | 5432 | 业务库 |
| Redis | 6379 | 缓存 / 队列 |

## 环境

- Docker 20.10+
- Docker Compose 2.0+
- 或本地 Python 3.11

## 快速开始（Docker）

```bash
cp .env.example .env
# 编辑 .env：数据库密码、ADMIN_PASSWORD、各 API Key

docker build -f Dockerfile.base -t trandsai-base:v1 .
docker compose up -d
docker compose ps
docker compose logs -f backend
```

API：http://localhost:8000  
OpenAPI：http://localhost:8000/docs

需要顺带起静态站时：

```bash
docker compose --profile with-nginx up -d
# 主站示例: http://localhost/
# 管理后台: http://localhost/admin/
```

首次启动后，用 `.env` 里的 `ADMIN_PASSWORD` 登录管理接口（不要使用示例值上线）。

## 本地开发（不用 Compose 跑 API）

```bash
cp .env.example .env
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
cd server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

`DATABASE_URL`、`REDIS_HOST` 指向本机或 Compose 里的 postgres/redis。

## 常用命令

```bash
docker compose up -d
docker compose down
docker compose restart backend
docker compose logs -f backend

# 依赖变更后重建基础镜像
docker build -f Dockerfile.base -t trandsai-base:v1 .
docker compose up -d backend
```

## 目录

```
trandsai/
├── .env.example          # 环境变量模板（复制为 .env）
├── docker-compose.yml    # API + Postgres + Redis（可选 Nginx）
├── Dockerfile.base       # Python 依赖镜像
├── Dockerfile            # 应用镜像（基于 base）
├── nginx.conf.example    # 静态站 + API 反代示例
├── requirements.txt
├── server/               # FastAPI 代码
└── page/                 # 静态前端示例（见 page/README.md）
    ├── frontend/         # 主站
    ├── kaya/             # 马甲示例
    ├── admin/            # 管理后台
    ├── agreement/        # 协议页
    ├── file/             # 杂项静态
    └── cert/             # 自备证书（仓库内无真实密钥）
```

API / 支付密钥放到本地 `server/certs/`（已 gitignore），路径写在 `.env`。  
站点 TLS 证书放到 `page/cert/`，不要提交。

## 安全

- 不要提交 `.env`、`.p8`、`.pem`、`.key`、服务账号 JSON
- 生产务必改 `POSTGRES_PASSWORD`、`ADMIN_PASSWORD`
- 生产 Redis 自行加密码，并写进 `REDIS_PASSWORD`
- HTTPS、域名自行配置；可参考 `nginx.conf.example`

## 备份

```bash
docker compose exec postgres pg_dump -U trandsai trandsai > backup.sql
docker compose exec -T postgres psql -U trandsai trandsai < backup.sql
```
