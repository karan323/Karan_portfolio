from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "visitor_logs.db"
HOST = "127.0.0.1"
PORT = 8000
ADMIN_ID = "admin"
ADMIN_PASSWORD = "m3333@india"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_ip(value: str | None) -> str:
    if not value:
        return ""

    candidate = value.split(",")[0].strip()
    try:
        return str(ip_address(candidate))
    except ValueError:
        return ""


def is_public_ip(value: str) -> bool:
    if not value:
        return False

    try:
        parsed = ip_address(value)
    except ValueError:
        return False

    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_reserved
        or parsed.is_multicast
        or parsed.is_unspecified
        or parsed.is_link_local
    )


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS visitor_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                visited_at TEXT NOT NULL,
                ip TEXT NOT NULL,
                country TEXT NOT NULL,
                region TEXT NOT NULL,
                city TEXT NOT NULL,
                timezone TEXT NOT NULL,
                path TEXT NOT NULL,
                referrer TEXT NOT NULL,
                user_agent TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_visitor_logs_visited_at
            ON visitor_logs (visited_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_visitor_logs_ip
            ON visitor_logs (ip)
            """
        )
        connection.commit()


class PortfolioHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == "/api/admin/visitors":
            self.handle_admin_visitors(parsed)
            return

        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == "/api/visit":
            self.handle_log_visit()
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Unknown API endpoint")

    def log_message(self, format: str, *args: Any) -> None:
        super().log_message(format, *args)

    def parse_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}

        return payload if isinstance(payload, dict) else {}

    def send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def is_admin_request(self) -> bool:
        admin_id = self.headers.get("X-Admin-Id", "")
        admin_password = self.headers.get("X-Admin-Password", "")
        return admin_id == ADMIN_ID and admin_password == ADMIN_PASSWORD

    def handle_log_visit(self) -> None:
        payload = self.parse_json_body()

        request_ip = normalize_ip(self.headers.get("X-Forwarded-For")) or normalize_ip(self.client_address[0])
        reported_ip = normalize_ip(str(payload.get("ip", "")))
        ip_to_store = request_ip if is_public_ip(request_ip) else reported_ip or request_ip or "unknown"

        country = str(payload.get("country", "")).strip()
        region = str(payload.get("region", "")).strip()
        city = str(payload.get("city", "")).strip()
        timezone_name = str(payload.get("timezone", "")).strip()
        path = str(payload.get("path", "/")).strip() or "/"
        referrer = str(payload.get("referrer", "")).strip()
        user_agent = self.headers.get("User-Agent", "").strip()

        with sqlite3.connect(DB_PATH) as connection:
            connection.execute(
                """
                INSERT INTO visitor_logs (
                    visited_at, ip, country, region, city, timezone, path, referrer, user_agent
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now_iso(),
                    ip_to_store,
                    country,
                    region,
                    city,
                    timezone_name,
                    path,
                    referrer,
                    user_agent,
                ),
            )
            connection.execute(
                "DELETE FROM visitor_logs WHERE visited_at < ?",
                ((datetime.now(timezone.utc) - timedelta(days=90)).replace(microsecond=0).isoformat(),),
            )
            connection.commit()

        self.send_json({"ok": True})

    def handle_admin_visitors(self, parsed) -> None:
        if not self.is_admin_request():
            self.send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
            return

        query = parse_qs(parsed.query)
        try:
            days = int(query.get("days", ["7"])[0])
        except ValueError:
            days = 7

        days = max(1, min(days, 30))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()

        with sqlite3.connect(DB_PATH) as connection:
            connection.row_factory = sqlite3.Row

            rows = connection.execute(
                """
                SELECT
                    ip,
                    CASE WHEN country = '' THEN 'Unknown' ELSE country END AS country,
                    CASE WHEN region = '' THEN 'Unknown' ELSE region END AS region,
                    CASE WHEN city = '' THEN 'Unknown' ELSE city END AS city,
                    COUNT(*) AS visit_count,
                    MIN(visited_at) AS first_seen,
                    MAX(visited_at) AS last_seen
                FROM visitor_logs
                WHERE visited_at >= ?
                GROUP BY ip, country, region, city
                ORDER BY last_seen DESC
                LIMIT 500
                """,
                (cutoff,),
            ).fetchall()

            summary_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_visits,
                    COUNT(DISTINCT ip) AS unique_visitors,
                    MIN(visited_at) AS first_seen,
                    MAX(visited_at) AS last_seen
                FROM visitor_logs
                WHERE visited_at >= ?
                """,
                (cutoff,),
            ).fetchone()

        visitors = [
            {
                "ip": row["ip"],
                "country": row["country"],
                "region": row["region"],
                "city": row["city"],
                "visit_count": row["visit_count"],
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            }
            for row in rows
        ]

        summary = {
            "total_visits": int(summary_row["total_visits"] or 0),
            "unique_visitors": int(summary_row["unique_visitors"] or 0),
            "first_seen": summary_row["first_seen"],
            "last_seen": summary_row["last_seen"],
        }

        self.send_json(
            {
                "days": days,
                "generated_at": utc_now_iso(),
                "summary": summary,
                "visitors": visitors,
            }
        )


def main() -> None:
    init_db()

    server = ThreadingHTTPServer((HOST, PORT), PortfolioHandler)
    print(f"Serving portfolio app on http://{HOST}:{PORT}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
