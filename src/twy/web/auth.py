"""Single-password login, a 30-day session cookie and CSRF tokens on every form."""

import hmac
import secrets
import time

from flask import Blueprint, Flask, abort, current_app, redirect, render_template, request, session, url_for

bp = Blueprint("auth", __name__)

PUBLIC_ENDPOINTS = {"auth.login", "web.healthz", "web.calendar_feed", "static"}


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
    error = None
    if request.method == "POST":
        given = request.form.get("password", "").encode()
        if hmac.compare_digest(given, current_app.config["TWY_PASSWORD"].encode()):
            session.clear()
            session["auth"] = True
            session.permanent = True
            return redirect(_safe_next(request.args.get("next")))
        time.sleep(1)  # slow down guessing
        error = "Wrong password."
    return render_template("login.html", error=error), 401 if error else 200


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
