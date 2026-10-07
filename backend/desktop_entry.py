"""Bundled local API. Parent stdin closes when the desktop process exits."""

import argparse
import multiprocessing
import os
import threading

import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--parent-watch", action="store_true")
    parser.add_argument("--mcp-workspace")
    args = parser.parse_args()
    if args.mcp_workspace:
        from macbot.workspace_server import run
        run(args.mcp_workspace)
        return
    if not os.environ.get("MACBOT_TOKEN"):
        raise SystemExit("MACBOT_TOKEN is required")
    # Initialise core numerical libraries before worker threads start.
    import numpy  # noqa: F401
    import torch  # noqa: F401

    from macbot.api import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info"))
    if args.parent_watch:

        def watch():
            try:
                from macbot.lifecycle import parent_signal
                parent_signal()
            finally:
                server.should_exit = True

        threading.Thread(target=watch, daemon=True, name="desktop-parent").start()
    server.run()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
