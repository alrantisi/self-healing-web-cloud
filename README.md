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
```

## Run

```bash
docker compose up -d
docker compose ps
for i in $(seq 1 6); do curl -s localhost:8080 | grep -o "Backend Server [0-9]"; done
```

Each backend returns a page with its own identity, so you can see the round-robin distribution. The dashboard is served at `/dashboard` through HAProxy.

## Failure tests

```bash
docker kill web2                  # crashed container: restart policy brings it back
docker exec web2 nginx -s stop    # stopped app: health check fails, autoheal restarts it
```

During a failure, the other two backends keep serving requests, and the recovered backend rejoins the pool after two successful health checks.

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
