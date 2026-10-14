"""Single-password login, a 30-day session cookie and CSRF tokens on every form."""

import hmac
import secrets
import threading
import time

from flask import Blueprint, Flask, abort, current_app, redirect, render_template, request, session, url_for

bp = Blueprint("auth", __name__)

PUBLIC_ENDPOINTS = {"auth.login", "web.healthz", "web.calendar_feed", "static"}

MAX_FAILURES = 5  # wrong passwords in a row from one address before it is locked out
LOCKOUT_SECONDS = 30


class LoginThrottle:
    """Per-address lockout after repeated wrong passwords.

    Rejecting straight away (rather than sleeping) keeps gunicorn's few threads free, so
    someone guessing can't also make the app hang. In memory, which suits the single worker.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, tuple[int, float]] = {}  # address → (count, locked until)

    def locked(self, address: str) -> bool:
        with self._lock:
            return self._failures.get(address, (0, 0.0))[1] > time.monotonic()

    def failed(self, address: str) -> None:
        with self._lock:
            count = self._failures.get(address, (0, 0.0))[0] + 1
            until = time.monotonic() + LOCKOUT_SECONDS if count >= MAX_FAILURES else 0.0
            self._failures[address] = (0 if until else count, until)

    def succeeded(self, address: str) -> None:
        with self._lock:
            self._failures.pop(address, None)


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _safe_next(target: str | None) -> str:
    """Only follow local paths after login."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("web.index")


def init_app(app: Flask) -> None:
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.extensions["twy_login_throttle"] = LoginThrottle()
    app.register_blueprint(bp)

    @app.before_request
    def guard():
        if request.method == "POST":
            sent = request.form.get("csrf", "")
            if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
                abort(400, "This form has expired. Go back, reload the page and try again.")
        if request.endpoint not in PUBLIC_ENDPOINTS and not session.get("auth"):
            target = request.full_path if request.query_string else request.path
            return redirect(url_for("auth.login", next=target))


@bp.route("/login", methods=["GET", "POST"])
def login():
    error, status = None, 200
    if request.method == "POST":
        throttle: LoginThrottle = current_app.extensions["twy_login_throttle"]
        address = request.remote_addr or ""
        given = request.form.get("password", "").encode()
        if throttle.locked(address):
            error, status = f"Too many wrong passwords. Wait {LOCKOUT_SECONDS} seconds and try again.", 429
        elif hmac.compare_digest(given, current_app.config["TWY_PASSWORD"].encode()):
            throttle.succeeded(address)
            session.clear()
            session["auth"] = True
            session.permanent = True
            return redirect(_safe_next(request.args.get("next")))
        else:
            throttle.failed(address)
            error, status = "Wrong password.", 401
    return render_template("login.html", error=error), status


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
