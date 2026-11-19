'use strict';

const assert = require('node:assert/strict');
const {
  buildFromTypeRequest,
  deviceTypeResultMessage,
  draftFromType,
  errorsFromApi,
  modbusAddressLines,
  suggestionForDeviceId,
  syncDraftForDeviceId,
  validateFromTypeRequest,
} = require('../iot_sim/static/device-type-form.js');

function temperatureType() {
  return {
    id: 'mqtt.temperature',
    name: '温度传感器',
    protocol: 'mqtt',
    id_prefix: 'mqtt-temp',
    topic_pattern: 'iot-simulator/test/{device_id}/telemetry',
    description: 'MQTT 温度与湿度',
    signals: [{id: 'temperature'}, {id: 'humidity'}],
    defaults: {
      connection_id: 'default', qos: 1, retain: false, payload_mode: 'nested', publish_interval_ms: 1000,
    },
    suggestion: {
      device_id: 'mqtt-temp-002',
      name: '温度传感器 002',
      topic: 'iot-simulator/test/mqtt-temp-002/telemetry',
      allocatable: true,
      error: null,
    },
  };
}

function counterType() {
  return {
    id: 'mqtt.counter',
    name: 'MQTT 计数设备',
    protocol: 'mqtt',
    id_prefix: 'mqtt',
    topic_pattern: 'devices/{device_id}/telemetry',
    signals: [{id: 'counter'}],
    defaults: {
      connection_id: 'default', qos: 0, retain: false, payload_mode: 'nested', publish_interval_ms: null,
    },
    suggestion: {
      device_id: 'mqtt-001',
      name: 'MQTT 计数设备 001',
      topic: 'devices/mqtt-001/telemetry',
    },
  };
}

const temperature = temperatureType();
let draft = draftFromType(temperature);
assert.equal(draft.deviceId, 'mqtt-temp-002');
assert.equal(draft.name, '温度传感器 002');
assert.equal(draft.topic, 'iot-simulator/test/mqtt-temp-002/telemetry');
assert.equal(draft.nameDirty, false);
assert.equal(draft.topicDirty, false);
assert.equal(draft.desiredState, 'running');
assert.equal(draft.enabled, true);

draft = syncDraftForDeviceId(draft, 'mqtt-temp-009');
assert.equal(draft.name, '温度传感器 009');
assert.equal(draft.topic, 'iot-simulator/test/mqtt-temp-009/telemetry');
assert.equal(suggestionForDeviceId(temperature, 'mqtt-temp-009').topic, draft.topic);
const omitted = buildFromTypeRequest(draft);
assert.equal(omitted.device_id, 'mqtt-temp-009');
assert.equal(Object.hasOwn(omitted, 'name'), false);
assert.equal(Object.hasOwn(omitted, 'topic'), false);
assert.equal(omitted.connection_id, 'default');
assert.equal(omitted.qos, 1);
assert.equal(omitted.retain, false);
assert.equal(omitted.publish_interval_ms, 1000);
assert.equal(omitted.desired_state, 'running');
assert.equal(JSON.stringify(omitted).includes('password'), false);

const stale = buildFromTypeRequest({
  ...draftFromType(temperature),
  deviceId: 'mqtt-temp-009',
  name: '温度传感器 002',
  nameDirty: false,
  topic: 'iot-simulator/test/mqtt-temp-002/telemetry',
  topicDirty: false,
});
assert.equal(stale.device_id, 'mqtt-temp-009');
assert.equal(Object.hasOwn(stale, 'name'), false);
assert.equal(Object.hasOwn(stale, 'topic'), false);

draft.topic = 'custom/topic';
draft.topicDirty = true;
assert.equal(buildFromTypeRequest(draft).topic, 'custom/topic');

const sameAsPattern = buildFromTypeRequest({
  ...draft,
  topic: 'iot-simulator/test/mqtt-temp-009/telemetry',
  topicDirty: true,
  name: '温度传感器 009',
  nameDirty: true,
});
assert.equal(Object.hasOwn(sameAsPattern, 'topic'), false);
assert.equal(Object.hasOwn(sameAsPattern, 'name'), false);

