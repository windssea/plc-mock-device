const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
let config = null;
let statuses = {};
let editingId = null;
let errorId = 0;
let activeProtocol = 'opcua';
let protocolConfigDrafts = {};
let editingMqttConnectionId = null;

async function api(path, options = {}) {
  const response = await fetch('/api/v1' + path, {
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
    ...options,
  });
  const body = await response.text();
  let data;
  try {
    data = body ? JSON.parse(body) : null;
  } catch {
    data = body;
  }
  if (!response.ok) throw new Error(SignalForm.formatApiError(data));
  return data;
}

function toast(message, error = false) {
  const element = $('#toast');
  element.setAttribute('role', error ? 'alert' : 'status');
  element.setAttribute('aria-live', error ? 'assertive' : 'polite');
  element.textContent = message;
  element.style.background = error ? '#a12b22' : '#172536';
  element.style.display = 'block';
  setTimeout(() => element.style.display = 'none', 4500);
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);
}

function stateClass(value) {
  return ['running', 'stopped'].includes(value) ? 'ok' : value === 'degraded' ? 'warn' : 'bad';
}

async function loadConfig() {
  syncConfigViews(await api('/config'));
}

function syncConfigViews(nextConfig) {
  config = nextConfig;
  $('#configEditor').value = JSON.stringify(config, null, 2);
  $('#revision').textContent = `配置版本 ${config.config_revision}`;
  const values = ProtocolForm.endpointValues(config);
  $$('#protocolSettingsForm [data-config-path]').forEach(field => {
    const value = values[field.dataset.configPath];
    if (field.type === 'checkbox') field.checked = value;
    else field.value = value;
  });
  const defaultPasswordSaved = ProtocolForm.hasStoredMqttPassword(config.mqtt?.password);
  const passwordEnvField = $('#protocolSettingsForm [data-config-path="mqtt.password_env"]');
  passwordEnvField.disabled = defaultPasswordSaved;
  passwordEnvField.setAttribute('aria-disabled', String(defaultPasswordSaved));
  $('#defaultMqttPasswordEnvHelp').textContent = defaultPasswordSaved
    ? '默认连接已保存 SQLite 密码。若要改用环境变量，请在下方 MQTT 连接编辑器中清除已保存密码。'
    : '这里只填写环境变量名称，不填写真实密码；进程会从该环境变量读取密码。';
  renderMqttConnections();
  renderDevices();
}

function mqttConnectionList() {
  return ProtocolForm.mqttConnections(config);
}

function mqttConnectionUsage(connectionId) {
  return (config?.devices || []).filter(device =>
    device.protocol === 'mqtt'
    && (device.protocol_config?.connection_id || 'default') === connectionId
  );
}

function renderMqttConnections() {
  if (!config) return;
  const connections = mqttConnectionList();
  const rows = connections.map(connection => {
    const usage = mqttConnectionUsage(connection.id);
    const authentication = connection.username
      ? `用户名：${esc(connection.username)}`
      : '无用户名';
    const passwordState = ProtocolForm.hasStoredMqttPassword(connection.password)
      ? 'SQLite 密码已保存'
      : connection.password_env ? `环境变量 ${esc(connection.password_env)}` : '无密码';
    return `<tr><td><strong>${esc(connection.name)}</strong><br><small>${esc(connection.id)}</small></td><td>${esc(connection.host)}:${esc(connection.port)}<br><small>${connection.tls_enabled ? 'TLS' : '无 TLS'} · ${esc(connection.protocol_version)}</small></td><td>${authentication}<br><small>${passwordState}</small></td><td class="${connection.enabled ? 'ok' : 'warn'}">${connection.enabled ? '启用' : '停用'}</td><td>${usage.length} 台设备</td><td class="actions"><button type="button" onclick="editMqttConnection('${esc(connection.id)}')">编辑</button><button type="button" class="danger" onclick="deleteMqttConnection('${esc(connection.id)}')" ${connection.id === 'default' || usage.length ? 'disabled' : ''}>删除</button></td></tr>`;
  }).join('');
  $('#mqttConnectionTable').innerHTML = `<table><thead><tr><th>连接</th><th>Broker</th><th>认证</th><th>状态</th><th>使用情况</th><th>操作</th></tr></thead><tbody>${rows || '<tr><td colspan="6">暂无 MQTT 连接，请新增连接。</td></tr>'}</tbody></table>`;
}

