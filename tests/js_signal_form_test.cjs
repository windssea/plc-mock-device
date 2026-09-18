'use strict';

const assert = require('node:assert/strict');
const {
  collectSignal,
  defaultProtocolConfig,
  formatApiError,
  generatorPolicy,
  modbusBindingForValueType,
  protocolDraft,
  validatedSubmission,
  validateDevice,
  validateSignal,
} = require('../iot_sim/static/signal-form.js');

function element(group, key, value, type = 'text') {
  return {dataset: {[group]: key}, value: String(value), type};
}

function nodeFor(generatorType, generatorFields, bindingFields) {
  const groups = {
    '[data-key]': [
      element('key', 'id', 'point-1'),
      element('key', 'name', 'Point 1'),
      element('key', 'value_type', generatorType === 'increment' ? 'uint32' : 'float32'),
      element('key', 'generator.type', generatorType),
    ],
    '[data-gen]': Object.entries(generatorFields).map(([key, value]) =>
      element('gen', key, value, typeof value === 'number' ? 'number' : 'text')
    ),
    '[data-bind]': Object.entries(bindingFields).map(([key, value]) =>
      element('bind', key, value, typeof value === 'number' ? 'number' : 'text')
    ),
  };
  return {querySelectorAll: selector => groups[selector] || []};
}

const random = collectSignal(
  nodeFor('random_range', {min: 1, max: 10, precision: 2}, {field_name: 'temperature'}),
  'mqtt'
);
assert.deepEqual(random.generator, {type: 'random_range', min: 1, max: 10, precision: 2});
assert.deepEqual(random.binding, {type: 'mqtt', field_name: 'temperature'});

const increment = collectSignal(
  nodeFor(
    'increment',
    {initial_value: 0, value_min: 0, value_max: 100, step_min: 1, step_max: 2, boundary: 'wrap'},
    {node_id: 'device.counter'}
  ),
  'opcua'
);
assert.equal(increment.generator.type, 'increment');
assert.equal(increment.generator.boundary, 'wrap');
assert.deepEqual(increment.binding, {type: 'opcua', node_id: 'device.counter'});

const editedModbus = collectSignal(
  nodeFor('random_range', {min: 5, max: 8, precision: 1}, {table: 'input_register', address: 4, wire_type: 'float32'}),
  'modbus'
);
assert.equal(editedModbus.generator.type, 'random_range');
assert.equal(editedModbus.binding.type, 'modbus');
assert.equal(editedModbus.binding.address, 4);

assert.deepEqual(generatorPolicy('bool', 'increment'), {
  type: 'random_range',
  locked: true,
  precision: 0,
  message: '布尔类型只能使用区间随机，并固定在 0（假）到 1（真）之间。',
});
assert.equal(generatorPolicy('int16', 'random_range').precision, 0);
assert.equal(generatorPolicy('float32', 'increment').locked, false);
assert.deepEqual(defaultProtocolConfig('mqtt', 'device-9'), {
  connection_id: 'default', topic: 'devices/device-9/telemetry', qos: 0, retain: false,
});
assert.deepEqual(defaultProtocolConfig('modbus'), {unit_id: 1});
const changedProtocol = protocolDraft(
  {mqtt: '{"topic":"original"}'}, 'mqtt', '{"topic":"edited"}', 'modbus', 'device-9',
);
assert.equal(changedProtocol.text, '{\n  "unit_id": 1\n}');
const restoredProtocol = protocolDraft(
  changedProtocol.drafts, 'modbus', changedProtocol.text, 'mqtt', 'device-9',
);
assert.equal(restoredProtocol.text, '{"topic":"edited"}', 'switching back must restore the edited draft');
assert.deepEqual(modbusBindingForValueType('float32'), {
  type: 'modbus', table: 'input_register', wire_type: 'float32',
});
assert.deepEqual(modbusBindingForValueType('bool', {table: 'input_register', wire_type: 'float32'}), {
  type: 'modbus', table: 'coil', wire_type: 'bool',
});

function baseSignal(overrides = {}) {
  return {
    id: 'point-1',
    name: 'Point 1',
    value_type: 'float32',
    generator: {type: 'random_range', min: 0, max: 10, precision: 2},
    binding: {type: 'mqtt', field_name: 'point'},
    ...overrides,
  };
}

