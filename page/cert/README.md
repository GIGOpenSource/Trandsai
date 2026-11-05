# TLS certificates (local only)

**Do not commit** real `.key` / `.crt` / `.pem`.

当前 Nginx 使用的文件名：

```
page/cert/
  jnaliaosao_xyz.crt
  jnaliaosao_xyz.key
  jnaliaosao_live.crt
  jnaliaosao_live.key
  jnaliaosao_life.crt
  jnaliaosao_life.key
```

挂载到容器：`/etc/nginx/certs/`。
