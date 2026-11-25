# Nexora backend
# - Citizens report incidents (public, no login) and track them with a tracking ID
# - IoT sensors send alerts through MQTT (Mosquitto)
# - Department staff and the Command Center log in and handle incidents
import hashlib, hmac, json, secrets, sqlite3, threading, time, urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

import paho.mqtt.client as mqtt
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

DB = Path(__file__).resolve().parent / "nexora.db"
DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard"
BROKER = "127.0.0.1"
TOPIC = "nexora/alerts/#"

# Google Form: paste your Apps Script web app link here, ending with ?key=YOUR_SECRET
# Leave it as "" to switch the Google Form off.
GFORM_URL = ""
_link_file = Path(__file__).resolve().parent / "gform_url.txt"   # the secret link is kept in this file, not on GitHub
if _link_file.exists(): GFORM_URL = _link_file.read_text().strip()
GFORM_EVERY = 10  # seconds between checks

# Which department(s) handle each incident type
ROUTING = {
    "fire": ["Fire"],
    "smoke": ["Fire"],
    "medical": ["Hospital"],
    "accident": ["Police", "Hospital"],
    "traffic": ["Traffic"],
}
DEFAULT_SEVERITY = {"fire": "high", "smoke": "medium", "medical": "high",
                    "accident": "high", "traffic": "low"}

# Anti-spam: each device (IP address) may send at most 3 reports per 5 minutes
REPORT_LIMIT, REPORT_WINDOW = 3, 300
recent_reports = {}
gform_waiting = {}  # Google Form rows still waiting for their tracking ID

state = {"mqtt_connected": False, "last_sensor_alert": None,
         "gform_last_sync": None, "gform_error": None}


# ---------- database helpers ----------
def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def new_tracking_id(con, wanted=None):
    # Use the ID made by the Google Form (if valid and unused), otherwise make a new one
    if wanted and len(wanted) == 9 and wanted.startswith("NX-") and wanted[3:].isalnum():
        if not con.execute("SELECT 1 FROM incidents WHERE tracking_id=?", (wanted,)).fetchone():
            return wanted
    while True:
        tid = "NX-" + "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        if not con.execute("SELECT 1 FROM incidents WHERE tracking_id=?", (tid,)).fetchone():
            return tid


def create_incident(kind, location, description, severity, source, name=None, phone=None, via=None, email=None,
                    tracking_id=None):
    departments = ROUTING[kind]
    con = db()
    tid = new_tracking_id(con, tracking_id)
    cur = con.execute(
        "INSERT INTO incidents (tracking_id, type, location, description, severity, department, "
        "source, reporter_name, reporter_phone, reporter_email, via, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'assigned')",
        (tid, kind, location, description, severity, ",".join(departments), source, name, phone, email, via))
    con.execute("INSERT INTO incident_updates (incident_id, status, note) VALUES (?, 'reported', ?)",
                (cur.lastrowid, f"Reported by {source}" + (f" via {via}" if via else "")))
    con.execute("INSERT INTO incident_updates (incident_id, status, note) VALUES (?, 'assigned', ?)",
                (cur.lastrowid, "Assigned to " + " and ".join(departments)))
    con.commit()
    con.close()
    print(f"New incident {tid}: {kind} at {location} -> {', '.join(departments)}")
    return tid


# ---------- login helpers ----------
def check_password(password, stored):
    salt_hex, digest_hex = stored.split(":")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 100_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def current_user(authorization):
    token = (authorization or "").removeprefix("Bearer ").strip()
    con = db()
    user = con.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token=?", (token,)
    ).fetchone()
    con.close()
    if not user:
        raise HTTPException(401, "Please log in")
    return user


def can_handle(user, incident):
    return user["role"] == "command" or user["department"] in incident["department"].split(",")


# ---------- MQTT (IoT sensors) ----------
def on_connect(client, userdata, flags, reason_code, properties):
    state["mqtt_connected"] = True
    client.subscribe(TOPIC)
    print("Connected to MQTT broker, listening on", TOPIC)


def on_disconnect(client, userdata, flags, reason_code, properties):
    state["mqtt_connected"] = False
    print("MQTT broker disconnected")


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        kind = str(data.get("type", "")).lower()
        if kind not in ROUTING:
            print("Ignored sensor alert, unknown type:", kind)
            return
        severity = str(data.get("severity", DEFAULT_SEVERITY[kind])).lower()
        if severity not in ("low", "medium", "high"):
            severity = DEFAULT_SEVERITY[kind]
        create_incident(kind, str(data.get("location", "Unknown"))[:100],
                        f"Automatic {kind} alert from IoT sensor", severity, "sensor")
        state["last_sensor_alert"] = time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        print("Bad sensor message:", msg.payload, e)


mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message = on_message


