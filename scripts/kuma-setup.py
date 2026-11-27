#!/usr/bin/env python3
"""Penyiapan awal Uptime Kuma (idempoten). Kredensial hanya dari variabel lingkungan."""
import os
import sys
from uptime_kuma_api import UptimeKumaApi, MonitorType, NotificationType

URL = os.environ.get("KUMA_URL", "http://uptime-kuma:3001")
USER = os.environ.get("KUMA_USER")
PASSWORD = os.environ.get("KUMA_PASS")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK")
DNS_SERVER = os.environ.get("DNS_SERVER", "10.33.109.92")
NOTIF_NAME = "Discord KEL7"

if not (USER and PASSWORD and WEBHOOK):
    sys.exit("Isi dulu KUMA_USER, KUMA_PASS, dan DISCORD_WEBHOOK.")


def find_by_name(items, name):
    for item in items:
        if item.get("name") == name:
            return item
    return None


with UptimeKumaApi(URL) as api:
    if api.need_setup():
        api.setup(USER, PASSWORD)
        print("[OK] Akun admin dibuat")
    else:
        print("[SKIP] Akun admin sudah ada")
    api.login(USER, PASSWORD)
    print("[OK] Login berhasil")

    notif = find_by_name(api.get_notifications(), NOTIF_NAME)
    if notif is None:
        api.add_notification(
            name=NOTIF_NAME,
            type=NotificationType.DISCORD,
            discordWebhookUrl=WEBHOOK,
            isDefault=True,
            applyExisting=True,
        )
        notif = find_by_name(api.get_notifications(), NOTIF_NAME)
        print("[OK] Notifikasi dibuat")
    else:
        print("[SKIP] Notifikasi sudah ada")
    notif_id = notif["id"]

    monitors = api.get_monitors()

    if find_by_name(monitors, "Self-Healing Web") is None:
        api.add_monitor(
            type=MonitorType.HTTP,
            name="Self-Healing Web",
            url="http://haproxy:80",
            interval=20,
            maxretries=0,
            notificationIDList={notif_id: True},
        )
        print("[OK] Monitor Self-Healing Web dibuat")
    else:
        print("[SKIP] Monitor Self-Healing Web sudah ada")

    if find_by_name(monitors, "Bind9 DNS") is None:
        api.add_monitor(
            type=MonitorType.DNS,
            name="Bind9 DNS",
            hostname="app.kel7.lab",
            dns_resolve_server=DNS_SERVER,
            port=53,
            dns_resolve_type="A",
            interval=20,
            maxretries=0,
            notificationIDList={notif_id: True},
        )
        print("[OK] Monitor Bind9 DNS dibuat")
    else:
        print("[SKIP] Monitor Bind9 DNS sudah ada")

print("Selesai.")
