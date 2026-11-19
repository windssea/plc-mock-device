from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


def _function_body(source: str, name: str) -> str:
    marker = f"async function {name}"
    start = source.index(marker)
    open_brace = source.index("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"{name} is not closed")


def test_signal_form_serialization_with_node() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [node, str(root / "tests" / "js_signal_form_test.cjs")],
        cwd=root,
        check=True,
    )


def test_html_id_patterns_compile_with_regexp_v_flag() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [node, str(root / "tests" / "html_pattern_test.cjs")],
        cwd=root,
        check=True,
    )


def test_device_type_form_with_node() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [node, str(root / "tests" / "js_device_type_form_test.cjs")],
        cwd=root,
        check=True,
    )


def test_device_type_dialog_is_wired_without_taking_the_device_id_field() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "iot_sim" / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (root / "iot_sim" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="newDeviceFromType"' in html
    assert 'id="deviceTypeDialog"' in html
    assert 'id="deviceTypeErrors"' in html
    assert 'id="deviceTypeForm" method="dialog" novalidate' in html
    assert html.index("/static/device-type-form.js") < html.index("/static/app.js")
    assert html.index('id="deviceForm"') < html.index('name="id"') < html.index('id="deviceTypeDialog"')
    dialog = html.split('id="deviceTypeDialog"', 1)[1].split("</dialog>", 1)[0]
    assert 'name="id"' not in dialog
    for field in (
        "type_id", "device_id", "name", "topic", "connection_id", "unit_id",
        "address_base", "node_ids",
    ):
        assert f'data-type-field="{field}"' in dialog
    assert "零基 PDU，不要填 40001/30001。" in dialog
    assert "监听地址、端口和 Broker 在「协议与配置」修改" in dialog
    assert 'data-device-field="unit_id"' in html
    assert 'data-device-field="topic"' in html
    assert "fetch('/api/v1/device-types'" in app_js
    assert "fetch('/api/v1/devices/from-type'" in app_js
    assert "showDeviceTypeErrors" in app_js
    assert "DeviceTypeForm.errorsFromApi" in app_js
    assert "DeviceTypeForm.deviceTypeResultMessage" in app_js
    assert "structuredClone" in app_js
    body = _function_body(app_js, "submitDeviceFromType")
    assert "showValidationErrors" not in body
    assert "applyResultMessage" not in body
    assert "#formErrors" not in body
    assert re.search(r"\bapi\(", body) is None
    assert "fetch('/api/v1/devices/from-type'" in body
    assert "loadConfig()" in body
    assert "loadStatus()" in body


def test_protocol_settings_form_with_node() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [node, str(root / "tests" / "js_protocol_form_test.cjs")],
        cwd=root,
        check=True,
    )


def test_toast_is_an_accessible_live_region() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "iot_sim" / "static" / "index.html").read_text(encoding="utf-8")
    assert '<div id="toast" role="status" aria-live="polite" aria-atomic="true">' in html
    app_js = (root / "iot_sim" / "static" / "app.js").read_text(encoding="utf-8")
    assert "element.setAttribute('role', error ? 'alert' : 'status')" in app_js
    assert "element.setAttribute('aria-live', error ? 'assertive' : 'polite')" in app_js


def test_mqtt_connection_ui_uses_dedicated_sqlite_crud_api() -> None:
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "iot_sim" / "static" / "app.js").read_text(encoding="utf-8")
    assert "'/mqtt/connections'" in app_js
    assert "`/mqtt/connections/${encodeURIComponent(originalId)}`" in app_js
    assert "`/mqtt/connections/${encodeURIComponent(id)}`" in app_js
    assert "ProtocolForm.hasStoredMqttPassword(connection.password)" in app_js
    assert "field.value = field.dataset.connectionField === 'password' ? '' : value" in app_js
    assert "data-connection-control=\"clear_password\"" in (
        root / "iot_sim" / "static" / "index.html"
    ).read_text(encoding="utf-8")
