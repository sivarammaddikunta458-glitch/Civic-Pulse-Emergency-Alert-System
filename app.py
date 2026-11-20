import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("CIVIC_PULSE_DB", BASE_DIR / "civic_pulse.db"))
app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

STATUSES = ("Pending", "Saved Offline", "Sending", "Alert Sent", "Assistance Dispatched", "Resolved")
CATEGORIES = ("Medical Emergency", "Accident", "Personal Safety", "Other Emergency")


@contextmanager
def db_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    with db_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL UNIQUE,
                emergency_contact_name TEXT,
                emergency_contact_phone TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                client_request_id TEXT UNIQUE,
                category TEXT NOT NULL,
                message TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                location_link TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                last_error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(alerts)")}
        if "client_request_id" not in columns:
            connection.execute("ALTER TABLE alerts ADD COLUMN client_request_id TEXT")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_client_request_id "
            "ON alerts(client_request_id) WHERE client_request_id IS NOT NULL"
        )


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def row_to_dict(row):
    return dict(row) if row else None


def send_sms(alert, user):
    """Send through a configured provider, or stay safely in demo mode.

    A production deployment should replace this adapter with the chosen
    operator/provider integration. No claim is made that SMS is free.
    """
    if os.getenv("CIVIC_PULSE_SMS_PROVIDER", "demo").lower() == "demo":
        return False, "Demo mode: configure an SMS provider to transmit alerts."
    # Provider integrations belong here. Keeping the boundary explicit avoids
    # pretending a browser or Flask server can send carrier SMS by itself.
    return False, "SMS provider integration is not configured."


def alert_payload(row):
    payload = row_to_dict(row)
    if payload:
        payload["map_url"] = (
            f"https://www.openstreetmap.org/?mlat={payload['latitude']}&mlon={payload['longitude']}#map=18/{payload['latitude']}/{payload['longitude']}"
            if payload["latitude"] is not None and payload["longitude"] is not None
            else None
        )
    return payload


@app.get("/")
def index():
    return render_template("index.html", categories=CATEGORIES, statuses=STATUSES)


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "Civic Pulse", "sms_mode": os.getenv("CIVIC_PULSE_SMS_PROVIDER", "demo").lower()})


@app.post("/api/users")
def create_user():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required."}), 400
    name = str(data.get("name", "")).strip()
    phone = str(data.get("phone", "")).strip()
    if not name or not phone:
        return jsonify({"error": "Name and phone number are required."}), 400
    if len(name) > 120 or len(phone) > 40:
        return jsonify({"error": "Name or phone number is too long."}), 400
    contact_name = str(data.get("emergency_contact_name") or "").strip()
    contact_phone = str(data.get("emergency_contact_phone") or "").strip()
    if len(contact_name) > 120 or len(contact_phone) > 40:
        return jsonify({"error": "Emergency contact details are too long."}), 400
    try:
        with db_connection() as connection:
            cursor = connection.execute(
                "INSERT INTO users (name, phone, emergency_contact_name, emergency_contact_phone, created_at) VALUES (?, ?, ?, ?, ?)",
                (name, phone, contact_name or None, contact_phone or None, now_iso()),
            )
            return jsonify({"id": cursor.lastrowid, "message": "Registration saved."}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "That phone number is already registered."}), 409


