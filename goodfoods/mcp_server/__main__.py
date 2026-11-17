"""Entry point: ``python -m goodfoods.mcp_server [--http] [--host 127.0.0.1] [--port 8765]``."""

from __future__ import annotations

import argparse
import logging
import sys

from goodfoods import config
from goodfoods.core.seed import ensure_database


def main() -> None:
    parser = argparse.ArgumentParser(description="GoodFoods reservations MCP server")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    # stdout is the protocol channel on stdio: all logging goes to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ensure_database(config.DB_PATH)

    from goodfoods.mcp_server.server import mcp, service

    service()  # warm up (load catalog, open DB) before the first request
    if args.http:
        mcp.run("streamable-http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
