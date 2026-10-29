# TLS certificates (local only)

Put your own cert files here. **Do not commit** real `.key` / `.pem` / `.crt`.

Example names (match your Nginx config):

```
page/cert/
  your.example.com_bundle.crt
  your.example.com.key
```

Compose / Nginx examples in this repo do **not** mount production domains.