assert.deepEqual(validateSignal(baseSignal()), []);
assert.deepEqual(validateSignal(baseSignal({
  value_type: 'bool',
  generator: {type: 'random_range', min: 0, max: 1, precision: 0},
})), []);

const boolIncrement = validateSignal(baseSignal({
  value_type: 'bool',
  generator: {
    type: 'increment', initial_value: 0, value_min: 0, value_max: 1,
    step_min: 1, step_max: 1, boundary: 'wrap',
  },
}));
assert.ok(boolIncrement.some(error => error.path === 'generator.type' && error.message.includes('布尔类型不能使用递增')));

const invalidBoolRange = validateSignal(baseSignal({
  value_type: 'bool',
  generator: {type: 'random_range', min: 0.5, max: 2, precision: 1},
}));
assert.ok(invalidBoolRange.some(error => error.path === 'generator.min' && error.message.includes('只能是 0 或 1')));
assert.ok(!invalidBoolRange.some(error => error.path === 'generator.precision'), 'bool precision 1 is accepted by the backend');

assert.deepEqual(validateSignal(baseSignal({
  value_type: 'int16',
  generator: {type: 'random_range', min: 0, max: 10, precision: 2},
})), [], 'integer random precision 0..15 is accepted by Pydantic');

const decimalInteger = validateSignal(baseSignal({
  value_type: 'int16',
  generator: {
    type: 'increment', initial_value: 1.5, value_min: 0, value_max: 10,
    step_min: -1.5, step_max: -0.5, boundary: 'wrap',
  },
}));
assert.ok(decimalInteger.some(error => error.path === 'generator.initial_value' && error.message.includes('不能自动舍入')));
assert.ok(decimalInteger.some(error => error.path === 'generator.step_min' && error.message.includes('不能自动舍入')));

const negativeSteps = validateSignal(baseSignal({
  value_type: 'int16',
  generator: {
    type: 'increment', initial_value: 5, value_min: 0, value_max: 10,
    step_min: -3, step_max: -1, boundary: 'wrap',
  },
}));
assert.deepEqual(negativeSteps, []);

const invalidIncrement = validateSignal(baseSignal({
  generator: {
    type: 'increment', initial_value: 11, value_min: 0, value_max: 10,
    step_min: 2, step_max: 1, boundary: 'clamp',
  },
}));
assert.ok(invalidIncrement.some(error => error.path === 'generator.initial_value'));
assert.ok(invalidIncrement.some(error => error.path === 'generator.step_max'));

const outOfRange = validateSignal(baseSignal({
  value_type: 'float32',
  generator: {type: 'random_range', min: 0, max: 4e38, precision: 2},
}));
assert.ok(outOfRange.some(error => error.path === 'generator.max' && error.message.includes('超出 float32')));

const modbusAddressOverflow = validateSignal(baseSignal({
  value_type: 'float64',
  binding: {
    type: 'modbus', table: 'input_register', address: 65535, wire_type: 'float64',
    scale: 1, offset: 0, byte_order: 'big', word_order: 'big',
  },
}));
assert.ok(modbusAddressOverflow.some(error => error.path === 'binding.address' && error.message.includes('4 个寄存器')));

const modbusWireOverflow = validateSignal(baseSignal({
  value_type: 'float32',
  generator: {type: 'random_range', min: 0, max: 40000, precision: 2},
  binding: {
    type: 'modbus', table: 'input_register', address: 0, wire_type: 'int16',
    scale: 1, offset: 0, byte_order: 'big', word_order: 'big',
  },
}));
assert.ok(modbusWireOverflow.some(error => error.path === 'binding.wire_type' && error.message.includes('scale/offset')));

const duplicateDevice = validateDevice({
  id: 'device 1', name: '', default_interval_ms: 999, protocol: 'mqtt',
  protocol_config: {topic: 'devices/test'},
  signals: [baseSignal(), baseSignal()],
});
assert.ok(duplicateDevice.some(error => error.scope === 'device' && error.path === 'id'));
assert.ok(duplicateDevice.some(error => error.scope === 'signal' && error.path === 'id' && error.message.includes('重复')));

