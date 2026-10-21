"""Supported single-worker launcher, with application-owned proxy trust."""

import argparse
import logging
import os
import sys
from pathlib import Path

import uvicorn


def main(*, sockets=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.chdir(root)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        from services.auth import AuthSettings
        settings = AuthSettings.from_env()
        if not settings.secure_cookie or not settings.trusted_proxies:
            parser.error("Non-loopback binding requires configured HTTPS origins and an explicit trusted reverse proxy")
    security_logger = logging.getLogger("crypto_ai.security")
    security_logger.setLevel(logging.INFO)
    if not security_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        security_logger.addHandler(handler)
    security_logger.propagate = False
    # No access log: its raw target can contain secrets supplied in a URL.
    # Security events use route templates and server-generated correlation IDs.
    config = uvicorn.Config("main:app", host=args.host, port=args.port,
                workers=1, proxy_headers=False, access_log=False,
                limit_concurrency=64, timeout_keep_alive=5)
    uvicorn.Server(config).run(sockets=sockets)


if __name__ == "__main__":
    main()
