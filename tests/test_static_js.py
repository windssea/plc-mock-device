from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


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
