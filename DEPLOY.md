# 部署说明

面向自己的机器或测试环境。不含生产域名、证书和前端包。

## 1. 配置

```bash
cp .env.example .env
```

至少改：

| 变量 | 说明 |
|------|------|
| `POSTGRES_PASSWORD` / `DATABASE_URL` | 数据库密码，两处保持一致 |
| `ADMIN_PASSWORD` | 管理端密码，不要用 `change-me` |
| `DEEPSEEK_API_KEY` 等 | 所用模型的 Key |
| `REDIS_HOST` | Compose 内填 `redis` |

登录 / 支付相关（`GOOGLE_*`、`APPLE_*`、`GOOGLE_PLAY_*`）按需填写；证书文件放 `server/certs/`，不要进 git。

## 2. 构建并启动

```bash
docker build -f Dockerfile.base -t trandsai-base:v1 .
docker compose up -d
```

`Dockerfile` / `Dockerfile.base` 只装依赖和启动 uvicorn，不内嵌密钥。

## 3. 健康检查

```bash
docker compose ps
curl -sS http://127.0.0.1:8000/docs
docker compose exec postgres pg_isready -U trandsai
docker compose exec redis redis-cli ping
```

## 4. 静态页 `page/` + 可选 Nginx

仓库已带示例静态目录：`page/frontend`、`page/admin`、`page/kaya`、`page/agreement`。  
`page/cert/` 只放自备证书，**不要**提交真实 `.key` / `.pem`。

```bash
# 起 API + DB + Redis + Nginx（托管 page）
docker compose --profile with-nginx up -d
```

或用自己的 Nginx / Caddy：改 `nginx.conf.example` 的 `server_name`，证书放到 `page/cert/`，反代到 `127.0.0.1:8000`。

## 5. 更新

```bash
git pull
docker build -f Dockerfile.base -t trandsai-base:v1 .   # 仅 requirements 变化时
docker compose up -d
docker compose logs -f backend
```

## 6. 不要做的事

- 不要把 `.env`、`.p8`、服务账号 JSON 拷进镜像或公开仓库
- 不要在 `docker-compose.yml` 里写死 Redis/数据库密码
- 不要把内网 IP、现网域名写进配置示例
