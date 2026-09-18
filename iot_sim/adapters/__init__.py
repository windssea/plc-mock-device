"""Protocol adapters exposed to the simulator runtime."""

from .base import Adapter, AdapterStatus
from .modbus import ModbusAdapter, ModbusTcpAdapter
from .modbus_codec import decode_modbus_value, encode_modbus_value, register_count
from .mqtt import MQTTAdapter, MqttAdapter
from .opcua import OPCUAAdapter, OpcUaAdapter

__all__ = [
    "Adapter",
    "AdapterStatus",
    "MQTTAdapter",
    "MqttAdapter",
    "ModbusAdapter",
    "ModbusTcpAdapter",
    "OPCUAAdapter",
    "OpcUaAdapter",
    "decode_modbus_value",
    "encode_modbus_value",
    "register_count",
]

