# Civic Pulse

Civic Pulse is a Flask and SQLite emergency-alert prototype. It includes a citizen registration form, GPS-assisted alert creation, an alert dashboard, and a device-side queue for alerts created while the server is unreachable.

> **Prototype only:** This project is not connected to emergency services. The default SMS adapter is demo-only and never sends an SMS. Do not rely on this application for real emergencies or deploy it with real personal data without adding authentication, authorization, privacy controls, and a verified SMS provider.

## Requirements

- Python 3.10 or newer
- pip
- A modern browser; location access requires permission and a secure context (localhost is supported)

## Run locally (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py app.py
```

Open <http://127.0.0.1:5000>. To stop the development server, press `Ctrl+C`.

The app creates `civic_pulse.db` next to `app.py` on first run. Existing alert tables are migrated automatically when the app starts. Set `CIVIC_PULSE_DB` to use a different SQLite file:

```powershell
$env:CIVIC_PULSE_DB = "C:\path\to\civic-pulse.db"
py app.py
```

## How it behaves

- Register while the server is reachable. The browser remembers the resulting user ID on that device.
- The browser requests location permission when an alert is raised. Coordinates are optional.
- If the app server cannot be reached, an alert is queued in that browser's local storage and synced when connectivity returns or **Retry pending alerts** is pressed. Keep the app open for automatic retry; browser storage is not a guaranteed emergency-message delivery mechanism.
- SQLite stores server-side alerts. The dashboard API is unauthenticated and is suitable only for local prototyping.
- `CIVIC_PULSE_SMS_PROVIDER` defaults to `demo`. The `send_sms()` adapter in `app.py` returns an explicit non-delivery result until a real provider is implemented and tested. A provider name alone does not configure SMS delivery.
- GPS can work without mobile data, but map links require internet to open. SMS and any emergency response depend on separately configured services.

## Run tests

```powershell
py -m unittest discover -s tests -v
```

## Before deployment

Implement and test a real SMS provider, authenticate and authorize all dashboard and API access, add consent/data-retention controls, use HTTPS, and validate operational reliability. This demo makes no guarantee of delivery, background execution, or emergency-service response.