function protocolSettingsValues() {
  return Object.fromEntries($$('#protocolSettingsForm [data-config-path]').map(field => [
    field.dataset.configPath,
    field.type === 'checkbox' ? field.checked : field.value,
  ]));
}

function clearProtocolSettingsErrors() {
  const form = $('#protocolSettingsForm');
  form.querySelectorAll('.field-error').forEach(element => element.remove());
  form.querySelectorAll('.field-invalid').forEach(element => {
    element.classList.remove('field-invalid');
    element.removeAttribute('aria-invalid');
    element.removeAttribute('aria-describedby');
  });
  const summary = $('#protocolSettingsErrors');
  summary.hidden = true;
  summary.replaceChildren();
}

function showProtocolSettingsErrors(errors) {
  clearProtocolSettingsErrors();
  if (!errors.length) return true;
  const summary = $('#protocolSettingsErrors');
  const heading = document.createElement('strong');
  heading.textContent = '请修正协议设置后再保存：';
  const list = document.createElement('ul');
  summary.append(heading, list);
  let first = null;
  errors.forEach(error => {
    const item = document.createElement('li');
    item.textContent = error.message;
    list.append(item);
    if (!error.path) return;
    const field = $(`#protocolSettingsForm [data-config-path="${error.path}"]`);
    if (!field) return;
    first ||= field;
    field.classList.add('field-invalid');
    field.setAttribute('aria-invalid', 'true');
    const inline = document.createElement('small');
    inline.className = 'field-error';
    inline.id = `protocol-error-${++errorId}`;
    inline.textContent = error.message;
    field.closest('label')?.append(inline);
    field.setAttribute('aria-describedby', inline.id);
  });
  summary.hidden = false;
  (first || summary).focus();
  return false;
}

function showProtocolApplyResult(result) {
  const element = $('#protocolApplyResult');
  element.textContent = ProtocolForm.applyResultMessage(result);
  element.hidden = false;
}

function mqttConnectionFormValues() {
  const values = Object.fromEntries($$('#mqttConnectionForm [data-connection-field]').map(field => [
    field.dataset.connectionField,
    field.type === 'checkbox' ? field.checked : field.value,
  ]));
  const passwordField = $('#mqttConnectionForm [data-connection-field="password"]');
  values.password = ProtocolForm.mqttPasswordSubmission(
    passwordField.value,
    passwordField.dataset.hasStoredPassword === 'true',
    $('#mqttConnectionForm [data-connection-control="clear_password"]').checked,
  );
  return values;
}

function clearMqttConnectionErrors() {
  const form = $('#mqttConnectionForm');
  form.querySelectorAll('.field-error').forEach(element => element.remove());
  form.querySelectorAll('.field-invalid').forEach(element => {
    element.classList.remove('field-invalid');
    element.removeAttribute('aria-invalid');
    element.removeAttribute('aria-describedby');
  });
  const summary = $('#mqttConnectionErrors');
  summary.hidden = true;
  summary.replaceChildren();
}

function showMqttConnectionErrors(errors) {
  clearMqttConnectionErrors();
  if (!errors.length) return true;
  const summary = $('#mqttConnectionErrors');
  const heading = document.createElement('strong');
  heading.textContent = '请修正 MQTT 连接后再保存：';
  const list = document.createElement('ul');
  summary.append(heading, list);
  let first = null;
  errors.forEach(error => {
    const item = document.createElement('li');
    item.textContent = error.message;
    list.append(item);
    const field = error.path
      ? $(`#mqttConnectionForm [data-connection-field="${error.path}"]`)
      : null;
    if (!field) return;
    first ||= field;
    field.classList.add('field-invalid');
    field.setAttribute('aria-invalid', 'true');
    const inline = document.createElement('small');
    inline.className = 'field-error';
    inline.id = `mqtt-connection-error-${++errorId}`;
    inline.textContent = error.message;
    field.closest('label')?.append(inline);
    field.setAttribute('aria-describedby', inline.id);
  });
  summary.hidden = false;
  (first || summary).focus();
  return false;
}

function newMqttConnectionDefaults() {
  return {
    id: '', name: '', enabled: true, host: 'localhost', port: 1883,
    transport: 'tcp', protocol_version: '3.1.1', client_id: '', keepalive: 60,
    username: null, password: null, password_env: null, tls_enabled: false,
    reconnect_min_seconds: 1, reconnect_max_seconds: 30,
    max_inflight_messages: 20, max_queued_messages: 1000,
  };
}

