"""Read-only Modbus TCP server backed by PyModbus SimDevice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from pymodbus.constants import ExcCodes
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from ._compat import (
    configured_devices,
    device_id,
    device_protocol_config,
    endpoint,
    protocol_devices,
    sample_field,
    signal_binding,
    signal_id,
    text,
    value,
)
from .base import Adapter
from .modbus_codec import encode_modbus_value, register_count


_TABLE_INDEX = {
    "coil": 0,
    "coils": 0,
    "discrete_input": 1,
    "discrete_inputs": 1,
    "holding_register": 2,
    "holding_registers": 2,
    "input_register": 3,
    "input_registers": 3,
}
_FUNCTION_TABLE = {1: 0, 5: 0, 15: 0, 2: 1, 3: 2, 6: 2, 16: 2, 22: 2, 23: 2, 4: 3}
_WRITE_FUNCTIONS = {5, 6, 15, 16, 22, 23}


@dataclass(slots=True)
class _Binding:
    unit_id: int
    table: int
    address: int
    wire_type: str
    byte_order: str
    word_order: str
    scale: float
    offset: float


class ModbusAdapter(Adapter):
    name = "modbus"

    def __init__(self) -> None:
        super().__init__()
        self._server: ModbusTcpServer | None = None
        self._bindings: dict[tuple[str, str], _Binding] = {}
        self._register_values: dict[tuple[int, int, int], list[int]] = {}
        self._bit_values: dict[tuple[int, int, int], bool] = {}
        self._table_has_data: set[tuple[int, int]] = set()

    async def start(self, config: Any, devices: Iterable[Any] | None = None) -> None:
        if self.running:
            return
        server_config = endpoint(config, "modbus")
        if not bool(value(server_config, "enabled", True)):
            self._status.last_error = None
            self._transition(running=False, connected=False)
            return
        self._bindings.clear()
        self._register_values.clear()
        self._bit_values.clear()
        self._table_has_data.clear()
        sim_devices: list[SimDevice] = []
        for device in protocol_devices(configured_devices(config, devices), "modbus"):
            dev_id = device_id(device)
            unit_id = int(value(device_protocol_config(device), "unit_id"))
            blocks: tuple[list[SimData], list[SimData], list[SimData], list[SimData]] = ([], [], [], [])
            for signal in value(device, "signals", []) or []:
                binding_config = signal_binding(signal)
                table_name = text(value(binding_config, "table")).lower()
                try:
                    table = _TABLE_INDEX[table_name]
                except KeyError as exc:
                    raise ValueError(f"unsupported Modbus table: {table_name}") from exc
                address = int(value(binding_config, "address"))
                wire_type = text(value(binding_config, "wire_type", value(signal, "value_type", "uint16"))).lower()
                if table < 2 and wire_type != "bool":
                    raise ValueError(f"bit table signal {dev_id}/{signal_id(signal)} must use bool wire type")
                count = 1 if table < 2 else register_count(wire_type)
                configured_count = value(binding_config, "register_length")
                if configured_count is not None and int(configured_count) != count:
                    raise ValueError(f"register_length for {dev_id}/{signal_id(signal)} must be {count}")
                blocks[table].append(
                    SimData(
                        address=address,
                        values=False if table < 2 else [0] * count,
                        datatype=DataType.BITS if table < 2 else DataType.REGISTERS,
                        readonly=True,
                    )
                )
                self._bindings[(dev_id, signal_id(signal))] = _Binding(
                    unit_id=unit_id,
                    table=table,
                    address=address,
                    wire_type=wire_type,
                    byte_order=text(value(binding_config, "byte_order", "big")),
                    word_order=text(value(binding_config, "word_order", "big")),
                    scale=float(value(binding_config, "scale", 1.0)),
                    offset=float(value(binding_config, "offset", 0.0)),
                )
                self._table_has_data.add((unit_id, table))
                if table < 2:
                    self._bit_values[(unit_id, table, address)] = False
                else:
                    self._register_values[(unit_id, table, address)] = [0] * count
            for block_index in range(4):
                if not blocks[block_index]:
                    # PyModbus requires bit blocks to contain BITS entries.  A
                    # read-only placeholder is harmless for an otherwise empty
                    # table and keeps the four-block SimDevice shape valid.
                    blocks[block_index].append(
                        SimData(
                            address=0,
                            values=False,
                            datatype=DataType.BITS,
                            readonly=True,
                        )
                        if block_index < 2
                        else SimData(address=0, datatype=DataType.INVALID)
                    )
            sim_devices.append(
                SimDevice(
                    id=unit_id,
                    simdata=blocks,
                    use_bit_addressing=True,
                    action=self._make_action(unit_id),
                )
            )
        server = ModbusTcpServer(
            sim_devices,
            address=(
                text(value(server_config, "host", "0.0.0.0")),
                int(value(server_config, "port", 1502)),
            ),
            ignore_missing_devices=False,
            broadcast_enable=False,
        )
        self._server = server
        try:
            await server.serve_forever(background=True)
            self._status.last_error = None
            self._transition(running=True, connected=True)
        except Exception as exc:
            self._server = None
            self._status.last_error = str(exc)
            try:
                await server.shutdown()
            except Exception:
                pass
            self._transition(running=False, connected=False)
            raise

    def _make_action(self, unit_id: int):
        async def action(
            function_code: int,
            start_address: int,
            _address: int,
            _count: int,
            current_registers: list[int],
            set_values: list[int] | list[bool] | None,
        ) -> ExcCodes | None:
            if set_values is not None or function_code in _WRITE_FUNCTIONS:
                return ExcCodes.ILLEGAL_FUNCTION
            table = _FUNCTION_TABLE.get(function_code)
            if table is None:
                return ExcCodes.ILLEGAL_FUNCTION
            if (unit_id, table) not in self._table_has_data:
                return ExcCodes.ILLEGAL_ADDRESS
            if table < 2:
                first_bit = start_address * 16
                for (mapped_unit, mapped_table, address), bit_value in self._bit_values.items():
                    if mapped_unit != unit_id or mapped_table != table:
                        continue
                    relative = address - first_bit
                    register_index, bit_index = divmod(relative, 16)
                    if 0 <= register_index < len(current_registers):
                        mask = 1 << bit_index
                        if bit_value:
                            current_registers[register_index] |= mask
                        else:
                            current_registers[register_index] &= ~mask
            else:
                for (mapped_unit, mapped_table, address), registers in self._register_values.items():
                    if mapped_unit != unit_id or mapped_table != table:
                        continue
                    relative = address - start_address
                    current_registers[relative : relative + len(registers)] = registers
            return None

        return action

    async def update(self, sample: Any) -> None:
        key = (text(sample_field(sample, "device_id")), text(sample_field(sample, "signal_id")))
        binding = self._bindings.get(key)
        if binding is None:
            return
        sample_value = sample_field(sample, "value")
        if binding.table < 2:
            self._bit_values[(binding.unit_id, binding.table, binding.address)] = bool(sample_value)
            return
        encoded = encode_modbus_value(
            sample_value,
            binding.wire_type,
            byte_order=binding.byte_order,
            word_order=binding.word_order,
            scale=binding.scale,
            offset=binding.offset,
        )
        # Replacing the complete list makes multi-register updates atomic to readers.
        self._register_values[(binding.unit_id, binding.table, binding.address)] = encoded

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            await server.shutdown()
        self._transition(running=False, connected=False)


ModbusTcpAdapter = ModbusAdapter
