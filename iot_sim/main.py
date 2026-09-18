"""Command line entrypoint."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import uvicorn

from .api import create_app
from .config_store import (
    ConfigStore,
    ConfigStoreError,
    load_bootstrap_config_sync,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PLCnext IoT virtual device simulator")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("serve", "validate"),
        default="serve",
    )
    parser.add_argument(
        "--config",
        default=os.getenv("IOT_SIM_CONFIG", "config/example.yaml"),
        help="restricted bootstrap YAML path (startup defaults only)",
    )
    parser.add_argument(
        "--database",
        default=os.getenv("IOT_SIM_DATABASE", "data/iot-simulator.db"),
        help="SQLite runtime configuration database path",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.command == "validate":
        bootstrap = load_bootstrap_config_sync(args.config)
        print(
            "bootstrap configuration valid: "
            f"version={bootstrap.version}, initial_revision=0, initial_devices=0"
        )
        return

    try:
        config = ConfigStore(args.database, bootstrap_path=args.config).load_sync()
        default_host, default_port = config.web.host, config.web.port
    except ConfigStoreError as exc:
        logging.getLogger(__name__).error(
            "Starting management console in configuration recovery mode: %s", exc
        )
        default_host, default_port = "127.0.0.1", 8080
    host = os.getenv("IOT_SIM_WEB_HOST", default_host)
    port = int(os.getenv("IOT_SIM_WEB_PORT", str(default_port)))
    uvicorn.run(
        create_app(str(Path(args.config)), str(Path(args.database))),
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
