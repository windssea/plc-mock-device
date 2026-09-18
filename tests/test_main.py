from __future__ import annotations

import sqlite3
import sys

import yaml

from iot_sim import main as main_module
from iot_sim.models import BootstrapConfig


def test_serve_enters_recovery_mode_for_invalid_database_schema(
    tmp_path, monkeypatch
) -> None:
    yaml_path = tmp_path / "devices.yaml"
    yaml_path.write_text(
        yaml.safe_dump(BootstrapConfig().model_dump(mode="json")), encoding="utf-8"
    )
    db_path = tmp_path / "config.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA user_version = 1")

    invocation: dict[str, object] = {}

    def capture_run(app, **kwargs):
        invocation.update(app=app, **kwargs)

    monkeypatch.setattr(main_module.uvicorn, "run", capture_run)
    monkeypatch.delenv("IOT_SIM_WEB_HOST", raising=False)
    monkeypatch.delenv("IOT_SIM_WEB_PORT", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "iot-sim",
            "serve",
            "--config",
            str(yaml_path),
            "--database",
            str(db_path),
        ],
    )
    main_module.main()

    assert invocation["host"] == "127.0.0.1"
    assert invocation["port"] == 8080
    assert invocation["app"].state.manager.config is None
