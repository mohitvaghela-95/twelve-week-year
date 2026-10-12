#!/usr/bin/env bash
# Copy twy to a home server over SSH and (re)start it with Docker.
#
#   scripts/deploy.sh user@host            first deploy, and every update after
#   scripts/deploy.sh user@host --copy-db  also copy this Mac's database (first time only)
#
# The server address is remembered in deploy.env, so later runs can omit it.
# The server's data/ folder and .env are never overwritten.
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f deploy.env ]] && source deploy.env

SERVER=${TWY_SERVER:-}
COPY_DB=0
for arg in "$@"; do
    case $arg in
        --copy-db) COPY_DB=1 ;;
        -*) echo "Unknown option: $arg" >&2; exit 2 ;;
        *) SERVER=$arg ;;
    esac
done
if [[ -z $SERVER ]]; then
    echo "Usage: $0 user@host [--copy-db]" >&2
    exit 2
fi
REMOTE_DIR=${TWY_REMOTE_DIR:-twy}  # relative to the home folder on the server
remote() { ssh "$SERVER" "$@"; }

echo "→ Copying project to $SERVER:~/$REMOTE_DIR"
remote "mkdir -p '$REMOTE_DIR/data'"
rsync -az --delete \
    --exclude .venv --exclude .git --exclude data --exclude .env --exclude deploy.env \
    --exclude __pycache__ --exclude .pytest_cache \
    ./ "$SERVER:$REMOTE_DIR/"

if ! remote "test -f '$REMOTE_DIR/.env'"; then
    remote "cd '$REMOTE_DIR' && sed -e \"s/^TWY_UID=.*/TWY_UID=\$(id -u)/\" -e \"s/^TWY_GID=.*/TWY_GID=\$(id -g)/\" .env.example > .env && chmod 600 .env"
    echo
    echo "Created ~/$REMOTE_DIR/.env on the server. Set TWY_PASSWORD in it (and TZ if needed):"
    echo "    ssh -t $SERVER nano $REMOTE_DIR/.env"
    echo "Then run this script again."
    exit 1
fi
if remote "grep -qE '^TWY_PASSWORD=\s*$' '$REMOTE_DIR/.env'"; then
    echo "TWY_PASSWORD is empty in ~/$REMOTE_DIR/.env on the server. Set it, then run this again." >&2
    exit 1
fi

if [[ $COPY_DB == 1 ]]; then
    if remote "test -e '$REMOTE_DIR/data/twy.db'"; then
        echo "The server already has data/twy.db, so this Mac's database was not copied." >&2
        echo "To replace it, stop the app (docker compose down) and delete that file first." >&2
        exit 1
    fi
    local_db=$(uv run --quiet python -c 'from twy.db import default_db_path; print(default_db_path())')
    if [[ ! -f $local_db ]]; then
        echo "No local database at $local_db" >&2
        exit 1
    fi
    echo "→ Copying $local_db"
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    # A consistent single-file copy, even if something has the database open.
    uv run --quiet python -c 'import sys; from twy.db import backup_to, connect; backup_to(connect(sys.argv[1]), sys.argv[2])' \
        "$local_db" "$tmp/twy.db"
    scp -q "$tmp/twy.db" "$SERVER:$REMOTE_DIR/data/twy.db"
fi

echo "→ Building and starting the container"
remote "cd '$REMOTE_DIR' && docker compose up -d --build"

if [[ ! -f deploy.env ]]; then
    echo "TWY_SERVER=$SERVER" > deploy.env
    echo "→ Saved the server in deploy.env, so next time just run scripts/deploy.sh."
fi

ip=$(remote "hostname -I 2>/dev/null | awk '{print \$1}'" || true)
port=$(remote "grep -E '^TWY_PORT=' '$REMOTE_DIR/.env' | cut -d= -f2" || true)
echo
echo "Done. Open http://${ip:-<server-ip>}:${port:-8000} on any device on your home network."