function openMqttConnection(connection = null) {
  editingMqttConnectionId = connection?.id || null;
  const form = $('#mqttConnectionForm');
  form.reset();
  clearMqttConnectionErrors();
  const values = ProtocolForm.mqttConnectionValues(connection || newMqttConnectionDefaults());
  form.querySelectorAll('[data-connection-field]').forEach(field => {
    const value = values[field.dataset.connectionField];
    if (field.type === 'checkbox') field.checked = Boolean(value);
    else field.value = field.dataset.connectionField === 'password' ? '' : value;
  });
  const hasStoredPassword = ProtocolForm.hasStoredMqttPassword(connection?.password);
  const passwordField = form.querySelector('[data-connection-field="password"]');
  passwordField.dataset.hasStoredPassword = String(hasStoredPassword);
  passwordField.disabled = false;
  const clearPassword = form.querySelector('[data-connection-control="clear_password"]');
  clearPassword.checked = false;
  $('#clearMqttPasswordField').hidden = !hasStoredPassword;
  $('#mqttStoredPasswordHelp').textContent = hasStoredPassword
    ? '密码已保存。留空将保留原密码，输入新密码将替换，或勾选清除已保存密码。'
    : '填写后将保存到本地 SQLite，请限制数据库文件访问权限。';
  const idField = form.querySelector('[data-connection-field="id"]');
  idField.readOnly = Boolean(connection);
  idField.setAttribute('aria-readonly', String(Boolean(connection)));
  $('#mqttConnectionDialogTitle').textContent = connection ? '编辑 MQTT 连接' : '新增 MQTT 连接';
  $('#mqttConnectionDialog').showModal();
}

async function saveMqttConnection(connection, originalId = null) {
  const path = originalId
    ? `/mqtt/connections/${encodeURIComponent(originalId)}`
    : '/mqtt/connections';
  const result = await api(path, {
    method: originalId ? 'PUT' : 'POST',
    body: JSON.stringify(connection),
  });
  syncConfigViews(result.config);
  showProtocolApplyResult(result);
  return result;
}

async function loadStatus() {
  try {
    const status = await api('/status');
    statuses = Object.fromEntries((status.devices || []).map(item => [item.device_id, item]));
    renderStatus(status);
  } catch (error) {
    toast(error.message, true);
  }
}

function renderStatus(status) {
  const adapters = status.adapters || {};
  $('#adapterCards').innerHTML = ['opcua', 'mqtt', 'modbus'].map(key => {
    const adapter = adapters[key] || {state: 'stopped'};
    return `<div class="card"><strong>${key.toUpperCase()}</strong><div class="state ${stateClass(adapter.state)}">${esc(adapter.state)}</div><small>${esc(adapter.last_error || '')}</small></div>`;
  }).join('');
  const rows = (status.devices || []).map(device => `<tr><td>${esc(device.device_id)}</td><td class="${stateClass(device.actual_state)}">${esc(device.actual_state)}</td><td>${esc(device.protocol)}</td><td>${esc(device.last_transition_at || '')}</td><td>${Object.entries(device.latest_values || {}).map(([key, value]) => `${esc(key)}=${esc(typeof value === 'object' ? value.value : value)}`).join('<br>')}</td><td class="bad">${esc(device.last_error || '')}</td></tr>`).join('');
  $('#statusTable').innerHTML = `<table><thead><tr><th>设备</th><th>状态</th><th>协议</th><th>最后变化</th><th>最新值</th><th>错误</th></tr></thead><tbody>${rows || '<tr><td colspan="6">暂无设备</td></tr>'}</tbody></table>`;
}

function renderDevices() {
  if (!config) return;
  $('#deviceTable').innerHTML = `<table><thead><tr><th>编号</th><th>名称</th><th>协议</th><th>周期</th><th>状态</th><th>操作</th></tr></thead><tbody>${config.devices.map(device => `<tr><td>${esc(device.id)}</td><td>${esc(device.name)}</td><td>${esc(device.protocol)}</td><td>${device.default_interval_ms} ms</td><td class="${stateClass(statuses[device.id]?.actual_state)}">${esc(statuses[device.id]?.actual_state || device.desired_state)}</td><td class="actions"><button onclick="editDevice('${esc(device.id)}')">编辑</button><button onclick="copyDevice('${esc(device.id)}')">复制</button><button onclick="deviceAction('${esc(device.id)}','${device.desired_state === 'running' ? 'stop' : 'start'}')">${device.desired_state === 'running' ? '暂停' : '启动'}</button><button class="danger" onclick="deleteDevice('${esc(device.id)}')">删除</button></td></tr>`).join('')}</tbody></table>`;
}

