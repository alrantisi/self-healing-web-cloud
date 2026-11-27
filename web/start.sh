#!/bin/sh
set -e
N="$1"
cp -r /web-src/site/. /usr/share/nginx/html/
sed -i "s/__SERVER__/$N/g" /usr/share/nginx/html/index.html
echo "$N" > /usr/share/nginx/html/whoami.txt
echo ok > /usr/share/nginx/html/health
exec nginx -g 'daemon off;'
