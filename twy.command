#!/bin/zsh
# Launch twy in a Terminal window.
# Double-click this file in Finder, or a Finder alias to it (e.g. on the Desktop).

# Run from the project folder, wherever this script lives.
cd "${0:A:h}" || exit 1

# Finder-launched shells may not have Homebrew or ~/.local/bin on PATH.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

printf '\e]0;12 Week Year\a'  # window title

# Once deployed to a home server (scripts/deploy.sh writes deploy.env), open the
# terminal app there so it uses the same data as the web app.
[[ -f deploy.env ]] && source deploy.env
if [[ -n "${TWY_SERVER:-}" ]]; then
    exec ssh -t "$TWY_SERVER" docker exec -it twy twy
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv was not found. Install it with: brew install uv"
    read -k1 "?Press any key to close…"
    exit 1
fi

uv run --quiet twy