const cleared = syncDraftForDeviceId(draftFromType(temperature), '');
assert.equal(cleared.deviceId, '');
assert.equal(cleared.name, '');
assert.equal(cleared.topic, '');
const clearedRequest = buildFromTypeRequest(cleared);
assert.equal(Object.hasOwn(clearedRequest, 'device_id'), false);
assert.equal(Object.hasOwn(clearedRequest, 'name'), false);
assert.equal(Object.hasOwn(clearedRequest, 'topic'), false);
const kept = buildFromTypeRequest({
  ...cleared,
  name: '操作员名称',
  nameDirty: true,
  topic: 'kept/topic',
  topicDirty: true,
});
assert.equal(Object.hasOwn(kept, 'device_id'), false);
assert.equal(kept.name, '操作员名称');
assert.equal(kept.topic, 'kept/topic');

const counter = counterType();
assert.equal(suggestionForDeviceId(counter, 'mqtt-1').name, 'MQTT 计数设备 mqtt-1');
assert.equal(suggestionForDeviceId(counter, 'mqtt-1000').name, 'MQTT 计数设备 1000');
assert.equal(suggestionForDeviceId(counter, 'mqtt-001').topic, 'devices/mqtt-001/telemetry');
const counterRequest = buildFromTypeRequest(draftFromType(counter));
assert.equal(counterRequest.qos, 0);
assert.equal(Object.hasOwn(counterRequest, 'publish_interval_ms'), false);
assert.equal(Object.hasOwn(counterRequest, 'topic'), false);

const power = buildFromTypeRequest(draftFromType({
  id: 'mqtt.power_meter',
  name: '电能表',
  protocol: 'mqtt',
  id_prefix: 'mqtt-power',
  topic_pattern: 'iot-simulator/test/{device_id}/telemetry',
  defaults: {connection_id: 'default', qos: 1, retain: false, payload_mode: 'flat', publish_interval_ms: 2000},
  suggestion: {device_id: 'mqtt-power-001', name: '电能表 001', topic: 'iot-simulator/test/mqtt-power-001/telemetry'},
}));
assert.equal(power.publish_interval_ms, 2000);
assert.equal(power.payload_mode, 'flat');

const stopped = buildFromTypeRequest({...draftFromType(temperature), enabled: false, desiredState: 'stopped'});
assert.equal(stopped.enabled, false);
assert.equal(stopped.desired_state, 'stopped');

const opcua = {
  id: 'opcua.generic',
  name: 'OPC UA 示例设备',
  protocol: 'opcua',
  id_prefix: 'opcua',
  topic_pattern: null,
  signals: [{id: 'temperature'}],
  defaults: {},
  suggestion: {device_id: 'opcua-001', name: 'OPC UA 示例设备 001', node_ids: {temperature: 'opcua-001.temperature'}},
};
assert.deepEqual(suggestionForDeviceId(opcua, 'opcua-009').node_ids, {temperature: 'opcua-009.temperature'});
let opcuaDraft = syncDraftForDeviceId(draftFromType(opcua), 'opcua-009');
opcuaDraft.nodeIds = {temperature: 'Custom.Node'};
const moved = syncDraftForDeviceId(opcuaDraft, 'opcua-010');
assert.equal(moved.nodeIds.temperature, 'Custom.Node');
assert.equal(moved.name, 'OPC UA 示例设备 010');
assert.deepEqual(buildFromTypeRequest(moved).node_ids, {temperature: 'Custom.Node'});
assert.equal(Object.hasOwn(buildFromTypeRequest(moved), 'topic'), false);
assert.equal(Object.hasOwn(buildFromTypeRequest({...moved, nodeIds: {temperature: '  '}}), 'node_ids'), false);

const modbus = {
  id: 'modbus.pressure',
  name: 'Modbus 压力设备',
  protocol: 'modbus',
  id_prefix: 'modbus',
  signals: [{id: 'pressure'}],
  defaults: {},
  suggestion: {device_id: 'modbus-002', name: 'Modbus 压力设备 002', unit_id: 2, addresses: {pressure: 0}},
};
const modbusDraft = draftFromType(modbus);
assert.equal(modbusDraft.unitId, 2);
assert.equal(modbusDraft.addressBase, 0);
const modbusRequest = buildFromTypeRequest(modbusDraft);
assert.equal(modbusRequest.unit_id, 2);
assert.equal(modbusRequest.address_base, 0);
assert.equal(Object.hasOwn(modbusRequest, 'topic'), false);
assert.equal(Object.hasOwn(modbusRequest, 'connection_id'), false);
assert.equal(Object.hasOwn(buildFromTypeRequest({...modbusDraft, unitId: '', addressBase: ''}), 'unit_id'), false);
assert.deepEqual(modbusAddressLines(modbus, 10), [
  'pressure，表 input_register，wire float32，宽度 2，PDU 10',
]);

