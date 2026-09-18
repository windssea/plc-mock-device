(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.ProtocolForm = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const FIELD_TYPES = {
    'opcua.enabled': 'boolean', 'opcua.host': 'string', 'opcua.port': 'integer',
    'opcua.endpoint_path': 'string', 'opcua.server_name': 'string',
    'opcua.application_uri': 'string', 'opcua.namespace_uri': 'string',
    'mqtt.enabled': 'boolean', 'mqtt.host': 'string', 'mqtt.port': 'integer',
    'mqtt.protocol_version': 'string', 'mqtt.client_id': 'string',
    'mqtt.keepalive': 'integer', 'mqtt.username': 'nullable', 'mqtt.password_env': 'nullable',
    'mqtt.reconnect_min_seconds': 'integer', 'mqtt.reconnect_max_seconds': 'integer',
    'mqtt.max_inflight_messages': 'integer', 'mqtt.max_queued_messages': 'integer',
    'modbus.enabled': 'boolean', 'modbus.host': 'string', 'modbus.port': 'integer',
  };

  const MQTT_CONNECTION_FIELD_TYPES = {
    id: 'string', name: 'string', enabled: 'boolean', host: 'string', port: 'integer',
    transport: 'string', protocol_version: 'string', client_id: 'string',
    keepalive: 'integer', username: 'nullable', password: 'nullable',
    password_env: 'nullable', tls_enabled: 'boolean', reconnect_min_seconds: 'integer',
    reconnect_max_seconds: 'integer', max_inflight_messages: 'integer',
    max_queued_messages: 'integer',
  };

  const MQTT_CONNECTION_LABELS = {
    id: '连接编号', name: '连接名称', host: 'Broker 地址', port: 'Broker 端口',
    transport: '传输方式', protocol_version: '协议版本', client_id: 'Client ID',
    keepalive: 'Keepalive', password_env: '密码环境变量名',
    reconnect_min_seconds: '最小重连间隔', reconnect_max_seconds: '最大重连间隔',
    max_inflight_messages: '最大在途消息数', max_queued_messages: '最大队列消息数',
  };

  const CONNECTION_ID_PATTERN = /^[A-Za-z0-9_.-]+$/;
  const REDACTED_PASSWORD = '********';

  const LABELS = {
    'opcua.host': 'OPC UA 监听地址', 'opcua.port': 'OPC UA 端口',
    'opcua.endpoint_path': 'OPC UA Endpoint 路径', 'opcua.server_name': 'OPC UA 服务名称',
    'opcua.application_uri': 'OPC UA Application URI', 'opcua.namespace_uri': 'OPC UA Namespace URI',
    'mqtt.host': 'MQTT Broker 地址', 'mqtt.port': 'MQTT Broker 端口',
    'mqtt.protocol_version': 'MQTT 协议版本', 'mqtt.client_id': 'MQTT Client ID',
    'mqtt.keepalive': 'MQTT Keepalive', 'mqtt.password_env': 'MQTT 密码环境变量名',
    'mqtt.reconnect_min_seconds': 'MQTT 最小重连间隔', 'mqtt.reconnect_max_seconds': 'MQTT 最大重连间隔',
    'mqtt.max_inflight_messages': 'MQTT 最大在途消息数', 'mqtt.max_queued_messages': 'MQTT 最大队列消息数',
    'modbus.host': 'Modbus 监听地址', 'modbus.port': 'Modbus 端口',
  };

  function endpointValues(config) {
    const values = {};
    Object.keys(FIELD_TYPES).forEach(path => {
      const [section, field] = path.split('.');
      const value = config?.[section]?.[field];
      values[path] = value === null || value === undefined ? '' : value;
    });
    return values;
  }

  function mqttConnections(config) {
    const connections = Array.isArray(config?.mqtt_connections)
      ? config.mqtt_connections.map(connection => ({...connection}))
      : [];
    const defaultIndex = connections.findIndex(connection => connection.id === 'default');
    if (defaultIndex === -1) {
      connections.unshift({
        transport: 'tcp', password: null, tls_enabled: false,
        ...(config?.mqtt || {}), id: 'default', name: '默认连接',
      });
    } else {
      connections[defaultIndex] = {
        ...connections[defaultIndex], ...(config?.mqtt || {}), id: 'default',
        name: connections[defaultIndex].name || '默认连接',
      };
    }
    return connections;
  }

  function mqttConnectionValues(connection = {}) {
    const values = {};
    Object.keys(MQTT_CONNECTION_FIELD_TYPES).forEach(field => {
      const value = connection[field];
      values[field] = value === null || value === undefined ? '' : value;
    });
    return values;
  }

  function buildMqttConnection(values) {
    const connection = {};
    Object.entries(MQTT_CONNECTION_FIELD_TYPES).forEach(([field, type]) => {
      const source = values[field];
      if (type === 'boolean') connection[field] = Boolean(source);
      else if (type === 'integer') connection[field] = Number(source);
      else if (type === 'nullable') connection[field] = source === '' ? null : String(source);
      else connection[field] = String(source ?? '').trim();
    });
    return connection;
  }

  function hasStoredMqttPassword(value) {
    return value === REDACTED_PASSWORD;
  }

  function mqttPasswordSubmission(typedPassword, hasStoredPassword, clearStoredPassword) {
    if (clearStoredPassword) return null;
    if (typedPassword !== '') return String(typedPassword);
    return hasStoredPassword ? REDACTED_PASSWORD : null;
  }

  function buildEndpoints(values) {
    const endpoints = {opcua: {}, mqtt: {}, modbus: {}};
    Object.entries(FIELD_TYPES).forEach(([path, type]) => {
      const [section, field] = path.split('.');
      const source = values[path];
      if (type === 'boolean') endpoints[section][field] = Boolean(source);
      else if (type === 'integer') endpoints[section][field] = Number(source);
      else if (type === 'nullable') endpoints[section][field] = source === '' ? null : String(source);
      else endpoints[section][field] = String(source ?? '');
    });
    return endpoints;
  }

  function add(errors, path, message) {
    errors.push({path, message});
  }

  function requiredText(errors, endpoints, path) {
    const [section, field] = path.split('.');
    if (!endpoints[section][field].trim()) add(errors, path, `${LABELS[path]}不能为空。`);
  }

  function port(errors, endpoints, path) {
    const [section, field] = path.split('.');
    const value = endpoints[section][field];
    if (!Number.isInteger(value) || value < 1 || value > 65535) {
      add(errors, path, `${LABELS[path]}必须是 1 到 65535 之间的整数。`);
    }
  }

  function positiveInteger(errors, endpoints, path, max = null) {
    const [section, field] = path.split('.');
    const value = endpoints[section][field];
    if (!Number.isInteger(value) || value < 1 || (max !== null && value > max)) {
      add(errors, path, `${LABELS[path]}必须是${max ? ` 1 到 ${max} 之间` : '大于或等于 1'}的整数。`);
    }
  }

  function validateEndpoints(endpoints) {
    const errors = [];
    ['opcua.host', 'opcua.endpoint_path', 'opcua.server_name', 'opcua.application_uri',
      'opcua.namespace_uri', 'mqtt.host', 'mqtt.client_id', 'modbus.host']
      .forEach(path => requiredText(errors, endpoints, path));
    ['opcua.port', 'mqtt.port', 'modbus.port'].forEach(path => port(errors, endpoints, path));
    if (!['3.1.1', '5.0'].includes(endpoints.mqtt.protocol_version)) {
      add(errors, 'mqtt.protocol_version', 'MQTT 协议版本只能是 3.1.1 或 5.0。');
    }
    positiveInteger(errors, endpoints, 'mqtt.keepalive', 65535);
    positiveInteger(errors, endpoints, 'mqtt.reconnect_min_seconds');
    positiveInteger(errors, endpoints, 'mqtt.reconnect_max_seconds');
    positiveInteger(errors, endpoints, 'mqtt.max_inflight_messages');
    positiveInteger(errors, endpoints, 'mqtt.max_queued_messages');
    if (Number.isInteger(endpoints.mqtt.reconnect_min_seconds)
      && Number.isInteger(endpoints.mqtt.reconnect_max_seconds)
      && endpoints.mqtt.reconnect_min_seconds > endpoints.mqtt.reconnect_max_seconds) {
      add(errors, 'mqtt.reconnect_max_seconds', '最大重连间隔必须大于或等于最小重连间隔。');
    }
    if (endpoints.mqtt.password_env !== null
      && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(endpoints.mqtt.password_env)) {
      add(errors, 'mqtt.password_env', '密码环境变量名只能包含字母、数字和下划线，且不能以数字开头。');
    }
    return errors;
  }

  function validateMqttConnection(connection, existingConnections = [], originalId = null) {
    const errors = [];
    ['id', 'name', 'host', 'client_id'].forEach(field => {
      if (!connection[field]?.trim()) {
        add(errors, field, `${MQTT_CONNECTION_LABELS[field]}不能为空。`);
      }
    });
    if (connection.id && !CONNECTION_ID_PATTERN.test(connection.id)) {
      add(errors, 'id', '连接编号只能包含字母、数字、点、下划线和短横线。');
    } else if (connection.id.length > 128) {
      add(errors, 'id', '连接编号不能超过 128 个字符。');
    }
    if (connection.name.length > 256) add(errors, 'name', '连接名称不能超过 256 个字符。');
    if (existingConnections.some(item => item.id === connection.id && item.id !== originalId)) {
      add(errors, 'id', `连接编号“${connection.id}”已存在。`);
    }
    if (originalId !== null && connection.id !== originalId) {
      add(errors, 'id', '已保存连接的编号不能修改；如需新编号，请新增连接。');
    }
    if (connection.transport !== 'tcp') {
      add(errors, 'transport', '当前版本仅支持 MQTT TCP。');
    }
    if (!['3.1.1', '5.0'].includes(connection.protocol_version)) {
      add(errors, 'protocol_version', '协议版本只能是 3.1.1 或 5.0。');
    }
    const numberTarget = {mqtt: connection};
    port(errors, numberTarget, 'mqtt.port');
    positiveInteger(errors, numberTarget, 'mqtt.keepalive', 65535);
    positiveInteger(errors, numberTarget, 'mqtt.reconnect_min_seconds');
    positiveInteger(errors, numberTarget, 'mqtt.reconnect_max_seconds');
    positiveInteger(errors, numberTarget, 'mqtt.max_inflight_messages');
    positiveInteger(errors, numberTarget, 'mqtt.max_queued_messages');
    if (Number.isInteger(connection.reconnect_min_seconds)
      && Number.isInteger(connection.reconnect_max_seconds)
      && connection.reconnect_min_seconds > connection.reconnect_max_seconds) {
      add(errors, 'reconnect_max_seconds', '最大重连间隔必须大于或等于最小重连间隔。');
    }
    if (connection.password_env !== null
      && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(connection.password_env)) {
      add(errors, 'password_env', '密码环境变量名只能包含字母、数字和下划线，且不能以数字开头。');
    }
    if (connection.password !== null && connection.password_env !== null) {
      add(errors, 'password', '明文密码和密码环境变量只能填写一项。');
      add(errors, 'password_env', '明文密码和密码环境变量只能填写一项。');
    }
    return errors.map(error => ({
      ...error,
      path: error.path.startsWith('mqtt.') ? error.path.slice('mqtt.'.length) : error.path,
    }));
  }

  function mergeMqttConnections(config, connections) {
    const copied = connections.map(connection => ({...connection}));
    const defaultConnection = copied.find(connection => connection.id === 'default');
    if (!defaultConnection) throw new Error('MQTT 连接列表必须包含 default 连接。');
    const {id: _id, name: _name, ...legacyMqtt} = defaultConnection;
    return {...config, mqtt: legacyMqtt, mqtt_connections: copied};
  }

  function rebaseMqttConnections(baseConfig, latestConfig, connections) {
    if (latestConfig.config_revision !== baseConfig.config_revision) {
      const profilesChanged = JSON.stringify(mqttConnections(latestConfig))
        !== JSON.stringify(mqttConnections(baseConfig));
      const defaultChanged = JSON.stringify(latestConfig.mqtt) !== JSON.stringify(baseConfig.mqtt);
      if (profilesChanged || defaultChanged) {
        return {
          conflict: true,
          message: 'MQTT 连接已被其他操作修改，请重新加载后再保存。',
        };
      }
    }
    return {conflict: false, candidate: mergeMqttConnections(latestConfig, connections)};
  }

  function mergeConfig(config, endpoints) {
    const candidate = {
      ...config,
      opcua: {...endpoints.opcua},
      mqtt: {...(config.mqtt || {}), ...endpoints.mqtt},
      modbus: {...endpoints.modbus},
    };
    if (!Array.isArray(config.mqtt_connections)) return candidate;
    candidate.mqtt_connections = mqttConnections(config).map(connection => connection.id === 'default'
      ? {...connection, ...candidate.mqtt, id: 'default'}
      : connection);
    return candidate;
  }

  function rebaseConfig(baseConfig, latestConfig, endpoints) {
    if (latestConfig.config_revision !== baseConfig.config_revision) {
      const protocolChanged = ['opcua', 'mqtt', 'modbus', 'mqtt_connections'].some(section =>
        JSON.stringify(latestConfig[section]) !== JSON.stringify(baseConfig[section]),
      );
      if (protocolChanged) {
        return {
          conflict: true,
          message: '协议设置已被其他操作修改，请重新加载后再保存。',
        };
      }
    }
    return {conflict: false, candidate: mergeConfig(latestConfig, endpoints)};
  }

  function validatedSubmission(config, values, submit) {
    const endpoints = buildEndpoints(values);
    const errors = validateEndpoints(endpoints);
    if (errors.length) return {submitted: false, errors};
    const candidate = mergeConfig(config, endpoints);
    return {submitted: true, errors: [], candidate, result: submit(candidate)};
  }

  function applyResultMessage(result) {
    const restarted = result?.restarted_adapters || [];
    const lines = ['协议设置已保存到 SQLite。'];
    if (restarted.length) lines.push(`已重启适配器：${restarted.join('、')}。`);
    const pending = Array.isArray(result?.restart_required)
      ? result.restart_required
      : (result?.restart_required ? [String(result.restart_required)] : []);
    if (pending.length) lines.push(`仍需重启后生效：${pending.join('、')}。`);
    else if (!restarted.length) lines.push('本次修改无需重启协议适配器。');
    return lines.join(' ');
  }

  return {
    applyResultMessage,
    buildEndpoints,
    buildMqttConnection,
    endpointValues,
    mergeConfig,
    mergeMqttConnections,
    hasStoredMqttPassword,
    mqttConnections,
    mqttConnectionValues,
    mqttPasswordSubmission,
    REDACTED_PASSWORD,
    rebaseConfig,
    rebaseMqttConnections,
    validatedSubmission,
    validateEndpoints,
    validateMqttConnection,
  };
});
