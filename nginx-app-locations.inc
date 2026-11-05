# 由 server {} include；admin 用 root（勿用 alias+try_files，会 500）

location /api/ {
    proxy_pass http://backend_pool/api/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_connect_timeout 180s;
    proxy_send_timeout 180s;
    proxy_read_timeout 180s;
}

location /ws/ {
    proxy_pass http://backend_pool/ws/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_connect_timeout 180s;
    proxy_send_timeout 180s;
    proxy_read_timeout 180s;
}

location /admin/ {
    root /usr/share/nginx/html;
    try_files $uri $uri/ /admin/index.html;
    index index.html;
}

location = /admin {
    return 301 /admin/;
}

location /agreement/ {
    root /usr/share/nginx/html;
    try_files $uri $uri/ =404;
}

location / {
    root /usr/share/nginx/html/frontend;
    try_files $uri $uri/ /index.html;
    index index.html;
}
