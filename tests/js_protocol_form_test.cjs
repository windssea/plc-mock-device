'use strict';

const assert = require('node:assert/strict');
const {
  applyResultMessage,
  buildEndpoints,
  buildMqttConnection,
  endpointValues,
  hasStoredMqttPassword,
  mergeMqttConnections,
  mqttConnections,
  mqttConnectionValues,
  mqttPasswordSubmission,
  REDACTED_PASSWORD,
  rebaseConfig,
  rebaseMqttConnections,
  validatedSubmission,
  validateEndpoints,
  validateMqttConnection,
} = require('../iot_sim/static/protocol-form.js');

const config = {
  version: 1,
  config_revision: 4,
  random_seed: 1,
  web: {host: '127.0.0.1', port: 8080},
  opcua: {
    enabled: true, host: '0.0.0.0', port: 4840, endpoint_path: '/iot-simulator/',
    server_name: 'IoT Simulator', application_uri: 'urn:test:server', namespace_uri: 'urn:test:devices',
  },
  mqtt: {
    enabled: true, host: 'broker', port: 1883, protocol_version: '5.0',
    client_id: 'iot-simulator', keepalive: 60, username: null, password_env: 'MQTT_PASSWORD',
    reconnect_min_seconds: 1, reconnect_max_seconds: 30,
    max_inflight_messages: 20, max_queued_messages: 1000,
  },
  modbus: {enabled: true, host: '0.0.0.0', port: 1502},
  mqtt_connections: [
    {
      id: 'default', name: '默认连接', enabled: true, host: 'broker', port: 1883,
      transport: 'tcp', protocol_version: '5.0', client_id: 'iot-simulator',
      keepalive: 60, username: null, password: null, password_env: 'MQTT_PASSWORD',
      tls_enabled: false, reconnect_min_seconds: 1, reconnect_max_seconds: 30,
      max_inflight_messages: 20, max_queued_messages: 1000,
    },
    {
      id: 'factory-b', name: 'B 工厂', enabled: true, host: 'broker-b', port: 8883,
      transport: 'tcp', protocol_version: '3.1.1', client_id: 'factory-b-gateway',
      keepalive: 45, username: 'mqtt-b', password: '********', password_env: null,
      tls_enabled: true, reconnect_min_seconds: 2, reconnect_max_seconds: 60,
      max_inflight_messages: 10, max_queued_messages: 200,
    },
  ],
  devices: [{id: 'device-1'}],
};

const values = endpointValues(config);
assert.equal(values['mqtt.username'], '');
assert.equal(values['mqtt.password_env'], 'MQTT_PASSWORD');
assert.equal(values['opcua.port'], 4840);
assert.deepEqual(buildEndpoints(values), {
  opcua: config.opcua,
  mqtt: config.mqtt,
  modbus: config.modbus,
});
assert.deepEqual(validateEndpoints(buildEndpoints(values)), []);
assert.equal(mqttConnections(config).length, 2);
assert.equal(mqttConnections(config)[0].id, 'default');

const factoryBValues = mqttConnectionValues(mqttConnections(config)[1]);
const factoryB = buildMqttConnection(factoryBValues);
assert.equal(REDACTED_PASSWORD, '********');
assert.equal(factoryB.password, REDACTED_PASSWORD);
assert.equal(hasStoredMqttPassword(factoryB.password), true);
assert.equal(hasStoredMqttPassword('real-secret'), false);
assert.equal(mqttPasswordSubmission('', true, false), REDACTED_PASSWORD);
assert.equal(mqttPasswordSubmission('replacement', true, false), 'replacement');
assert.equal(mqttPasswordSubmission('', true, true), null);
assert.equal(mqttPasswordSubmission('', false, false), null);
assert.equal(factoryB.tls_enabled, true);
assert.deepEqual(validateMqttConnection(factoryB, mqttConnections(config), 'factory-b'), []);
const invalidConnection = buildMqttConnection({
  ...factoryBValues,
  id: 'bad id',
  transport: 'websockets',
  password_env: 'MQTT_B_PASSWORD',
  reconnect_min_seconds: '70',
  reconnect_max_seconds: '60',
});
const connectionErrors = validateMqttConnection(invalidConnection, mqttConnections(config));
assert.ok(connectionErrors.some(error => error.path === 'id'));
assert.ok(connectionErrors.some(error => error.path === 'transport'));
assert.ok(connectionErrors.some(error => error.path === 'password'));
assert.ok(connectionErrors.some(error => error.path === 'password_env'));
assert.ok(connectionErrors.some(error => error.path === 'reconnect_max_seconds'));

