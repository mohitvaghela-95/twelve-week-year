"""`twy-web`: run the web app with Flask's built-in server, for trying it out locally.

On the home server, use the Docker image instead, which runs gunicorn.
"""

import argparse

from twy.web import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the twy web app.")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default: all interfaces)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--debug", action="store_true", help="auto-reload and show tracebacks")
    args = parser.parse_args()
    create_app().run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