function help(text) {
  return `<small class="field-help">${text}</small>`;
}

function numberField(key, label, description, value, attributes = 'step="any"') {
  return `<label>${label}<input data-gen="${key}" type="number" required value="${esc(value)}" ${attributes}>${help(description)}</label>`;
}

function renderSignalFields(node, data = {}) {
  const valueType = node.querySelector('.value-type').value;
  const generatorType = node.querySelector('.generator-type').value;
  const generator = data.generator || {};
  const integerType = ['bool', 'int16', 'uint16', 'int32', 'uint32'].includes(valueType);
  const boolType = valueType === 'bool';
  const numericStep = integerType ? 'step="1"' : 'step="any"';
  const fields = node.querySelector('.generator-fields');

  if (generatorType === 'increment') {
    fields.innerHTML = [
      numberField('initial_value', '初始值', '启动时的第一个值，必须位于增长上下限之间。', generator.initial_value ?? 0, numericStep),
      numberField('value_min', '增长下限', '值不能低于此下限。', generator.value_min ?? 0, numericStep),
      numberField('value_max', '增长上限', '必须大于增长下限。', generator.value_max ?? 100, numericStep),
      numberField('step_min', '每次最小步长', '每次从最小步长到最大步长之间取值；可以为负数。', generator.step_min ?? 1, numericStep),
      numberField('step_max', '每次最大步长', '必须大于或等于最小步长，二者不能同时为 0。', generator.step_max ?? 1, numericStep),
      `<label>越界处理<select data-gen="boundary" required><option value="wrap" ${generator.boundary !== 'clamp' ? 'selected' : ''}>回绕到另一端（wrap）</option><option value="clamp" ${generator.boundary === 'clamp' ? 'selected' : ''}>停在边界（clamp）</option></select>${help('回绕会保留越界超量；停在边界会固定为上下限。')}</label>`,
    ].join('');
  } else {
    const min = boolType ? 0 : (generator.min ?? 0);
    const max = boolType ? 1 : (generator.max ?? 100);
    const precision = generator.precision ?? (integerType ? 0 : 2);
    const rangeAttributes = `${numericStep}${boolType ? ' readonly aria-readonly="true"' : ''}`;
    const precisionAttributes = 'min="0" max="15" step="1"';
    fields.innerHTML = [
      numberField('min', '随机最小值', boolType ? '布尔值固定为 0（假）。' : '每次生成的值不会低于此值。', min, rangeAttributes),
      numberField('max', '随机最大值', boolType ? '布尔值固定为 1（真）。' : '必须大于或等于随机最小值。', max, rangeAttributes),
      numberField('precision', '保留小数位数', integerType ? '允许 0 到 15；整数和布尔结果仍保持整数。' : '允许 0 到 15；用于截取浮点值的小数位数。', precision, precisionAttributes),
    ].join('');
  }

  const protocol = $('#deviceForm [name=protocol]').value;
  const binding = data.binding || {};
  let bindingHtml = '';
  if (protocol === 'opcua') {
    bindingHtml = `<label>OPC UA NodeId<input data-bind="node_id" required value="${esc(binding.node_id ?? '')}">${help('客户端用于稳定定位此点位的唯一节点编号。')}</label><label>浏览名称<input data-bind="browse_name" value="${esc(binding.browse_name ?? '')}">${help('可选；留空时使用点位名称。')}</label><label>显示名称<input data-bind="display_name" value="${esc(binding.display_name ?? '')}">${help('可选；供 OPC UA 客户端界面显示。')}</label>`;
  }
  if (protocol === 'mqtt') {
    bindingHtml = `<label>MQTT JSON 字段名<input data-bind="field_name" required value="${esc(binding.field_name ?? '')}">${help('此点位在 values 对象中的字段名称。')}</label>`;
  }
  if (protocol === 'modbus') {
    const bitType = valueType === 'bool';
    const table = binding.table || (bitType ? 'coil' : 'input_register');
    const wireType = binding.wire_type || (bitType ? 'bool' : valueType);
    bindingHtml = `<label>数据表<select data-bind="table" required>${['coil', 'discrete_input', 'input_register', 'holding_register'].map(value => `<option ${table === value ? 'selected' : ''}>${value}</option>`).join('')}</select>${help('线圈/离散输入用于布尔值，寄存器用于数值。')}</label><label>零基地址<input data-bind="address" type="number" min="0" max="65535" step="1" required value="${binding.address ?? 0}">${help('PDU 地址范围为 0 到 65535。')}</label><label>线类型<select data-bind="wire_type" required>${['bool', 'int16', 'uint16', 'int32', 'uint32', 'float32', 'float64'].map(value => `<option ${wireType === value ? 'selected' : ''}>${value}</option>`).join('')}</select></label><label>字节序<select data-bind="byte_order" required><option>big</option><option ${binding.byte_order === 'little' ? 'selected' : ''}>little</option></select></label><label>字序<select data-bind="word_order" required><option>big</option><option ${binding.word_order === 'little' ? 'selected' : ''}>little</option></select></label><label>缩放系数<input data-bind="scale" type="number" step="any" required value="${binding.scale ?? 1}">${help('换算公式中的 scale，不能为 0。')}</label><label>偏移量<input data-bind="offset" type="number" step="any" required value="${binding.offset ?? 0}"></label>`;
  }
  node.querySelector('.binding-fields').innerHTML = bindingHtml;
}

