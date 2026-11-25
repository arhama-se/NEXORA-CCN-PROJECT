# Nexora - creates a NEW database (nexora.db) for the citizen + department system.
# Run:  py create_db.py
# It deletes the old nexora.db first, so only run it when you want a fresh start.
import hashlib, os, sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "nexora.db"


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex() + ":" + digest.hex()


if DB.exists():
    DB.unlink()
    print("Old nexora.db deleted.")

con = sqlite3.connect(DB)
con.executescript("""
CREATE TABLE incidents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_id    TEXT UNIQUE NOT NULL,
    type           TEXT NOT NULL,
    location       TEXT NOT NULL,
    description    TEXT,
    severity       TEXT NOT NULL DEFAULT 'medium' CHECK (severity IN ('low','medium','high')),
    department     TEXT NOT NULL,
    source         TEXT NOT NULL CHECK (source IN ('citizen','sensor')),
    reporter_name  TEXT,
    reporter_phone TEXT,
    status         TEXT NOT NULL DEFAULT 'reported'
                   CHECK (status IN ('reported','assigned','in_progress','resolved')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE incident_updates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    status      TEXT NOT NULL,
    note        TEXT,
    by_user     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('department','command')),
    department    TEXT
);

CREATE TABLE sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
""")

USERS = [
    ("fire",     "fire123",     "Fire Department",       "department", "Fire"),
    ("hospital", "hospital123", "City Hospital",         "department", "Hospital"),
    ("police",   "police123",   "Police Department",     "department", "Police"),
    ("traffic",  "traffic123",  "Traffic Control",       "department", "Traffic"),
    ("command",  "command123",  "Command Center",        "command",    None),
]
for username, password, name, role, dept in USERS:
    con.execute(
        "INSERT INTO users (username, password_hash, full_name, role, department) VALUES (?,?,?,?,?)",
        (username, hash_password(password), name, role, dept))

con.commit()
con.close()
print("New database ready: nexora.db")
print("Logins:  fire/fire123  hospital/hospital123  police/police123  traffic/traffic123  command/command123")