# ---------- Google Form (citizens reporting online) ----------
def gform_check_once():
    with urllib.request.urlopen(GFORM_URL, timeout=15) as res:
        data = json.loads(res.read().decode())
    if "error" in data:
        raise ValueError(data["error"])
    con = db()
    con.execute("CREATE TABLE IF NOT EXISTS gform_imported (row INTEGER PRIMARY KEY)")
    done = {r[0] for r in con.execute("SELECT row FROM gform_imported")}
    con.close()
    for item in data["rows"]:
        if item["row"] in done:
            continue
        tracking_id = str(item.get("tracking_id", "")).strip().upper()
        if not tracking_id:
            # Google adds the tracking ID a few seconds after the answer arrives: wait up to 60 s for it
            first_seen = gform_waiting.setdefault(item["row"], time.time())
            if time.time() - first_seen < 60:
                continue
        gform_waiting.pop(item["row"], None)
        kind = str(item.get("type", "")).strip().lower()
        if kind in ROUTING:
            create_incident(kind, str(item.get("location", "Unknown"))[:100] or "Unknown",
                            str(item.get("description", ""))[:500], DEFAULT_SEVERITY[kind], "citizen",
                            str(item.get("name", ""))[:60], str(item.get("phone", ""))[:20], via="Google Form",
                            email=str(item.get("email", ""))[:100] or None, tracking_id=tracking_id or None)
        con = db()
        con.execute("INSERT OR IGNORE INTO gform_imported (row) VALUES (?)", (item["row"],))
        con.commit()
        con.close()


def gform_loop():
    while True:
        try:
            gform_check_once()
            state["gform_last_sync"] = time.strftime("%Y-%m-%d %H:%M:%S")
            state["gform_error"] = None
        except Exception as e:
            if state["gform_error"] != str(e):
                print("Google Form check failed:", e)
            state["gform_error"] = str(e)
        time.sleep(GFORM_EVERY)


def upgrade_database():
    # Adds new columns if the database does not have them yet
    con = db()
    columns = [r["name"] for r in con.execute("PRAGMA table_info(incidents)")]
    for column in ("via", "reporter_email"):
        if column not in columns:
            con.execute(f"ALTER TABLE incidents ADD COLUMN {column} TEXT")
    con.commit()
    con.close()


@asynccontextmanager
async def lifespan(app):
    upgrade_database()
    mqtt_client.connect_async(BROKER, 1883)
    mqtt_client.loop_start()
    if GFORM_URL:
        threading.Thread(target=gform_loop, daemon=True).start()
        print("Google Form sync is ON")
    yield
    mqtt_client.loop_stop()


app = FastAPI(lifespan=lifespan)


# ---------- PUBLIC: citizens ----------
class Report(BaseModel):
    type: str
    location: str = Field(min_length=3, max_length=100)
    description: str = Field(min_length=5, max_length=500)
    name: str = Field(min_length=2, max_length=60)
    phone: str = Field(min_length=7, max_length=20)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=100)


@app.post("/api/report")
def report(r: Report, request: Request):
    kind = r.type.lower()
    if kind not in ROUTING:
        raise HTTPException(400, "Unknown incident type")
    ip = request.client.host
    now = time.time()
    recent_reports[ip] = [t for t in recent_reports.get(ip, []) if now - t < REPORT_WINDOW]
    if len(recent_reports[ip]) >= REPORT_LIMIT:
        raise HTTPException(429, "Too many reports from this device. Please wait a few minutes.")
    recent_reports[ip].append(now)
    tid = create_incident(kind, r.location.strip(), r.description.strip(),
                          DEFAULT_SEVERITY[kind], "citizen", r.name.strip(), r.phone.strip(),
                          email=r.email.strip().lower())
    return {"tracking_id": tid, "department": ROUTING[kind]}


@app.get("/api/track/{tracking_id}")
def track(tracking_id: str):
    con = db()
    inc = con.execute("SELECT * FROM incidents WHERE tracking_id=?",
                      (tracking_id.strip().upper(),)).fetchone()
    if not inc:
        con.close()
        raise HTTPException(404, "Tracking ID not found")
    updates = con.execute(
        "SELECT status, note, created_at FROM incident_updates WHERE incident_id=? ORDER BY id",
        (inc["id"],)).fetchall()
    con.close()
    # Only safe fields: no name or phone number
    return {"tracking_id": inc["tracking_id"], "type": inc["type"], "location": inc["location"],
            "department": inc["department"], "status": inc["status"],
            "created_at": inc["created_at"], "updates": [dict(u) for u in updates]}