function collectSignal(node, protocol) {
  return SignalForm.collectSignal(node, protocol);
}

function syncSignalPolicy(node, data = {}) {
  const valueType = node.querySelector('.value-type').value;
  const generatorSelect = node.querySelector('.generator-type');
  const policy = SignalForm.generatorPolicy(valueType, generatorSelect.value);
  generatorSelect.value = policy.type;
  generatorSelect.disabled = policy.locked;
  generatorSelect.setAttribute('aria-disabled', String(policy.locked));
  node.querySelector('.generator-policy').textContent = policy.message;
  const nextData = {...data, generator: {...(data.generator || {}), type: policy.type}};
  if (valueType === 'bool') Object.assign(nextData.generator, {min: 0, max: 1});
  if ($('#deviceForm [name=protocol]').value === 'modbus') {
    nextData.binding = SignalForm.modbusBindingForValueType(valueType, nextData.binding);
  }
  renderSignalFields(node, nextData);
}

function protocolHelp(protocol) {
  if (protocol === 'mqtt') return 'MQTT 至少需要 topic；connection_id 由上方连接选择器维护。示例：{"connection_id":"default","topic":"devices/device-001/telemetry","qos":0,"retain":false}';
  if (protocol === 'modbus') return 'Modbus TCP 至少需要 unit_id（1～247）。示例：{"unit_id":1}';
  return 'OPC UA 可使用空对象 {}，也可设置 object_name。';
}

function parseProtocolConfig(text) {
  try {
    const value = JSON.parse(text || '{}');
    return value && !Array.isArray(value) && typeof value === 'object' ? value : null;
  } catch {
    return null;
  }
}

function syncMqttDeviceConnectionField(protocol, protocolConfig = null) {
  const wrapper = $('#mqttDeviceConnectionField');
  wrapper.hidden = protocol !== 'mqtt';
  if (protocol !== 'mqtt' || !config) return;
  const select = $('#deviceForm [name=mqtt_connection_id]');
  const selected = protocolConfig?.connection_id || 'default';
  const connections = mqttConnectionList();
  select.innerHTML = connections.map(connection => {
    const suffix = connection.enabled ? '' : '，已停用';
    return `<option value="${esc(connection.id)}">${esc(connection.name)} (${esc(connection.id)}${suffix})</option>`;
  }).join('');
  if (!connections.some(connection => connection.id === selected)) {
    select.insertAdjacentHTML('beforeend', `<option value="${esc(selected)}">未知连接 (${esc(selected)})</option>`);
  }
  select.value = selected;
}

function updateMqttConnectionInProtocolText() {
  const textarea = $('#deviceForm [name=protocol_config]');
  const parsed = parseProtocolConfig(textarea.value);
  if (!parsed) return;
  parsed.connection_id = $('#deviceForm [name=mqtt_connection_id]').value || 'default';
  textarea.value = JSON.stringify(parsed, null, 2);
  protocolConfigDrafts.mqtt = textarea.value;
}

function switchProtocolConfig(protocol) {
  const textarea = $('#deviceForm [name=protocol_config]');
  const deviceId = $('#deviceForm [name=id]').value.trim() || 'device-001';
  const next = SignalForm.protocolDraft(
    protocolConfigDrafts, activeProtocol, textarea.value, protocol, deviceId,
  );
  protocolConfigDrafts = next.drafts;
  textarea.value = next.text;
  activeProtocol = protocol;
  $('#protocolConfigHelp').textContent = protocolHelp(protocol);
  syncMqttDeviceConnectionField(protocol, parseProtocolConfig(textarea.value));
}

