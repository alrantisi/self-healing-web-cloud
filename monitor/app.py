import csv, hmac, io, json, os, socket, threading, time, http.client
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


PIN = os.environ.get("CONTROL_PIN", "")
ACTIONS = {
    "crash": ["nginx", "-s", "stop"],
    "breakhealth": ["rm", "-f", "/usr/share/nginx/html/health"],
}
LABEL = {"crash": "crash (nginx stop)", "breakhealth": "rusak health check"}
ctl_lock = threading.Lock()
last_try = 0.0
last_ctl = 0.0


def docker_post(path, payload):
    c = UnixConn()
    try:
        data = json.dumps(payload).encode()
        c.request("POST", path, body=data, headers={"Content-Type": "application/json", "Content-Length": str(len(data))})
        r = c.getresponse()
        body = r.read()
        try:
            return r.status, (json.loads(body) if body else None)
        except ValueError:
            return r.status, None
    finally:
        c.close()


def docker_exec(name, cmd):
    st, res = docker_post("/containers/%s/exec" % name, {"Cmd": cmd, "AttachStdout": False, "AttachStderr": False})
    if st not in (200, 201) or not res or "Id" not in res:
        return False
    st, _ = docker_post("/exec/%s/start" % res["Id"], {"Detach": True})
    return st in (200, 201, 204)


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

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        global last_try, last_ctl
        if self.path.split("?")[0] != "/api/control":
            self.send_error(404)
            return
        try:
            n = max(0, min(int(self.headers.get("Content-Length", 0)), 1024))
            req = json.loads(self.rfile.read(n) or b"{}")
            pin = str(req.get("pin", ""))
            target = str(req.get("target"))
            action = str(req.get("action"))
        except Exception:
            return self.reply(400, {"ok": False, "msg": "permintaan tidak valid"})
        with ctl_lock:
            now = time.time()
            if now - last_try < 2:
                return self.reply(429, {"ok": False, "msg": "terlalu cepat, coba lagi sebentar"})
            last_try = now
        if not PIN:
            return self.reply(503, {"ok": False, "msg": "kontrol nonaktif: PIN belum diatur"})
        if not hmac.compare_digest(pin.encode(), PIN.encode()):
            return self.reply(403, {"ok": False, "msg": "PIN salah"})
        if target not in BACKENDS or action not in ACTIONS:
            return self.reply(400, {"ok": False, "msg": "target atau aksi tidak dikenal"})
        with ctl_lock:
            if now - last_ctl < 10:
                return self.reply(429, {"ok": False, "msg": "tunggu 10 detik antar aksi"})
            last_ctl = now
        ok = docker_exec(target, ACTIONS[action])
        with lock:
            event("warn" if ok else "err", "Uji manual: %s pada %s %s" % (LABEL[action], target, "dijalankan" if ok else "GAGAL"))
        return self.reply(200 if ok else 502, {"ok": ok, "msg": "perintah dikirim" if ok else "gagal menjalankan perintah"})

    def log_message(self, *a):
        pass


threading.Thread(target=poll, daemon=True).start()
ThreadingHTTPServer(("", 8000), H).serve_forever()
