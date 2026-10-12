"""Flask web interface for twy, meant to run on a home network.

Run with gunicorn (see the Dockerfile) or `twy-web` for a quick local try.
"""

import os
import secrets
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, g

from twy.db import connect, default_db_path
from twy.domain.scoring import format_score, score_band
from twy.models import hhmm
from twy.services import fmt_num


def _secret_key(db_path: Path) -> str:
    """TWY_SECRET_KEY, else a key generated once and kept next to the database."""
    env = os.environ.get("TWY_SECRET_KEY")
    if env:
        return env
    key_file = db_path.parent / "secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    key_file.write_text(key)
    key_file.chmod(0o600)
    return key


def create_app(
    db_path: Path | str | None = None,
    today: date | None = None,
    password: str | None = None,
    secret_key: str | None = None,
) -> Flask:
    password = password or os.environ.get("TWY_PASSWORD")
    if not password:
        raise RuntimeError("TWY_PASSWORD is not set. Set it (for example in .env) to protect the web app.")
    db_path = Path(db_path) if db_path else default_db_path()
    env_today = os.environ.get("TWY_TODAY")

    app = Flask(__name__)
    app.config.update(
        TWY_DB=db_path,
        TWY_PASSWORD=password,
        TWY_TODAY=today or (date.fromisoformat(env_today) if env_today else None),
        SECRET_KEY=secret_key or _secret_key(db_path),
        SESSION_COOKIE_NAME="twy_session",
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    connect(db_path).close()  # create and migrate up front, not on the first request

    app.jinja_env.filters.update(score=format_score, band=score_band, num=fmt_num, hhmm=hhmm)
    app.teardown_appcontext(_close_repo)

    from twy.web import auth, routes

    auth.init_app(app)
    app.register_blueprint(routes.bp)
    return app


def _close_repo(_exc: BaseException | None) -> None:
    repo = g.pop("repo", None)
    if repo is not None:
        repo.conn.close()
