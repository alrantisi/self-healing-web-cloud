import csv, io, json, os, socket, threading, time, http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

BACKENDS = ["web1", "web2", "web3"]
DATA = "/data/state.json"
MSG = {
    "failing": ("warn", "mulai gagal health check"),
    "out": ("err", "dikeluarkan HAProxy dari pool"),
    "restarting": ("err", "berhenti, menunggu dipulihkan"),
    "recovering": ("warn", "berjalan kembali, menunggu 2 kali health check berhasil"),
    "healthy": ("ok", "masuk kembali ke pool dan sehat"),
}

S = {"events": [], "restarts": {}, "started": {}}
if os.path.exists(DATA):
    try:
        S.update(json.load(open(DATA)))
    except Exception:
        pass

lock = threading.Lock()
cur = {"backends": [], "service": "UNKNOWN", "rt": 0, "restarts_total": 0, "events": [], "updated": 0}
prev = {}
rts = []
last_service = None


class UnixConn(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost")

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect("/var/run/docker.sock")
        self.sock = s


def docker(path):
    c = UnixConn()
    try:
        c.request("GET", path)
        r = c.getresponse()
        body = r.read()
        return json.loads(body) if r.status == 200 else None
    finally:
        c.close()


def save():
    tmp = DATA + ".tmp"
    with open(tmp, "w") as f:
        json.dump(S, f)
    os.replace(tmp, DATA)


def event(kind, text):
    S["events"].insert(0, {"t": time.time(), "k": kind, "m": text})
    del S["events"][100:]
    save()


def haproxy_stats():
    raw = urlopen("http://haproxy:8404/stats;csv", timeout=2).read().decode()
    rows = csv.DictReader(io.StringIO(raw.lstrip("# ")))
    return {r["svname"]: r for r in rows if r["pxname"] == "servers"}


def derive(c, row):
    if not c or not c["State"].get("Running"):
        return "restarting"
    health = c["State"].get("Health", {}).get("Status", "none")
    if row is None:
        return "healthy" if health == "healthy" else "failing"
    st = row.get("status", "")
    if st.startswith("DOWN"):
        return "out" if health == "unhealthy" else "recovering"
    if health == "unhealthy" or "/" in st:
        return "failing"
    return "healthy" if st.startswith("UP") else "recovering"


def poll():
    global last_service
    last_probe = 0
    while True:
        try:
            try:
                hp = haproxy_stats()
            except Exception:
                hp = None
            backends = []
            for n in BACKENDS:
                try:
                    c = docker("/containers/%s/json" % n)
                except Exception:
                    c = None
                row = hp.get(n) if hp else None
                state = derive(c, row)
                if c:
                    started = c["State"].get("StartedAt", "")
                    old = S["started"].get(n)
                    if old and old != started:
                        S["restarts"][n] = S["restarts"].get(n, 0) + 1
                        event("ok", "Container %s dijalankan ulang (restart ke-%d)" % (n, S["restarts"][n]))
                    if old != started:
                        S["started"][n] = started
                        save()
                before = prev.get(n)
                if before and before != state and state in MSG:
                    k, t = MSG[state]
                    event(k, "Backend %s %s" % (n, t))
                prev[n] = state
                backends.append({
                    "name": n,
                    "state": state,
                    "restarts": S["restarts"].get(n, 0),
                    "req": int(row["stot"] or 0) if row else 0,
                })
            if time.time() - last_probe >= 5:
                last_probe = time.time()
                t0 = time.time()
                try:
                    urlopen("http://haproxy:80/", timeout=3).read()
                    ok = True
                    rts.append(int((time.time() - t0) * 1000))
                    del rts[:-20]
                except Exception:
                    ok = False
                svc = "ONLINE" if ok else "OFFLINE"
                if last_service and last_service != svc:
                    event("ok" if ok else "err", "Layanan utama kembali ONLINE" if ok else "Layanan utama OFFLINE, tidak merespons")
                last_service = svc
            with lock:
                cur.update({
                    "backends": backends,
                    "service": last_service or "UNKNOWN",
                    "rt": sum(rts) // len(rts) if rts else 0,
                    "restarts_total": sum(S["restarts"].values()),
                    "events": S["events"][:30],
                    "updated": time.time(),
                })
        except Exception as e:
            print("poll error:", e, flush=True)
        time.sleep(2)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/status":
            with lock:
                body = json.dumps(cur).encode()
            ct = "application/json"
        elif p in ("/dashboard", "/dashboard/"):
            body = open("/app/dashboard.html", "rb").read()
            ct = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


threading.Thread(target=poll, daemon=True).start()
ThreadingHTTPServer(("", 8000), H).serve_forever()