# ---------- STAFF: login ----------
class Login(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(body: Login):
    con = db()
    user = con.execute("SELECT * FROM users WHERE username=?", (body.username.strip().lower(),)).fetchone()
    if not user or not check_password(body.password, user["password_hash"]):
        con.close()
        raise HTTPException(401, "Wrong username or password")
    token = secrets.token_hex(24)
    con.execute("INSERT INTO sessions (token, user_id) VALUES (?,?)", (token, user["id"]))
    con.commit()
    con.close()
    return {"token": token, "name": user["full_name"], "role": user["role"],
            "department": user["department"]}


@app.post("/api/logout")
def logout(authorization: str = Header(None)):
    token = (authorization or "").removeprefix("Bearer ").strip()
    con = db()
    con.execute("DELETE FROM sessions WHERE token=?", (token,))
    con.commit()
    con.close()
    return {"ok": True}


@app.get("/api/me")
def me(authorization: str = Header(None)):
    u = current_user(authorization)
    return {"name": u["full_name"], "username": u["username"], "role": u["role"], "department": u["department"]}


# ---------- STAFF: profile (change display name / password) ----------
def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex() + ":" + digest.hex()


class NewName(BaseModel):
    full_name: str


class NewPassword(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/profile/name")
def change_name(body: NewName, authorization: str = Header(None)):
    u = current_user(authorization)
    name = body.full_name.strip()
    if not 2 <= len(name) <= 40:
        raise HTTPException(400, "Name must be 2 to 40 characters.")
    con = db()
    con.execute("UPDATE users SET full_name=? WHERE id=?", (name, u["id"]))
    con.commit()
    con.close()
    return {"ok": True, "name": name}


@app.post("/api/profile/password")
def change_password(body: NewPassword, authorization: str = Header(None)):
    u = current_user(authorization)
    if not check_password(body.current_password, u["password_hash"]):
        raise HTTPException(400, "Your current password is wrong.")
    if len(body.new_password) < 6:
        raise HTTPException(400, "New password must be at least 6 characters.")
    if body.new_password == body.current_password:
        raise HTTPException(400, "New password must be different from the old one.")
    token = (authorization or "").removeprefix("Bearer ").strip()
    con = db()
    con.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(body.new_password), u["id"]))
    # log out every OTHER browser using this account; this one stays logged in
    con.execute("DELETE FROM sessions WHERE user_id=? AND token<>?", (u["id"], token))
    con.commit()
    con.close()
    return {"ok": True}


# ---------- STAFF: incidents ----------
@app.get("/api/incidents")
def incidents(authorization: str = Header(None)):
    user = current_user(authorization)
    con = db()
    rows = con.execute("SELECT * FROM incidents ORDER BY id DESC").fetchall()
    con.close()
    return [dict(r) for r in rows if can_handle(user, r)]


def change_status(incident_id, new_status, allowed_from, note, authorization):
    user = current_user(authorization)
    if user["role"] == "command":
        raise HTTPException(403, "The Command Center can only view incidents. The department must accept and resolve them.")
    con = db()
    inc = con.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
    if not inc:
        con.close()
        raise HTTPException(404, "Incident not found")
    if not can_handle(user, inc):
        con.close()
        raise HTTPException(403, "This incident belongs to another department")
    if inc["status"] not in allowed_from:
        con.close()
        raise HTTPException(400, f"Incident is already {inc['status']}")
    con.execute("UPDATE incidents SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
                (new_status, incident_id))
    con.execute("INSERT INTO incident_updates (incident_id, status, note, by_user) VALUES (?,?,?,?)",
                (incident_id, new_status, note.format(name=user["full_name"]), user["full_name"]))
    con.commit()
    con.close()
    return {"ok": True, "status": new_status}


@app.post("/api/incidents/{incident_id}/accept")
def accept(incident_id: int, authorization: str = Header(None)):
    return change_status(incident_id, "in_progress", ("reported", "assigned"),
                         "Accepted by {name}, team on the way", authorization)


@app.post("/api/incidents/{incident_id}/resolve")
def resolve(incident_id: int, authorization: str = Header(None)):
    return change_status(incident_id, "resolved", ("in_progress",),
                         "Resolved by {name}", authorization)


# ---------- STAFF: activity log ----------
@app.get("/api/events")
def events(authorization: str = Header(None)):
    user = current_user(authorization)
    con = db()
    rows = con.execute(
        "SELECT u.id, u.status, u.note, u.by_user, u.created_at, i.id AS incident_id, "
        "i.tracking_id, i.type, i.location, i.department "
        "FROM incident_updates u JOIN incidents i ON i.id = u.incident_id "
        "ORDER BY u.id DESC LIMIT 1000").fetchall()
    con.close()
    return [dict(r) for r in rows if can_handle(user, r)]


# ---------- COMMAND CENTER: system status ----------
@app.get("/api/system")
def system(authorization: str = Header(None)):
    user = current_user(authorization)
    if user["role"] != "command":
        raise HTTPException(403, "Command Center only")
    con = db()
    counts = {r["status"]: r["n"] for r in
              con.execute("SELECT status, COUNT(*) n FROM incidents GROUP BY status")}
    sensors = con.execute("SELECT COUNT(*) FROM incidents WHERE source='sensor'").fetchone()[0]
    citizens = con.execute("SELECT COUNT(*) FROM incidents WHERE source='citizen'").fetchone()[0]
    con.close()
    return {"mqtt_connected": state["mqtt_connected"], "last_sensor_alert": state["last_sensor_alert"],
            "gform_enabled": bool(GFORM_URL), "gform_last_sync": state["gform_last_sync"],
            "gform_error": state["gform_error"],
            "by_status": counts, "from_sensors": sensors, "from_citizens": citizens,
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S")}


# ---------- website (the files in C:\NEXORA\dashboard) ----------
app.mount("/", StaticFiles(directory=DASHBOARD, html=True), name="dashboard")