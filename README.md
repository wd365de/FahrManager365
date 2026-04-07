# FahrManager 360 (lokales MVP)

Lokale Webanwendung mit FastAPI, Jinja2 und SQLite.

## Projektstruktur

```text
.
├── app
│   ├── __init__.py
│   ├── auth.py
│   ├── database.py
│   ├── main.py
│   ├── models.py
│   ├── routes
│   │   ├── __init__.py
│   │   ├── admin_routes.py
│   │   ├── appointments_routes.py
│   │   ├── auth_routes.py
│   │   ├── portal_routes.py
│   │   └── utils.py
│   ├── static
│   │   └── styles.css
│   └── templates
│       ├── appointments_list.html
│       ├── base.html
│       ├── dashboard.html
│       ├── login.html
│       ├── portal.html
│       ├── slots_list.html
│       ├── student_form.html
│       ├── students_list.html
│       └── teachers_list.html
├── README.md
└── requirements.txt
```

## Start unter Windows (PowerShell)

1. Virtuelle Umgebung erstellen:

```powershell
python -m venv .venv
```

2. Aktivieren:

```powershell
.\.venv\Scripts\Activate.ps1
```

3. Abhängigkeiten installieren:

```powershell
pip install -r requirements.txt
```

4. App starten:

```powershell
uvicorn app.main:app --reload
```

5. Öffnen:

- `http://127.0.0.1:8000/login`

## Datenbank

- SQLite-Datei wird beim ersten Start automatisch erzeugt:
  - `fahrmanager360.db`

## Demo-Admin (automatisch angelegt)

- E-Mail: `admin@fahrmanager360.local`
- Passwort: `admin123`

## MVP-Funktionen

- Login / Logout (Session-basiert)
- Admin kann Schüler anlegen
- Admin kann Fahrlehrer anlegen
- Jeder Schüler wird einem festen Fahrlehrer zugeordnet
- Admin kann Verfügbarkeitsfenster als Einzelfenster anlegen
- Admin hat Wochenplan mit Schnellanlage in 90-Minuten-Blöcken
- Admin definiert pro Fenster: `Buchbar X Stunden vorher`
- Schüler sieht nur aktuell buchbare Zeitoptionen seines festen Fahrlehrers
- Schülerportal bietet Wochenansicht mit Vor-/Folgewoche
- Buchungsoptionen im Schülerportal sind nach Dauer filterbar
- Schüler kann variable Fahrstunden buchen (`45`, `60`, `90`, `120` Minuten)
- Schüler kann eigene Termine sehen und stornieren
- Terminstatus: `booked`, `cancelled`, `done`
- Keine Doppelbuchung (inkl. Pufferzeit)
