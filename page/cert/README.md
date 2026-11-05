# TLS certificates (local only)

Put your own cert files here. **Do not commit** real `.key` / `.pem` / `.crt`.

Nginx 期望文件名（已挂载到容器 `/etc/nginx/certs/`）：

```
page/cert/
  fullchain.pem   # 证书链（含域名 SAN：liaosao.xyz / .live / .life 及 www）
  privkey.pem     # 私钥
```

可用 Let's Encrypt（certbot）一次签多域名，再拷贝到上述路径。  
没有这两个文件时，带 443 的 Nginx **无法启动**。
