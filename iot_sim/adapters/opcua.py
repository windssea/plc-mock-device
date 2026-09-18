"""Anonymous, read-only OPC UA telemetry server."""

from __future__ import annotations

from typing import Any, Iterable

from asyncua import Server, ua

from ._compat import (
    configured_devices,
    device_id,
    endpoint,
    protocol_devices,
    sample_field,
    signal_binding,
    signal_id,
    text,
    utc_datetime,
    value,
)
from .base import Adapter


_VARIANT_TYPES: dict[str, ua.VariantType] = {
    "bool": ua.VariantType.Boolean,
    "int16": ua.VariantType.Int16,
    "uint16": ua.VariantType.UInt16,
    "int32": ua.VariantType.Int32,
    "uint32": ua.VariantType.UInt32,
    "float32": ua.VariantType.Float,
    "float64": ua.VariantType.Double,
}


def _initial_value(value_type: str) -> bool | int | float:
    if value_type == "bool":
        return False
    if value_type.startswith("float"):
        return 0.0
    return 0


class OPCUAAdapter(Adapter):
    name = "opcua"

    def __init__(self) -> None:
        super().__init__()
        self._server: Server | None = None
        self._nodes: dict[tuple[str, str], Any] = {}
        self._types: dict[tuple[str, str], ua.VariantType] = {}

    async def start(self, config: Any, devices: Iterable[Any] | None = None) -> None:
        if self.running:
            return
        server_config = endpoint(config, "opcua")
        if not bool(value(server_config, "enabled", True)):
            self._status.last_error = None
            self._transition(running=False, connected=False)
            return
        server = Server()
        await server.init()
        host = text(value(server_config, "host", "0.0.0.0"))
        port = int(value(server_config, "port", 4840))
        path = "/" + text(value(server_config, "endpoint_path", "/iot-simulator/")).strip("/") + "/"
        server.set_endpoint(f"opc.tcp://{host}:{port}{path}")
        server.set_server_name(text(value(server_config, "server_name", "PLCnext IoT Simulator")))
        await server.set_application_uri(
            text(value(server_config, "application_uri", "urn:plcnext:iot-simulator"))
        )
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        namespace = await server.register_namespace(
            text(value(server_config, "namespace_uri", "urn:plcnext:iot-simulator:devices"))
        )
        devices_folder = await server.nodes.objects.add_object(
            ua.NodeId("Devices", namespace), ua.QualifiedName("Devices", namespace)
        )
        self._nodes.clear()
        self._types.clear()
        for device in protocol_devices(configured_devices(config, devices), "opcua"):
            dev_id = device_id(device)
            device_node = await devices_folder.add_object(
                ua.NodeId(f"device:{dev_id}", namespace), ua.QualifiedName(dev_id, namespace)
            )
            for signal in value(device, "signals", []) or []:
                sig_id = signal_id(signal)
                binding = signal_binding(signal)
                value_type = text(value(signal, "value_type", "float64")).lower()
                variant_type = _VARIANT_TYPES[value_type]
                node_id = text(value(binding, "node_id", f"{dev_id}.{sig_id}"))
                browse_name = text(value(binding, "browse_name") or sig_id)
                node = await device_node.add_variable(
                    ua.NodeId(node_id, namespace),
                    ua.QualifiedName(browse_name, namespace),
                    _initial_value(value_type),
                    variant_type,
                )
                display_name = text(
                    value(binding, "display_name") or value(signal, "name") or browse_name
                )
                await node.write_attribute(
                    ua.AttributeIds.DisplayName,
                    ua.DataValue(ua.Variant(ua.LocalizedText(display_name), ua.VariantType.LocalizedText)),
                )
                unit = text(value(signal, "unit"))
                if unit:
                    await node.add_property(
                        ua.NodeId(f"{node_id}.Unit", namespace),
                        ua.QualifiedName("Unit", namespace),
                        unit,
                        ua.VariantType.String,
                    )
                await node.set_read_only()
                self._nodes[(dev_id, sig_id)] = node
                self._types[(dev_id, sig_id)] = variant_type
        self._server = server
        try:
            await server.start()
            self._status.last_error = None
            self._transition(running=True, connected=True)
        except Exception as exc:
            self._status.last_error = str(exc)
            self._server = None
            try:
                await server.stop()
            except Exception:
                pass
            self._transition(running=False, connected=False)
            raise

    async def update(self, sample: Any) -> None:
        key = (text(sample_field(sample, "device_id")), text(sample_field(sample, "signal_id")))
        node = self._nodes.get(key)
        if node is None:
            return
        timestamp = utc_datetime(sample_field(sample, "timestamp_utc"))
        data_value = ua.DataValue(
            ua.Variant(sample_field(sample, "value"), self._types[key]),
            ua.StatusCode(ua.StatusCodes.Good),
            SourceTimestamp=timestamp,
            ServerTimestamp=timestamp,
        )
        try:
            await node.write_value(data_value)
        except Exception as exc:
            self._status.last_error = str(exc)
            raise

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            await server.stop()
        self._nodes.clear()
        self._types.clear()
        self._transition(running=False, connected=False)


OpcUaAdapter = OPCUAAdapter
