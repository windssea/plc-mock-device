'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'iot_sim', 'static', 'index.html'),
  'utf8',
);

assert.match(html, /<form id="deviceForm"[^>]*\bnovalidate\b/);
assert.match(html, /id="formErrors"[^>]*role="alert"[^>]*aria-live="assertive"/);
assert.match(html, /data-key="value_type" class="value-type"/);
assert.match(html, /class="field-help generator-policy" aria-live="polite"/);
assert.match(html, /<form id="protocolSettingsForm"[^>]*\bnovalidate\b/);
assert.match(html, /<details class="advanced-config">/);
assert.match(html, /这里只填写环境变量名称，不填写真实密码/);
assert.match(html, /<script src="\/static\/protocol-form\.js"><\/script>/);
assert.match(html, /id="mqttConnectionTable"[^>]*aria-live="polite"/);
assert.match(html, /<form id="mqttConnectionForm"[^>]*\bnovalidate\b/);
assert.match(html, /name="mqtt_connection_id"/);
assert.match(html, /data-connection-field="password" type="password"/);
assert.match(html, /data-connection-control="clear_password"/);
assert.doesNotMatch(html, /data-connection-field="password"[^>]*\bvalue=/);
for (const field of [
  'id', 'name', 'enabled', 'host', 'port', 'transport', 'protocol_version', 'client_id',
  'keepalive', 'username', 'password', 'password_env', 'tls_enabled',
  'reconnect_min_seconds', 'reconnect_max_seconds', 'max_inflight_messages',
  'max_queued_messages',
]) assert.match(html, new RegExp(`data-connection-field="${field}"`));
for (const path of [
  'opcua.enabled', 'opcua.host', 'opcua.port', 'opcua.endpoint_path',
  'opcua.server_name', 'opcua.application_uri', 'opcua.namespace_uri',
  'mqtt.enabled', 'mqtt.host', 'mqtt.port', 'mqtt.protocol_version', 'mqtt.client_id',
  'mqtt.keepalive', 'mqtt.username', 'mqtt.password_env', 'mqtt.reconnect_min_seconds',
  'mqtt.reconnect_max_seconds', 'mqtt.max_inflight_messages', 'mqtt.max_queued_messages',
  'modbus.enabled', 'modbus.host', 'modbus.port',
]) assert.match(html, new RegExp(`data-config-path="${path.replace('.', '\\.')}`));

function inputPattern(selector) {
  const tag = html.match(selector);
  assert.ok(tag, `missing input matching ${selector}`);
  const pattern = tag[0].match(/\bpattern="([^"]+)"/);
  assert.ok(pattern, `missing pattern attribute on ${tag[0]}`);
  return pattern[1];
}

const patterns = {
  deviceId: inputPattern(/<input\b(?=[^>]*\bname="id")[^>]*>/),
  signalId: inputPattern(/<input\b(?=[^>]*\bdata-key="id")[^>]*>/),
};

for (const [field, pattern] of Object.entries(patterns)) {
  assert.equal(pattern, String.raw`[A-Za-z0-9_.\-]+`, `${field} pattern changed unexpectedly`);
  const regexp = new RegExp(`^(?:${pattern})$`, 'v');
  assert.equal(regexp.test('device-1.test_value'), true, `${field} should accept supported IDs`);
  assert.equal(regexp.test('bad id'), false, `${field} should reject whitespace`);
  assert.equal(regexp.test('bad/segment'), false, `${field} should reject slashes`);
}