const mqttSignal2 = baseSignal({id: 'point-2', binding: {type: 'mqtt', field_name: 'point'}});
const duplicateMqttFields = validateDevice({
  id: 'mqtt-device', name: 'MQTT', default_interval_ms: 1000, protocol: 'mqtt',
  protocol_config: {topic: 'devices/test'}, signals: [baseSignal(), mqttSignal2],
});
assert.ok(duplicateMqttFields.some(error => error.path === 'binding.field_name' && error.message.includes('重复')));

assert.deepEqual(validateDevice({
  id: 'mqtt-profile-device', name: 'MQTT profile', default_interval_ms: 1000, protocol: 'mqtt',
  protocol_config: {connection_id: 'factory-b', topic: 'devices/test'}, signals: [],
}), []);
assert.ok(validateDevice({
  id: 'mqtt-bad-profile', name: 'MQTT profile', default_interval_ms: 1000, protocol: 'mqtt',
  protocol_config: {connection_id: 'bad connection', topic: 'devices/test'}, signals: [],
}).some(error => error.path === 'protocol_config' && error.message.includes('connection_id')));

for (const [label, device] of [
  ['invalid qos', {id: 'mqtt-qos', name: 'MQTT', default_interval_ms: 1000, protocol: 'mqtt', protocol_config: {topic: 'devices/test', qos: 3}, signals: []}],
  ['extra protocol key', {id: 'mqtt-extra', name: 'MQTT', default_interval_ms: 1000, protocol: 'mqtt', protocol_config: {topic: 'devices/test', unknown: true}, signals: []}],
  ['long device id', {id: 'a'.repeat(129), name: 'Long', default_interval_ms: 1000, protocol: 'opcua', protocol_config: {}, signals: []}],
]) {
  let calls = 0;
  const result = validatedSubmission(device, [], () => { calls += 1; });
  assert.equal(result.submitted, false, label);
  assert.equal(calls, 0, `${label} must not call API`);
}

for (const [label, signal] of [
  ['Modbus address overflow', baseSignal({value_type: 'float64', binding: {type: 'modbus', table: 'input_register', address: 65535, wire_type: 'float64', scale: 1, offset: 0}})],
  ['Modbus wire overflow', baseSignal({value_type: 'float32', generator: {type: 'random_range', min: 0, max: 40000, precision: 2}, binding: {type: 'modbus', table: 'input_register', address: 0, wire_type: 'int16', scale: 1, offset: 0}})],
]) {
  let calls = 0;
  const result = validatedSubmission({
    id: 'modbus-device', name: 'Modbus', default_interval_ms: 1000, protocol: 'modbus',
    protocol_config: {unit_id: 1}, signals: [signal],
  }, [], () => { calls += 1; });
  assert.equal(result.submitted, false, label);
  assert.equal(calls, 0, `${label} must not call API`);
}

const readable = formatApiError({detail: [{
  loc: ['body', 'signals', 0, 'generator', 'type'],
  msg: 'Field required',
}]});
assert.equal(readable, '数据点 1 → 生成方式：不能为空');

assert.equal(formatApiError({detail: [{
  loc: ['body', 'protocol_config', 'mqtt', 'qos'], msg: 'Input should be 0, 1 or 2',
}]}), 'MQTT QoS：只能是 0、1 或 2');
assert.equal(formatApiError({detail: 'Value error, duplicate MQTT json_field in device mqtt-1'}), '设备内 MQTT JSON 字段名不能重复');

let requests = 0;
const blocked = validatedSubmission(
  {id: '', name: '', default_interval_ms: 999, protocol: 'mqtt', protocol_config: {}, signals: []},
  [],
  () => { requests += 1; },
);
assert.equal(blocked.submitted, false);
assert.equal(requests, 0, 'invalid submit must not call the API callback');

const allowed = validatedSubmission(
  {id: 'device-1', name: 'Device 1', default_interval_ms: 1000, protocol: 'mqtt', protocol_config: {topic: 'devices/device-1'}, signals: [baseSignal()]},
  [],
  () => { requests += 1; return 'sent'; },
);
assert.equal(allowed.submitted, true);
assert.equal(allowed.result, 'sent');
assert.equal(requests, 1);