@app.post("/api/alerts")
def create_alert():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required."}), 400
    try:
        user_id = int(data["user_id"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "A valid user_id is required."}), 400
    category = str(data.get("category", "")).strip()
    if category not in CATEGORIES:
        return jsonify({"error": "Choose a valid emergency category."}), 400
    request_id = data.get("request_id")
    if request_id is not None:
        try:
            request_id = str(uuid.UUID(str(request_id)))
        except (ValueError, AttributeError):
            return jsonify({"error": "A valid request_id is required."}), 400
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    if (latitude is None) != (longitude is None):
        return jsonify({"error": "Latitude and longitude must be provided together."}), 400
    if latitude is not None:
        try:
            latitude, longitude = float(latitude), float(longitude)
        except (TypeError, ValueError):
            return jsonify({"error": "Coordinates must be numbers."}), 400
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return jsonify({"error": "Coordinates are outside the valid range."}), 400
    with db_connection() as connection:
        user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return jsonify({"error": "Register the citizen before creating an alert."}), 404
        if request_id:
            existing = connection.execute(
                "SELECT alerts.*, users.name, users.phone FROM alerts JOIN users ON users.id = alerts.user_id WHERE client_request_id = ?",
                (request_id,),
            ).fetchone()
            if existing:
                if existing["user_id"] != user_id:
                    return jsonify({"error": "That request_id belongs to another user."}), 409
                return jsonify({"alert": alert_payload(existing), "sms_sent": existing["status"] == "Alert Sent", "notice": existing["last_error"], "duplicate": True}), 200
        message = f"CIVIC PULSE ALERT: {category} reported by {user['name']} ({user['phone']})."
        location_link = (
            f"https://www.openstreetmap.org/?mlat={latitude}&mlon={longitude}#map=18/{latitude}/{longitude}"
            if latitude is not None
            else None
        )
        cursor = connection.execute(
            "INSERT INTO alerts (user_id, client_request_id, category, message, latitude, longitude, location_link, created_at, status, attempts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (user_id, request_id, category, message, latitude, longitude, location_link, now_iso(), "Pending"),
        )
        alert_id = cursor.lastrowid
        alert = connection.execute("SELECT alerts.*, users.name, users.phone FROM alerts JOIN users ON users.id = alerts.user_id WHERE alerts.id = ?", (alert_id,)).fetchone()
        sent, error = send_sms(alert, user)
        new_status = "Alert Sent" if sent else "Saved Offline"
        connection.execute("UPDATE alerts SET status = ?, attempts = attempts + 1, last_error = ? WHERE id = ?", (new_status, None if sent else error, alert_id))
        alert = connection.execute("SELECT alerts.*, users.name, users.phone FROM alerts JOIN users ON users.id = alerts.user_id WHERE alerts.id = ?", (alert_id,)).fetchone()
        return jsonify({"alert": alert_payload(alert), "sms_sent": sent, "notice": error}), 201


@app.get("/api/alerts")
def list_alerts():
    with db_connection() as connection:
        rows = connection.execute("SELECT alerts.*, users.name, users.phone FROM alerts JOIN users ON users.id = alerts.user_id ORDER BY alerts.created_at DESC").fetchall()
    return jsonify([alert_payload(row) for row in rows])


@app.patch("/api/alerts/<int:alert_id>")
def update_alert(alert_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required."}), 400
    status = data.get("status")
    if status not in STATUSES:
        return jsonify({"error": "Invalid alert status."}), 400
    with db_connection() as connection:
        cursor = connection.execute("UPDATE alerts SET status = ? WHERE id = ?", (status, alert_id))
        if cursor.rowcount == 0:
            return jsonify({"error": "Alert not found."}), 404
    return jsonify({"message": "Alert status updated."})


@app.post("/api/alerts/retry")
def retry_alerts():
    with db_connection() as connection:
        pending = connection.execute("SELECT alerts.*, users.name, users.phone FROM alerts JOIN users ON users.id = alerts.user_id WHERE alerts.status IN ('Pending', 'Saved Offline')").fetchall()
        sent_count = 0
        for alert in pending:
            user = connection.execute("SELECT * FROM users WHERE id = ?", (alert["user_id"],)).fetchone()
            sent, error = send_sms(alert, user)
            connection.execute("UPDATE alerts SET status = ?, attempts = attempts + 1, last_error = ? WHERE id = ?", ("Alert Sent" if sent else "Saved Offline", None if sent else error, alert["id"]))
            sent_count += int(sent)
    return jsonify({"checked": len(pending), "sent": sent_count})


initialize_database()

if __name__ == "__main__":
    app.run(debug=os.getenv("CIVIC_PULSE_DEBUG", "").lower() == "true")