function addSignal(source = {}) {
  const data = Object.keys(source).length ? source : {
    value_type: 'float32',
    generator: {type: 'random_range', min: 0, max: 100, precision: 2},
  };
  const node = $('#signalTemplate').content.firstElementChild.cloneNode(true);
  $('#signalEditor').append(node);
  node.querySelectorAll('[data-key]').forEach(element => {
    const [parent, child] = element.dataset.key.split('.');
    const value = child ? data[parent]?.[child] : data[parent];
    if (value !== undefined && value !== null) element.value = value;
  });
  node.querySelector('.remove-signal').onclick = () => node.remove();
  node.querySelector('.generator-type').onchange = () => {
    const current = collectSignal(node, $('#deviceForm [name=protocol]').value);
    syncSignalPolicy(node, current);
  };
  node.querySelector('.value-type').onchange = () => {
    const current = collectSignal(node, $('#deviceForm [name=protocol]').value);
    syncSignalPolicy(node, current);
  };
  syncSignalPolicy(node, data);
}

function clearValidationErrors(form) {
  form.querySelectorAll('.field-error').forEach(element => element.remove());
  form.querySelectorAll('.field-invalid').forEach(element => {
    element.classList.remove('field-invalid');
    element.removeAttribute('aria-invalid');
    element.removeAttribute('aria-describedby');
  });
  const summary = $('#formErrors');
  summary.hidden = true;
  summary.replaceChildren();
}

function fieldForError(form, error) {
  if (error.scope === 'device') return form.querySelector(`[name="${error.path}"]`);
  const card = $$('#signalEditor .signal-card')[error.index];
  if (!card) return null;
  if (error.path.startsWith('generator.') && error.path !== 'generator.type') {
    return card.querySelector(`[data-gen="${error.path.slice('generator.'.length)}"]`);
  }
  if (error.path.startsWith('binding.')) {
    return card.querySelector(`[data-bind="${error.path.slice('binding.'.length)}"]`);
  }
  return card.querySelector(`[data-key="${error.path}"]`);
}

function showValidationErrors(form, errors) {
  clearValidationErrors(form);
  if (!errors.length) return true;
  const summary = $('#formErrors');
  const heading = document.createElement('strong');
  heading.textContent = `请修正以下 ${errors.length} 项后再保存：`;
  const list = document.createElement('ul');
  summary.append(heading, list);
  let firstField = null;
  errors.forEach(error => {
    const item = document.createElement('li');
    item.textContent = error.scope === 'signal' ? `数据点 ${error.index + 1}：${error.message}` : error.message;
    list.append(item);
    const field = fieldForError(form, error);
    if (!field) return;
    if (!firstField && !field.disabled) firstField = field;
    field.classList.add('field-invalid');
    field.setAttribute('aria-invalid', 'true');
    const inline = document.createElement('small');
    inline.className = 'field-error';
    inline.id = `field-error-${++errorId}`;
    inline.textContent = error.message;
    field.closest('label')?.append(inline);
    const describedBy = [field.getAttribute('aria-describedby'), inline.id].filter(Boolean).join(' ');
    field.setAttribute('aria-describedby', describedBy);
  });
  summary.hidden = false;
  (firstField || summary).focus();
  return false;
}

function openDevice(device = null) {
  editingId = device?.id || null;
  const form = $('#deviceForm');
  form.reset();
  clearValidationErrors(form);
  form.id.value = device?.id || '';
  form.name.value = device?.name || '';
  form.protocol.value = device?.protocol || 'opcua';
  form.default_interval_ms.value = device?.default_interval_ms || 1000;
  form.enabled.checked = device?.enabled ?? true;
  form.desired_state.value = device?.desired_state || 'running';
  const initialProtocolConfig = device?.protocol_config
    ?? SignalForm.defaultProtocolConfig(form.protocol.value, form.id.value || 'device-001');
  form.protocol_config.value = JSON.stringify(initialProtocolConfig, null, 2);
  activeProtocol = form.protocol.value;
  protocolConfigDrafts = {[activeProtocol]: form.protocol_config.value};
  $('#protocolConfigHelp').textContent = protocolHelp(activeProtocol);
  syncMqttDeviceConnectionField(activeProtocol, initialProtocolConfig);
  $('#signalEditor').innerHTML = '';
  (device?.signals || []).forEach(addSignal);
  $('#dialogTitle').textContent = device ? '编辑设备' : '新增设备';
  $('#deviceDialog').showModal();
}

