#!/bin/bash
# start-element-web.sh - Generate Element Web config and start Nginx

MATRIX_DOMAIN="${HICLAW_MATRIX_DOMAIN:-matrix-local.hiclaw.io:8080}"
ELEMENT_BRAND="${HICLAW_ELEMENT_BRAND:-Element}"

cat > /opt/element-web/config.json << EOF
{
    "default_server_config": {
        "m.homeserver": {
            "base_url": "http://${MATRIX_DOMAIN}"
        }
    },
    "brand": "${ELEMENT_BRAND}",
    "disable_guests": true,
    "disable_custom_urls": false
}
EOF

sed -i 's/worker_processes.*auto;/worker_processes 2;/' /etc/nginx/nginx.conf 2>/dev/null || \
sed -i 's/^worker_processes [0-9]*;/worker_processes 2;/' /etc/nginx/nginx.conf 2>/dev/null || \
grep -q '^worker_processes' /etc/nginx/nginx.conf || \
sed -i '1i worker_processes 2;' /etc/nginx/nginx.conf

cat > /etc/nginx/conf.d/element-web.conf << 'NGINX'
server {
    listen 8088;
    root /opt/element-web;
    index index.html;
    sub_filter '</head>' '<script>window.localStorage.setItem("mx_accepts_unsupported_browser","true");</script></head>';
    sub_filter_once on;
    sub_filter_types text/html;
    location / {
        try_files $uri $uri/ /index.html;
    }
    location ~* ^/(config.*\.json|index\.html|i18n|version)$ {
        add_header Cache-Control "no-cache";
    }
}
NGINX

OPENCLAW_TOKEN="${HICLAW_MANAGER_GATEWAY_KEY:-}"
cat > /etc/nginx/conf.d/openclaw-console.conf << NGINX
server {
    listen 18888;
    location / {
        proxy_pass http://127.0.0.1:18799;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Accept-Encoding "";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_hide_header Content-Security-Policy;
        sub_filter_types text/html;
        sub_filter_once on;
        sub_filter '</head>' '<script>(function(){var T="${OPENCLAW_TOKEN}";if(!T||location.hash.indexOf("token=")!==-1)return;location.replace(location.pathname+"#token="+T)})();</script></head>';
    }
}
NGINX

rm -f /etc/nginx/sites-enabled/default
exec nginx -g 'daemon off;'