const renamedConnection = {...factoryB, id: 'factory-c'};
assert.ok(validateMqttConnection(
  renamedConnection, mqttConnections(config), 'factory-b',
).some(error => error.path === 'id' && error.message.includes('不能修改')));

const mergedConnections = mergeMqttConnections(config, [
  {...mqttConnections(config)[0], host: 'localhost', username: 'mqtt', password: 'db-secret', password_env: null},
  factoryB,
]);
assert.equal(mergedConnections.mqtt.host, 'localhost');
assert.equal(mergedConnections.mqtt.password, 'db-secret');
assert.equal(mergedConnections.mqtt_connections[1].host, 'broker-b');

const invalidValues = {...values,
  'opcua.port': '70000',
  'mqtt.reconnect_min_seconds': '20',
  'mqtt.reconnect_max_seconds': '10',
  'mqtt.password_env': 'real password',
};
const invalid = validateEndpoints(buildEndpoints(invalidValues));
assert.ok(invalid.some(error => error.path === 'opcua.port'));
assert.ok(invalid.some(error => error.path === 'mqtt.reconnect_max_seconds'));
assert.ok(invalid.some(error => error.path === 'mqtt.password_env'));

let sends = 0;
const blocked = validatedSubmission(config, invalidValues, () => { sends += 1; });
assert.equal(blocked.submitted, false);
assert.equal(sends, 0);
const allowed = validatedSubmission(config, values, candidate => {
  sends += 1;
  return candidate.config_revision;
});
assert.equal(allowed.submitted, true);
assert.equal(allowed.result, 4);
assert.equal(sends, 1);
assert.deepEqual(allowed.candidate.web, config.web, 'protocol form must not change web settings');
assert.deepEqual(allowed.candidate.devices, config.devices, 'protocol form must preserve devices');

const deviceOnlyUpdate = {...config, config_revision: 5, devices: [{id: 'device-2'}]};
const safeRebase = rebaseConfig(config, deviceOnlyUpdate, buildEndpoints(values));
assert.equal(safeRebase.conflict, false);
assert.equal(safeRebase.candidate.config_revision, 5);
assert.deepEqual(safeRebase.candidate.devices, deviceOnlyUpdate.devices);

const protocolUpdate = {
  ...deviceOnlyUpdate,
  mqtt: {...deviceOnlyUpdate.mqtt, host: 'changed-elsewhere'},
};
assert.equal(rebaseConfig(config, protocolUpdate, buildEndpoints(values)).conflict, true);

const addedConnection = {
  ...factoryB, id: 'factory-c', name: 'C 工厂', host: 'broker-c', client_id: 'factory-c-gateway',
};
const connectionRebase = rebaseMqttConnections(
  config, deviceOnlyUpdate, [...mqttConnections(config), addedConnection],
);
assert.equal(connectionRebase.conflict, false);
assert.equal(connectionRebase.candidate.devices[0].id, 'device-2');
assert.equal(connectionRebase.candidate.mqtt_connections.length, 3);
const externallyEditedConnections = {
  ...deviceOnlyUpdate,
  mqtt_connections: deviceOnlyUpdate.mqtt_connections.map(connection =>
    connection.id === 'factory-b' ? {...connection, host: 'external-change'} : connection
  ),
};
assert.equal(rebaseMqttConnections(
  config, externallyEditedConnections, mqttConnections(config),
).conflict, true);

assert.equal(
  applyResultMessage({restarted_adapters: [], restart_required: []}),
  '协议设置已保存到 SQLite。 本次修改无需重启协议适配器。',
);
assert.match(
  applyResultMessage({restarted_adapters: ['mqtt'], restart_required: ['modbus']}),
  /已重启适配器：mqtt.*仍需重启后生效：modbus/,
);
