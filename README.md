# Karan Portfolio

## Local Run

Run the built-in Python app server from the project root:

```powershell
python server.py
```

Then open:

```text
http://127.0.0.1:8000/
http://127.0.0.1:8000/?admin=1
```

Admin login:

```text
ID: admin
Password: m3333@india
```

## Visitor Logging

The local app server writes visitor logs into a SQLite database under `data/visitor_logs.db`.
The admin panel shows unique visitors from the last 7 days, grouped by IP, with country and region/province details when available.

## Deployment Note

GitHub Pages alone cannot run the Python backend or store server-side visitor logs.
To use the visitor log feature live, the frontend must be paired with a hosted backend.