const invalid = validateFromTypeRequest({
  ...modbusDraft,
  deviceId: 'bad id',
  unitId: 0,
  addressBase: 70000,
});
assert.deepEqual(invalid.items.map(item => item.field), ['device_id', 'unit_id', 'address_base']);
assert.equal(invalid.title, '请修正以下 3 项后再创建：');
assert.equal(validateFromTypeRequest({...draftFromType(temperature), topic: 'a/+/b'}).items[0].field, 'topic');
assert.equal(validateFromTypeRequest({...draftFromType(temperature), topic: 'a/#/b'}).items[0].field, 'topic');
assert.equal(validateFromTypeRequest({...draftFromType(temperature), publishIntervalMs: 999}).items[0].field, 'publish_interval_ms');
assert.equal(validateFromTypeRequest({...draftFromType(temperature), publishIntervalMs: ''}).items.length, 0);
assert.equal(validateFromTypeRequest({...draftFromType(temperature), publishIntervalMs: 1000}).items.length, 0);
assert.equal(validateFromTypeRequest({...modbusDraft, deviceId: ''}).items.length, 0);

const listed = errorsFromApi({detail: [
  {loc: [], msg: 'device already exists: mqtt-1', type: 'value_error', input: {password: 'secret'}},
  {loc: [], msg: 'Value error, duplicate Modbus unit_id', type: 'value_error'},
  {loc: ['topic'], msg: 'Value error, MQTT publish topic cannot contain wildcards or NUL', type: 'value_error'},
  {loc: [], msg: 'MQTT topic already in use: x', type: 'value_error'},
  {loc: [], msg: 'OPC UA node_id Devices collides with a runtime node', type: 'value_error'},
  {loc: [], msg: 'unknown connection_id', type: 'value_error'},
  {loc: [], msg: 'device id space exhausted for prefix mqtt', type: 'value_error'},
  {loc: [], msg: 'address_base and addresses are mutually exclusive', type: 'value_error'},
  {loc: ['body', 'extra'], msg: 'Value error, extra field', type: 'value_error'},
]});
assert.deepEqual(listed.items.map(item => item.field), [
  'device_id', 'unit_id', 'topic', 'topic', 'node_ids', 'connection_id', 'device_id', 'address_base', null,
]);
assert.equal(listed.items[1].message, 'duplicate Modbus unit_id');
assert.equal(listed.items[2].message, 'MQTT publish topic cannot contain wildcards or NUL');
assert.equal(listed.title, '请修正以下 9 项后再创建：');
assert.equal(listed.text.startsWith(listed.title), true);
assert.equal(JSON.stringify(listed).includes('secret'), false);

const conflict = errorsFromApi({detail: 'revision conflict: expected 1, current 2'});
assert.equal(conflict.items.length, 1);
assert.equal(conflict.items[0].field, null);
assert.equal(conflict.items[0].message, 'revision conflict: expected 1, current 2\n请重新打开对话框以刷新建议');

const rollback = errorsFromApi({detail: 'configuration was rolled back: revision conflict: expected 3, current 4'});
assert.equal(rollback.items[0].field, null);
assert.equal(rollback.items[0].message, 'configuration was rolled back: revision conflict: expected 3, current 4');
assert.equal(rollback.items[0].message.includes('请重新打开对话框以刷新建议'), false);

const stringDetail = errorsFromApi({detail: 'unit_id conflict as a string'});
assert.equal(stringDetail.items[0].field, null);
assert.equal(stringDetail.title, '请修正以下 1 项后再创建：');

assert.equal(
  deviceTypeResultMessage('mqtt-temp-002', {restarted_adapters: ['mqtt'], restart_required: []}),
  '已从类型创建 mqtt-temp-002，已重启适配器：mqtt。',
);
assert.equal(
  deviceTypeResultMessage('mqtt-temp-002', {restarted_adapters: [], restart_required: []}),
  '已从类型创建 mqtt-temp-002',
);
assert.equal(
  deviceTypeResultMessage('modbus-001', {restarted_adapters: ['modbus'], restart_required: ['opcua']}),
  '已从类型创建 modbus-001，已重启适配器：modbus。仍需重启后生效：opcua。',
);
assert.equal(deviceTypeResultMessage('mqtt-temp-002', {}).includes('无需重启'), false);
