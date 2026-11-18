#!/bin/sh
# Usage: sudo ./bind/install.sh <SERVER_IP> <CLIENT_NET>   e.g. 203.0.113.10 203.0.113.0/24
set -e
[ $# -eq 2 ] || { echo "usage: $0 SERVER_IP CLIENT_NET"; exit 1; }
D=$(dirname "$0")
apt-get install -y bind9 bind9-utils
render() { sed "s|@SERVER_IP@|$1|g; s|@CLIENT_NET@|$2|g" "$3"; }
render "$1" "$2" "$D/db.kel7.lab" > /etc/bind/db.kel7.lab
render "$1" "$2" "$D/named.conf.options" > /etc/bind/named.conf.options
grep -q 'zone "kel7.lab"' /etc/bind/named.conf.local || cat "$D/named.conf.local" >> /etc/bind/named.conf.local
mkdir -p /etc/systemd/system/named.service.d
cp "$D/named-override.conf" /etc/systemd/system/named.service.d/override.conf
named-checkconf && named-checkzone kel7.lab /etc/bind/db.kel7.lab
systemctl daemon-reload && systemctl restart named
echo "Bind9 ready: dig @$1 app.kel7.lab +short"
