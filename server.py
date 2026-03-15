from http import cookies
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import json
import os
from srm_service import SrmPortalService


ROOT = Path(__file__).parent
DATA_FILE = ROOT / "data" / "dashboard.json"
SESSIONS: dict[str, str] = {}
SRM_SERVICE = SrmPortalService(DATA_FILE)
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"


class DashboardHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        if self.path == "/api/auth-context":
            self._serve_auth_context()
            return

        if self.path == "/api/session":
            self._serve_session()
            return

        if self.path == "/api/dashboard":
            self._serve_dashboard()
            return

        if self.path == "/":
            self.path = "/index.html"

        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/login":
            self._handle_login()
            return

        if self.path == "/api/logout":
            self._handle_logout()
            return

        self.send_error(404)

    def _handle_login(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length)

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid request body."}, status=400)
            return

        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        captcha = str(payload.get("captcha", "")).strip()
        prelogin_id = str(payload.get("preloginId", "")).strip()
        if not username or not password or not prelogin_id:
            self._send_json({"error": "Username, password, and login session are required."}, status=400)
            return

        try:
            session_id, dashboard = SRM_SERVICE.login(username, password, captcha, prelogin_id)
        except ValueError as error:
            self._send_json({"error": str(error)}, status=401)
            return
        SESSIONS[session_id] = username

        body = json.dumps(dashboard).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", self._build_session_cookie(session_id))
        self.end_headers()
        self.wfile.write(body)

    def _handle_logout(self):
        session_id = self._read_session_id()
        if session_id:
            SESSIONS.pop(session_id, None)
            SRM_SERVICE.logout(session_id)

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", self._build_session_cookie("", max_age=0))
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def _serve_auth_context(self):
        try:
            context = SRM_SERVICE.create_auth_context()
        except Exception as error:
            self._send_json({"error": str(error)}, status=502)
            return
        self._send_json(context)

    def _serve_session(self):
        if not self._is_authenticated():
            self._send_json({"error": "Not signed in."}, status=401)
            return

        dashboard = SRM_SERVICE.get_session_dashboard(self._read_session_id())
        if dashboard is None:
            self._send_json({"error": "Session expired."}, status=401)
            return
        self._send_json(dashboard)

    def _serve_dashboard(self):
        if not self._is_authenticated():
            self._send_json({"error": "Not signed in."}, status=401)
            return

        dashboard = SRM_SERVICE.get_session_dashboard(self._read_session_id())
        if dashboard is None:
            self._send_json({"error": "Session expired."}, status=401)
            return
        self._send_json(dashboard)

    def _is_authenticated(self) -> bool:
        session_id = self._read_session_id()
        return bool(session_id and session_id in SESSIONS)

    def _read_session_id(self):
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None

        jar = cookies.SimpleCookie()
        jar.load(cookie_header)
        cookie = jar.get("session_id")
        return cookie.value if cookie else None

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _build_session_cookie(self, session_id: str, max_age: int | None = None) -> str:
        parts = [
            f"session_id={session_id}",
            "HttpOnly",
            "Path=/",
            "SameSite=Lax",
        ]
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
        if COOKIE_SECURE:
            parts.append("Secure")
        return "; ".join(parts)

    def log_message(self, format, *args):
        return


def load_dashboard_payload():
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def validate_dashboard_payload() -> None:
    payload = load_dashboard_payload()
    required_keys = {"student", "lastSynced", "timetable", "attendance", "marks", "courses", "cgpaCourses"}
    missing = required_keys.difference(payload.keys())
    if missing:
        raise ValueError(f"dashboard.json is missing keys: {sorted(missing)}")


def main() -> None:
    validate_dashboard_payload()
    server = HTTPServer((HOST, PORT), DashboardHandler)
    print(f"Serving BunkMate at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
