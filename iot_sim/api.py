"""FastAPI management plane and static local console."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import Body, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .config_store import ConfigConflictError
from .models import (
    REDACTED_PASSWORD,
    AppConfig,
    DeviceConfig,
    MqttConnectionConfig,
)
from .runtime import ApplyResult, RuntimeManager
from .state import DesiredState

STATIC_DIR = Path(__file__).with_name("static")
CONSOLE_CACHE_CONTROL = "no-store, no-cache, must-revalidate, max-age=0"


def _redact_connection(data: dict) -> dict:
    redacted = dict(data)
    if redacted.get("password") is not None:
        redacted["password"] = REDACTED_PASSWORD
    return redacted


def _redact_password_fields(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            sensitive_key = isinstance(key, str) and any(
                marker in key.lower() for marker in ("password", "secret", "token")
            )
            safe_key = REDACTED_PASSWORD if sensitive_key else key
            redacted[safe_key] = (
                REDACTED_PASSWORD
                if sensitive_key and item is not None
                else _redact_password_fields(item)
            )
        return redacted
    if isinstance(value, list):
        return [_redact_password_fields(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_password_fields(item) for item in value]
    if isinstance(value, str) and any(
        marker in value.lower() for marker in ("password", "secret", "token")
    ):
        return REDACTED_PASSWORD
    return value


def _safe_validation_errors(exc) -> list[dict]:
    return _redact_password_fields([
        {key: value for key, value in item.items() if key != "ctx"}
        for item in exc.errors()
    ])


def _redacted_config(config: AppConfig) -> dict:
    payload = config.model_dump(mode="json")
    payload["mqtt"] = _redact_connection(payload["mqtt"])
    payload["mqtt_connections"] = [
        _redact_connection(item) for item in payload["mqtt_connections"]
    ]
    return payload


def _resolve_connection_password(
    connection: MqttConnectionConfig,
    existing: MqttConnectionConfig | None,
) -> MqttConnectionConfig:
    if connection.password != REDACTED_PASSWORD:
        return connection
    if existing is None:
        raise ValueError("redacted password cannot be used for a new MQTT connection")
    return connection.model_copy(update={"password": existing.password}, deep=True)


def _resolve_config_passwords(
    candidate: AppConfig,
    current: AppConfig | None,
) -> AppConfig:
    raw = candidate.model_dump(mode="python")
    if raw["mqtt"].get("password") == REDACTED_PASSWORD:
        if current is None:
            raise ValueError("redacted password has no existing MQTT configuration")
        raw["mqtt"]["password"] = current.mqtt.password
    current_profiles = {
        item.id: item for item in (current.mqtt_connections if current else [])
    }
    for profile in raw["mqtt_connections"]:
        if profile.get("password") != REDACTED_PASSWORD:
            continue
        existing = current_profiles.get(profile["id"])
        if existing is None:
            raise ValueError(
                f"redacted password has no existing MQTT connection: {profile['id']}"
            )
        profile["password"] = existing.password
    return AppConfig.model_validate(raw)


def _result_response(result: ApplyResult) -> JSONResponse:
    payload = {
        "config": _redacted_config(result.config),
        "restarted_adapters": result.restarted_adapters,
        "restarted_devices": result.restarted_devices,
        "restart_required": result.restart_required,
    }
    return JSONResponse(
        payload,
        status_code=202 if result.restarted_adapters or result.restart_required else 200,
    )


def create_app(config_path: str, database_path: str | None = None) -> FastAPI:
    manager = RuntimeManager(config_path, database_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await manager.start()
        yield
        await manager.stop()

    app = FastAPI(title="PLCnext IoT Device Simulator", version="0.1.0", lifespan=lifespan)
    app.state.manager = manager
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def disable_console_cache(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = CONSOLE_CACHE_CONTROL
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.exception_handler(ConfigConflictError)
    async def conflict_handler(_request: Request, exc: ConfigConflictError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _request: Request, exc: RequestValidationError
    ):
        return JSONResponse(
            {"detail": _safe_validation_errors(exc)}, status_code=422
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(RuntimeError)
    async def runtime_error_handler(_request: Request, exc: RuntimeError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/v1/config")
    async def get_config():
        if manager.config is None:
            raise HTTPException(503, manager.config_error or "configuration is unavailable")
        return _redacted_config(manager.config)

    @app.put("/api/v1/config")
    async def put_config(config: AppConfig):
        resolved = _resolve_config_passwords(config, manager.config)
        return _result_response(await manager.apply_config(resolved))

    @app.post("/api/v1/config/validate")
    async def validate_config(request: Request):
        raw = await request.body()
        content_type = request.headers.get("content-type", "")
        data = None
        try:
            if "yaml" in content_type:
                data = yaml.safe_load(raw.decode("utf-8"))
            else:
                data = __import__("json").loads(raw)
            config = AppConfig.model_validate(data)
            return {"valid": True, "config": _redacted_config(config)}
        except (ValueError, yaml.YAMLError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                detail = _safe_validation_errors(exc)
            else:
                detail = "invalid configuration"
            raise HTTPException(422, detail) from exc

    @app.get("/api/v1/config/export", response_class=PlainTextResponse)
    async def export_config():
        if manager.config is None:
            raise HTTPException(503, manager.config_error or "configuration is unavailable")
        body = yaml.safe_dump(_redacted_config(manager.config), allow_unicode=True, sort_keys=False)
        return PlainTextResponse(
            body,
            media_type="application/yaml",
            headers={"Content-Disposition": 'attachment; filename="devices.yaml"'},
        )

    @app.post("/api/v1/config/import")
    async def import_config(request: Request):
        try:
            data = yaml.safe_load((await request.body()).decode("utf-8"))
            candidate = AppConfig.model_validate(data)
            if manager.config is None:
                # Recovery is the only import mode without a current revision.
                candidate = candidate.model_copy(update={"config_revision": 0})
            candidate = _resolve_config_passwords(candidate, manager.config)
            return _result_response(await manager.apply_config(candidate))
        except ValidationError as exc:
            raise HTTPException(422, _safe_validation_errors(exc)) from exc
        except (UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
            # Parsing and sentinel-resolution errors must not echo the imported
            # document because it may contain a plaintext MQTT password.
            raise HTTPException(422, "invalid configuration import") from exc

    @app.get("/api/v1/devices")
    async def list_devices():
        if manager.config is None:
            raise HTTPException(503, manager.config_error or "configuration is unavailable")
        return [item.model_dump(mode="json") for item in manager.config.devices]

    @app.get("/api/v1/mqtt/connections")
    async def list_mqtt_connections():
        if manager.config is None:
            raise HTTPException(503, manager.config_error or "configuration is unavailable")
        return [
            _redact_connection(item.model_dump(mode="json"))
            for item in manager.config.mqtt_connections
        ]

    @app.post("/api/v1/mqtt/connections")
    async def create_mqtt_connection(connection: MqttConnectionConfig):
        resolved = _resolve_connection_password(connection, None)
        return _result_response(await manager.replace_mqtt_connection(None, resolved))

    @app.put("/api/v1/mqtt/connections/{connection_id}")
    async def update_mqtt_connection(
        connection_id: str, connection: MqttConnectionConfig
    ):
        try:
            if manager.config is None:
                raise HTTPException(
                    503, manager.config_error or "configuration is unavailable"
                )
            existing = next(
                (
                    item for item in manager.config.mqtt_connections
                    if item.id == connection_id
                ),
                None,
            )
            if existing is None:
                raise HTTPException(404, "MQTT connection not found")
            connection = _resolve_connection_password(connection, existing)
            return _result_response(
                await manager.replace_mqtt_connection(connection_id, connection)
            )
        except KeyError as exc:
            raise HTTPException(404, "MQTT connection not found") from exc

    @app.delete("/api/v1/mqtt/connections/{connection_id}")
    async def delete_mqtt_connection(connection_id: str):
        try:
            return _result_response(await manager.delete_mqtt_connection(connection_id))
        except KeyError as exc:
            raise HTTPException(404, "MQTT connection not found") from exc

    @app.post("/api/v1/devices")
    async def create_device(device: DeviceConfig):
        return _result_response(await manager.replace_device(None, device))

    @app.get("/api/v1/devices/{device_id}")
    async def get_device(device_id: str):
        if manager.config is None:
            raise HTTPException(503, manager.config_error or "configuration is unavailable")
        device = next((item for item in manager.config.devices if item.id == device_id), None)
        if device is None:
            raise HTTPException(404, "device not found")
        return device.model_dump(mode="json")

    @app.put("/api/v1/devices/{device_id}")
    async def update_device(device_id: str, device: DeviceConfig):
        try:
            return _result_response(await manager.replace_device(device_id, device))
        except KeyError as exc:
            raise HTTPException(404, "device not found") from exc

    @app.delete("/api/v1/devices/{device_id}")
    async def delete_device(device_id: str):
        try:
            return _result_response(await manager.delete_device(device_id))
        except KeyError as exc:
            raise HTTPException(404, "device not found") from exc

    @app.post("/api/v1/devices/{device_id}/actions/{action}")
    async def device_action(device_id: str, action: str):
        if action not in {"start", "stop"}:
            raise HTTPException(404, "unknown action")
        try:
            desired = DesiredState.RUNNING if action == "start" else DesiredState.STOPPED
            return _result_response(await manager.set_desired_state(device_id, desired))
        except KeyError as exc:
            raise HTTPException(404, "device not found") from exc

    @app.get("/api/v1/status")
    async def get_status():
        return manager.status_dict()

    @app.get("/api/v1/devices/{device_id}/status")
    async def get_device_status(device_id: str):
        device_status = manager.device_statuses.get(device_id)
        if device_status is None:
            raise HTTPException(404, "device not found")
        return device_status.model_dump(mode="json")

    return app
