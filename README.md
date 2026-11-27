# Self-Healing Web Cloud

A container-based prototype that combines load balancing, health checks, and automatic recovery to keep a web service available when a backend fails. Built as a group project (KEL7) and tested on an Ubuntu Server VM. AWS services (ELB, EC2, CloudWatch) were used only as conceptual references, not deployed.

## How it works

- **HAProxy** load-balances requests (round-robin) across three Nginx backends and checks `/health` on each every 2 seconds. A backend that fails 3 checks in a row is removed from the pool, and it returns after 2 successful checks.
- **Docker restart policy** restarts a container whose main process stops.
- **Autoheal** restarts containers that Docker reports as unhealthy.
- **Uptime Kuma** monitors the service and sends failure and recovery alerts to Discord. It only monitors and notifies; it does not restart anything.
- **Monitor dashboard** (`monitor/`) is a small Python service that reads container state from the Docker socket and shows backend health, restart counts, and recovery events live.

## Structure

```
docker-compose.yml     HAProxy, 3 backends, autoheal, Uptime Kuma, monitor
haproxy/haproxy.cfg    Load balancer and health check configuration
monitor/app.py         Status API and event tracking
monitor/dashboard.html Live dashboard
web/                   Backend page, copied into nginx at start by start.sh
scripts/kuma-setup.py  Uptime Kuma setup script
.env.example           Template for the dashboard test PIN
```

## Run

```bash
docker compose up -d
docker compose ps
for i in $(seq 1 6); do curl -s localhost:8080/whoami.txt; done
```

Each backend returns a page with its own identity, so you can see the round-robin distribution. The dashboard is served at `/dashboard` through HAProxy.

## Failure tests

```bash
docker exec web2 nginx -s stop                    # main process stops: restart policy starts it again
docker exec web1 rm /usr/share/nginx/html/health  # health check fails: autoheal restarts the container
# note: docker kill is treated as a manual stop, so it does not trigger the restart policy
```

During a failure, the other two backends keep serving requests, and the recovered backend rejoins the pool after two successful health checks.

## Resilience test panel

The dashboard has a panel that breaks a backend on purpose, so the recovery can be watched live. It is protected by a PIN stored in `.env` (git-ignored). Without a PIN the buttons stay disabled.

```bash
read -s -p "PIN: " P; printf 'CONTROL_PIN=%s\n' "$P" > .env; chmod 600 .env; unset P
docker compose up -d monitor
```

Use letters and digits only. `.env.example` shows the expected format.

| Action | What it does | Recovered by |
|---|---|---|
| `crash` | `nginx -s stop` inside the backend | restart policy |
| `breakhealth` | removes `/health` from the backend | autoheal |

Safeguards in `monitor/app.py`: only `web1`, `web2`, `web3` are accepted as targets, only the two fixed actions above exist (no free commands), the PIN is compared in constant time, attempts are limited to one per 2 seconds, and successful actions to one per 10 seconds.

Limits: the PIN is a simple gate, not user authentication. The `monitor` container talks to the Docker API through `docker.sock`. Mounting it with `:ro` only affects the socket file, not the API calls, so access to this endpoint is effectively access to Docker on the host. Do not expose it without a PIN.

## Uptime Kuma setup

`scripts/kuma-setup.py` configures a fresh Uptime Kuma (tested with 1.23.17): admin account, a Discord notification, an HTTP monitor for the load balancer, and a DNS monitor for Bind9. It is idempotent, so running it again skips what already exists. Credentials come only from environment variables and are never stored in the repo.

```bash
export KUMA_USER=admin
read -s -p "Kuma password: " KUMA_PASS; export KUMA_PASS; echo
read -s -p "Discord webhook: " DISCORD_WEBHOOK; export DISCORD_WEBHOOK; echo
docker run --rm --network self-healing-web-cloud_default \
  -e KUMA_USER -e KUMA_PASS -e DISCORD_WEBHOOK \
  -v "$PWD/scripts:/scripts:ro" python:3-alpine \
  sh -c "pip install -q uptime-kuma-api2 && python /scripts/kuma-setup.py"
unset KUMA_PASS DISCORD_WEBHOOK
```

The network name is `<project folder>_default`. If Kuma already has an account, `KUMA_USER` and `KUMA_PASS` must match it. The DNS monitor asks the resolver in `DNS_SERVER` (default `10.33.109.92`), so change it for another server.

## Notes

- Bind9 (internal DNS zone `kel7.lab` for hostname-based routing) runs on the host as a systemd service with `Restart=on-failure`. Config templates and an installer script are in `bind/`. Squid runs the same way but is not included.
- Out of scope: HAProxy redundancy (it is a single point of failure), auto-scaling, multi-host orchestration, and HTTPS.

## Deploy on another server

```bash
git clone git@github.com:alrantisi/self-healing-web-cloud.git && cd self-healing-web-cloud
docker compose up -d
sudo ./bind/install.sh <SERVER_IP> <CLIENT_NET>   # optional: internal DNS for kel7.lab
```

The base compose file binds HAProxy and Uptime Kuma to `127.0.0.1` only. To expose HAProxy on a specific IP, add a git-ignored `docker-compose.override.yml` with an extra port binding. Uptime Kuma data is not included: create the admin account, monitors, and notifications again after the first start.