window.editDevice = id => openDevice(config.devices.find(device => device.id === id));
window.copyDevice = id => {
  const device = structuredClone(config.devices.find(item => item.id === id));
  device.id += '-copy';
  device.name += ' 副本';
  openDevice(device);
  editingId = null;
};
window.editMqttConnection = id => openMqttConnection(
  mqttConnectionList().find(connection => connection.id === id),
);
window.deleteMqttConnection = async id => {
  if (id === 'default') {
    toast('default 是兼容默认连接，不能删除。', true);
    return;
  }
  const usage = mqttConnectionUsage(id);
  if (usage.length) {
    toast(`连接正在被 ${usage.length} 台设备使用，请先调整设备的 MQTT 连接。`, true);
    return;
  }
  if (!confirm(`确认删除 MQTT 连接 ${id}？`)) return;
  try {
    const result = await api(`/mqtt/connections/${encodeURIComponent(id)}`, {method: 'DELETE'});
    syncConfigViews(result.config);
    showProtocolApplyResult(result);
    toast('MQTT 连接已删除');
  } catch (error) {
    toast(error.message, true);
  }
};
window.deviceAction = async (id, action) => {
  try {
    await api(`/devices/${encodeURIComponent(id)}/actions/${action}`, {method: 'POST'});
    await loadConfig();
    await loadStatus();
    toast('操作已提交');
  } catch (error) {
    toast(error.message, true);
  }
};
window.deleteDevice = async id => {
  if (!confirm(`确认删除 ${id}？`)) return;
  try {
    await api(`/devices/${encodeURIComponent(id)}`, {method: 'DELETE'});
    await loadConfig();
    toast('已删除');
  } catch (error) {
    toast(error.message, true);
  }
};

$('#deviceForm').onsubmit = async event => {
  event.preventDefault();
  const form = event.target;
  let protocolConfig;
  const parseErrors = [];
  try {
    protocolConfig = JSON.parse(form.protocol_config.value || '{}');
    if (!protocolConfig || Array.isArray(protocolConfig) || typeof protocolConfig !== 'object') {
      parseErrors.push({scope: 'device', path: 'protocol_config', message: '协议参数必须是 JSON 对象。'});
    }
  } catch {
    parseErrors.push({scope: 'device', path: 'protocol_config', message: '协议参数不是有效的 JSON，请检查括号、引号和逗号。'});
  }
  if (form.protocol.value === 'mqtt' && protocolConfig
    && !Array.isArray(protocolConfig) && typeof protocolConfig === 'object') {
    protocolConfig.connection_id = form.querySelector('[name=mqtt_connection_id]').value || 'default';
  }
  const device = {
    id: form.id.value.trim(),
    name: form.name.value.trim(),
    enabled: form.enabled.checked,
    desired_state: form.desired_state.value,
    protocol: form.protocol.value,
    default_interval_ms: Number(form.default_interval_ms.value),
    protocol_config: protocolConfig,
    signals: $$('#signalEditor .signal-card').map(node => collectSignal(node, form.protocol.value)),
  };
  try {
    clearValidationErrors(form);
    const submission = SignalForm.validatedSubmission(device, parseErrors, payload => api(
      editingId ? `/devices/${encodeURIComponent(editingId)}` : '/devices', {
        method: editingId ? 'PUT' : 'POST',
        body: JSON.stringify(payload),
      },
    ));
    if (!submission.submitted) {
      showValidationErrors(form, submission.errors);
      return;
    }
    await submission.result;
    $('#deviceDialog').close();
    await loadConfig();
    toast('设备配置已保存');
  } catch (error) {
    toast(error.message, true);
  }
};

$('#newDevice').onclick = () => openDevice();
$('#newMqttConnection').onclick = () => openMqttConnection();
$('#addSignal').onclick = () => addSignal();
$('#closeDialog').onclick = $('#cancelDevice').onclick = () => $('#deviceDialog').close();
$('#closeMqttConnectionDialog').onclick = $('#cancelMqttConnection').onclick = () => $('#mqttConnectionDialog').close();
$('#deviceForm').addEventListener('input', event => {
  if (event.target.matches('input, select, textarea')) clearValidationErrors(event.currentTarget);
  if (event.target.name === 'protocol_config' && activeProtocol === 'mqtt') {
    syncMqttDeviceConnectionField('mqtt', parseProtocolConfig(event.target.value));
  }
});
$('#deviceForm [name=mqtt_connection_id]').onchange = updateMqttConnectionInProtocolText;
$('#deviceForm [name=protocol]').onchange = () => {
  const protocol = $('#deviceForm [name=protocol]').value;
  switchProtocolConfig(protocol);
  $$('.signal-card').forEach(node => {
    const data = collectSignal(node, protocol);
    syncSignalPolicy(node, data);
  });
};
$('#mqttConnectionForm').addEventListener('input', clearMqttConnectionErrors);
$('#mqttConnectionForm [data-connection-control="clear_password"]').onchange = event => {
  const passwordField = $('#mqttConnectionForm [data-connection-field="password"]');
  passwordField.disabled = event.target.checked;
  if (event.target.checked) passwordField.value = '';
  clearMqttConnectionErrors();
};
$('#mqttConnectionForm').onsubmit = async event => {
  event.preventDefault();
  clearMqttConnectionErrors();
  const connection = ProtocolForm.buildMqttConnection(mqttConnectionFormValues());
  const connections = mqttConnectionList();
  const errors = ProtocolForm.validateMqttConnection(
    connection, connections, editingMqttConnectionId,
  );
  if (!showMqttConnectionErrors(errors)) return;
  try {
    await saveMqttConnection(connection, editingMqttConnectionId);
    $('#mqttConnectionDialog').close();
    toast('MQTT 连接已保存到 SQLite');
  } catch (error) {
    showMqttConnectionErrors([{path: null, message: error.message}]);
    toast(error.message, true);
  }
};
$('#protocolSettingsForm').addEventListener('input', clearProtocolSettingsErrors);
$('#protocolSettingsForm').onsubmit = async event => {
  event.preventDefault();
  clearProtocolSettingsErrors();
  $('#protocolApplyResult').hidden = true;
  const endpoints = ProtocolForm.buildEndpoints(protocolSettingsValues());
  const errors = ProtocolForm.validateEndpoints(endpoints);
  if (!showProtocolSettingsErrors(errors)) return;
  try {
    const latest = await api('/config');
    const rebased = ProtocolForm.rebaseConfig(config, latest, endpoints);
    if (rebased.conflict) {
      showProtocolSettingsErrors([{path: null, message: rebased.message}]);
      return;
    }
    const result = await api('/config', {
      method: 'PUT',
      body: JSON.stringify(rebased.candidate),
    });
    syncConfigViews(result.config);
    showProtocolApplyResult(result);
    toast('协议设置已保存');
  } catch (error) {
    showProtocolSettingsErrors([{path: null, message: error.message}]);
    toast(error.message, true);
  }
};
$('#validateConfig').onclick = async () => {
  try {
    await api('/config/validate', {method: 'POST', body: $('#configEditor').value});
    toast('配置校验通过');
  } catch (error) {
    toast(error.message, true);
  }
};
$('#saveConfig').onclick = async () => {
  try {
    const candidate = JSON.parse($('#configEditor').value);
    const result = await api('/config', {method: 'PUT', body: JSON.stringify(candidate)});
    syncConfigViews(result.config);
    showProtocolApplyResult(result);
    toast('配置已应用');
  } catch (error) {
    toast(error.message, true);
  }
};
$('#exportConfig').onclick = () => window.location = '/api/v1/config/export';
$('#importFile').onchange = async event => {
  try {
    const text = await event.target.files[0].text();
    await fetch('/api/v1/config/import', {
      method: 'POST', headers: {'Content-Type': 'application/yaml'}, body: text,
    }).then(async response => {
      if (!response.ok) {
        const body = await response.text();
        let data;
        try { data = JSON.parse(body); } catch { data = body; }
        throw new Error(SignalForm.formatApiError(data));
      }
    });
    await loadConfig();
    toast('配置备份已导入 SQLite');
  } catch (error) {
    toast(error.message, true);
  }
};
$$('nav button').forEach(button => button.onclick = () => {
  $$('nav button,.tab').forEach(element => element.classList.remove('active'));
  button.classList.add('active');
  $('#' + button.dataset.tab).classList.add('active');
});
Promise.all([loadConfig(), loadStatus()]).catch(error => toast(error.message, true));
setInterval(() => loadStatus().then(renderDevices), 1000);
