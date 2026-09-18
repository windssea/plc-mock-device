(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.SignalForm = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const INTEGER_TYPES = new Set(['bool', 'int16', 'uint16', 'int32', 'uint32']);
  const TYPE_BOUNDS = {
    bool: [0, 1],
    int16: [-32768, 32767],
    uint16: [0, 65535],
    int32: [-2147483648, 2147483647],
    uint32: [0, 4294967295],
    float32: [-3.4028235e38, 3.4028235e38],
    float64: [-Number.MAX_VALUE, Number.MAX_VALUE],
  };
  const WIRE_REGISTER_LENGTH = {
    int16: 1, uint16: 1, int32: 2, uint32: 2, float32: 2, float64: 4,
  };
  const ID_PATTERN = /^[A-Za-z0-9_.-]+$/;
  const FIELD_NAMES = {
    id: '编号',
    name: '名称',
    default_interval_ms: '默认周期',
    interval_ms: '点位周期',
    value_type: '数值类型',
    generator: '生成器',
    'generator.type': '生成方式',
    'generator.min': '随机最小值',
    'generator.max': '随机最大值',
    'generator.precision': '小数位数',
    'generator.initial_value': '初始值',
    'generator.value_min': '增长下限',
    'generator.value_max': '增长上限',
    'generator.step_min': '最小步长',
    'generator.step_max': '最大步长',
    'binding.node_id': 'OPC UA NodeId',
    'binding.field_name': 'MQTT JSON 字段名',
    'binding.table': 'Modbus 数据表',
    'binding.wire_type': 'Modbus 线类型',
    'binding.address': 'Modbus 地址',
    'binding.scale': 'Modbus 缩放系数',
    'binding.offset': 'Modbus 偏移量',
    protocol_config: '协议参数',
    'protocol_config.type': '协议参数类型',
    'protocol_config.object_name': 'OPC UA 对象名称',
    'protocol_config.topic': 'MQTT topic',
    'protocol_config.connection_id': 'MQTT 连接编号',
    'protocol_config.qos': 'MQTT QoS',
    'protocol_config.retain': 'MQTT retain',
    'protocol_config.publish_interval_ms': 'MQTT 发布周期',
    'protocol_config.payload_mode': 'MQTT payload 模式',
    'protocol_config.unit_id': 'Modbus unit_id',
  };

  function setDeep(target, path, value) {
    const [parent, child] = path.split('.');
    if (child) {
      target[parent] ??= {};
      target[parent][child] = value;
    } else {
      target[parent] = value;
    }
  }

  function elementValue(element) {
    if (element.type !== 'number') return element.value;
    return element.value === '' ? null : Number(element.value);
  }

  function collectSignal(node, protocol) {
    const signal = {};
    node.querySelectorAll('[data-key]').forEach(element => {
      const value = elementValue(element);
      if (value !== null && value !== '') setDeep(signal, element.dataset.key, value);
    });

    // Preserve generator.type while merging the currently visible type-specific fields.
    const generator = {...(signal.generator || {})};
    node.querySelectorAll('[data-gen]').forEach(element => {
      const value = elementValue(element);
      if (value !== null && value !== '') generator[element.dataset.gen] = value;
    });

    const binding = {type: protocol};
    node.querySelectorAll('[data-bind]').forEach(element => {
      const value = elementValue(element);
      if (value !== null && value !== '') binding[element.dataset.bind] = value;
    });
    signal.generator = generator;
    signal.binding = binding;
    return signal;
  }

  function generatorPolicy(valueType, requestedType = 'random_range') {
    if (valueType === 'bool') {
      return {
        type: 'random_range',
        locked: true,
        precision: 0,
        message: '布尔类型只能使用区间随机，并固定在 0（假）到 1（真）之间。',
      };
    }
    const integer = INTEGER_TYPES.has(valueType);
    return {
      type: requestedType || 'random_range',
      locked: false,
      precision: integer ? 0 : 2,
      message: requestedType === 'increment'
        ? '递增会在步长区间内取值后累加；步长可以为负数，但布尔类型不能使用递增。'
        : integer
          ? '整数类型会在闭区间内生成整数；小数位数允许 0～15，但不改变整数结果。'
          : '区间随机会在最小值和最大值之间生成数值，可设置保留小数位数。',
    };
  }

  function defaultProtocolConfig(protocol, deviceId = 'device-001') {
    if (protocol === 'mqtt') {
      const safeId = ID_PATTERN.test(deviceId) ? deviceId : 'device-001';
      return {connection_id: 'default', topic: `devices/${safeId}/telemetry`, qos: 0, retain: false};
    }
    if (protocol === 'modbus') return {unit_id: 1};
    return {};
  }

  function protocolDraft(drafts, currentProtocol, currentText, nextProtocol, deviceId) {
    const nextDrafts = {...drafts, [currentProtocol]: currentText};
    if (!Object.prototype.hasOwnProperty.call(nextDrafts, nextProtocol)) {
      nextDrafts[nextProtocol] = JSON.stringify(
        defaultProtocolConfig(nextProtocol, deviceId), null, 2,
      );
    }
    return {drafts: nextDrafts, text: nextDrafts[nextProtocol]};
  }

  function modbusBindingForValueType(valueType, source = {}) {
    const binding = {...source, type: 'modbus'};
    const bitTable = ['coil', 'discrete_input'].includes(binding.table);
    const registerTable = ['input_register', 'holding_register'].includes(binding.table);
    if (valueType === 'bool') {
      if (!bitTable) binding.table = 'coil';
      binding.wire_type = 'bool';
    } else {
      if (!registerTable) binding.table = 'input_register';
      if (!binding.wire_type || binding.wire_type === 'bool') binding.wire_type = valueType;
    }
    return binding;
  }

  function addError(errors, path, message) {
    errors.push({path, message});
  }

  function requiredNumber(errors, generator, key, path) {
    const value = generator[key];
    if (value === undefined || value === null || value === '') {
      addError(errors, path, `${FIELD_NAMES[path]}不能为空。`);
      return null;
    }
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      addError(errors, path, `${FIELD_NAMES[path]}必须是有限数字。`);
      return null;
    }
    return value;
  }

  function validateSignal(signal) {
    const errors = [];
    if (!signal.id) addError(errors, 'id', '点位编号不能为空。');
    else if (!ID_PATTERN.test(signal.id)) addError(errors, 'id', '点位编号只能包含字母、数字、点、下划线和短横线。');
    else if (signal.id.length > 128) addError(errors, 'id', '点位编号不能超过 128 个字符。');
    if (!signal.name) addError(errors, 'name', '点位名称不能为空。');
    else if (typeof signal.name !== 'string' || signal.name.length > 256) addError(errors, 'name', '点位名称不能超过 256 个字符。');
    if (signal.unit !== undefined && signal.unit !== null && (
      typeof signal.unit !== 'string' || signal.unit.length > 64
    )) addError(errors, 'unit', '单位不能超过 64 个字符。');
    if (!TYPE_BOUNDS[signal.value_type]) addError(errors, 'value_type', '请选择有效的数值类型。');
    if (signal.interval_ms !== undefined && (
      !Number.isInteger(signal.interval_ms) || signal.interval_ms < 1000
    )) addError(errors, 'interval_ms', '点位周期必须是大于或等于 1000 的整数毫秒数。');

    const generator = signal.generator || {};
    const type = generator.type;
    if (!['random_range', 'increment'].includes(type)) {
      addError(errors, 'generator.type', '请选择生成方式。');
      return errors;
    }
    if (signal.value_type === 'bool' && type === 'increment') {
      addError(errors, 'generator.type', '布尔类型不能使用递增，请选择区间随机。');
    }

    const numeric = [];
    const bounded = [];
    if (type === 'random_range') {
      const min = requiredNumber(errors, generator, 'min', 'generator.min');
      const max = requiredNumber(errors, generator, 'max', 'generator.max');
      const precision = requiredNumber(errors, generator, 'precision', 'generator.precision');
      if (min !== null) {
        numeric.push(['generator.min', min]);
        bounded.push(['generator.min', min]);
      }
      if (max !== null) {
        numeric.push(['generator.max', max]);
        bounded.push(['generator.max', max]);
      }
      if (min !== null && max !== null && min > max) {
        addError(errors, 'generator.max', '随机最大值必须大于或等于随机最小值。');
      }
      if (precision !== null && (!Number.isInteger(precision) || precision < 0 || precision > 15)) {
        addError(errors, 'generator.precision', '小数位数必须是 0 到 15 之间的整数。');
      }
      if (signal.value_type === 'bool' && min !== null && max !== null && (
        ![0, 1].includes(min) || ![0, 1].includes(max)
      )) addError(errors, 'generator.min', '布尔随机范围的最小值和最大值只能是 0 或 1。');
    } else {
      const keys = ['initial_value', 'value_min', 'value_max', 'step_min', 'step_max'];
      const values = Object.fromEntries(keys.map(key => [
        key,
        requiredNumber(errors, generator, key, `generator.${key}`),
      ]));
      keys.forEach(key => {
        if (values[key] !== null) numeric.push([`generator.${key}`, values[key]]);
      });
      ['initial_value', 'value_min', 'value_max'].forEach(key => {
        if (values[key] !== null) bounded.push([`generator.${key}`, values[key]]);
      });
      if (values.value_min !== null && values.value_max !== null && values.value_min >= values.value_max) {
        addError(errors, 'generator.value_max', '增长上限必须大于增长下限。');
      }
      if (values.initial_value !== null && values.value_min !== null && values.value_max !== null && (
        values.initial_value < values.value_min || values.initial_value > values.value_max
      )) addError(errors, 'generator.initial_value', '初始值必须位于增长下限和增长上限之间。');
      if (values.step_min !== null && values.step_max !== null && values.step_min > values.step_max) {
        addError(errors, 'generator.step_max', '最大步长必须大于或等于最小步长。');
      }
      if (values.step_min === 0 && values.step_max === 0) {
        addError(errors, 'generator.step_min', '最小步长和最大步长不能同时为 0。');
      }
    }

    if (INTEGER_TYPES.has(signal.value_type)) {
      numeric.forEach(([path, value]) => {
        if (!Number.isInteger(value)) addError(errors, path, `${FIELD_NAMES[path]}必须是整数，不能自动舍入小数。`);
      });
    }
    const bounds = TYPE_BOUNDS[signal.value_type];
    if (bounds) bounded.forEach(([path, value]) => {
      if (value < bounds[0] || value > bounds[1]) {
        addError(errors, path, `${FIELD_NAMES[path]}超出 ${signal.value_type} 可表示的范围。`);
      }
    });

    const binding = signal.binding || {};
    if (binding.type === 'opcua' && !binding.node_id) {
      addError(errors, 'binding.node_id', 'OPC UA NodeId 不能为空。');
    } else if (binding.type === 'mqtt' && !binding.field_name) {
      addError(errors, 'binding.field_name', 'MQTT JSON 字段名不能为空。');
    } else if (binding.type === 'modbus') {
      const bitTable = ['coil', 'discrete_input'].includes(binding.table);
      const registerTable = ['input_register', 'holding_register'].includes(binding.table);
      if (!bitTable && !registerTable) {
        addError(errors, 'binding.table', '请选择有效的 Modbus 数据表。');
      } else if (bitTable && signal.value_type !== 'bool') {
        addError(errors, 'binding.table', '线圈和离散输入只能映射 bool 点位。');
      } else if (registerTable && signal.value_type === 'bool') {
        addError(errors, 'binding.table', 'bool 点位必须使用线圈或离散输入。');
      }
      if (bitTable && binding.wire_type !== 'bool') {
        addError(errors, 'binding.wire_type', '线圈和离散输入的线类型必须是 bool。');
      } else if (registerTable && binding.wire_type === 'bool') {
        addError(errors, 'binding.wire_type', '输入寄存器和保持寄存器必须使用数值线类型。');
      }
      if (!Number.isInteger(binding.address) || binding.address < 0 || binding.address > 65535) {
        addError(errors, 'binding.address', 'Modbus 地址必须是 0 到 65535 之间的整数。');
      }
      const scaleValid = typeof binding.scale === 'number' && Number.isFinite(binding.scale) && binding.scale !== 0;
      const offsetValid = typeof binding.offset === 'number' && Number.isFinite(binding.offset);
      if (!scaleValid) {
        addError(errors, 'binding.scale', 'Modbus 缩放系数必须是非零有限数字。');
      }
      if (!offsetValid) {
        addError(errors, 'binding.offset', 'Modbus 偏移量必须是有限数字。');
      }
      const registerLength = bitTable ? 1 : WIRE_REGISTER_LENGTH[binding.wire_type];
      if (registerTable && !registerLength) {
        addError(errors, 'binding.wire_type', '请选择有效的数值线类型。');
      }
      if (registerLength && Number.isInteger(binding.address) && binding.address + registerLength > 65536) {
        addError(errors, 'binding.address', `${binding.wire_type} 需要 ${registerLength} 个寄存器，地址已超出 65535。`);
      }
      if (binding.register_length !== undefined && registerLength && binding.register_length !== registerLength) {
        addError(errors, 'binding.wire_type', `${binding.wire_type} 必须占用 ${registerLength} 个寄存器。`);
      }
      if (registerTable && registerLength && scaleValid && offsetValid) {
        const wireBounds = TYPE_BOUNDS[binding.wire_type];
        const integerWire = ['int16', 'uint16', 'int32', 'uint32'].includes(binding.wire_type);
        const exceedsWire = bounded.some(([, value]) => {
          let raw = (Number(value) - binding.offset) / binding.scale;
          if (integerWire) raw = Math.round(raw);
          return !Number.isFinite(raw) || raw < wireBounds[0] || raw > wireBounds[1];
        });
        if (exceedsWire) {
          addError(errors, 'binding.wire_type', '生成范围经 scale/offset 换算后超出 Modbus 线类型范围。');
        }
      }
    }
    return errors;
  }

  function extraConfigKeys(config, allowed) {
    return Object.keys(config).filter(key => !allowed.includes(key));
  }

  function validateProtocolConfig(device, errors) {
    const config = device.protocol_config;
    if (!config || Array.isArray(config) || typeof config !== 'object') {
      errors.push({scope: 'device', path: 'protocol_config', message: '协议参数必须是 JSON 对象。'});
      return;
    }
    if (!['opcua', 'mqtt', 'modbus'].includes(device.protocol)) {
      errors.push({scope: 'device', path: 'protocol_config', message: '请选择有效的设备协议。'});
      return;
    }
    if (config.type !== undefined && config.type !== device.protocol) {
      errors.push({scope: 'device', path: 'protocol_config', message: '协议参数 type 必须与设备协议一致。'});
    }
    const common = ['type'];
    if (device.protocol === 'opcua') {
      const extras = extraConfigKeys(config, [...common, 'object_name']);
      if (extras.length) errors.push({scope: 'device', path: 'protocol_config', message: `OPC UA 协议参数不支持字段：${extras.join('、')}。`});
      if (config.object_name !== undefined && config.object_name !== null && typeof config.object_name !== 'string') {
        errors.push({scope: 'device', path: 'protocol_config', message: 'OPC UA object_name 必须是字符串或 null。'});
      }
      return;
    }
    if (device.protocol === 'mqtt') {
      const allowed = [...common, 'connection_id', 'topic', 'qos', 'retain', 'publish_interval_ms', 'payload_mode'];
      const extras = extraConfigKeys(config, allowed);
      if (extras.length) errors.push({scope: 'device', path: 'protocol_config', message: `MQTT 协议参数不支持字段：${extras.join('、')}。`});
      if (config.connection_id !== undefined && (
        typeof config.connection_id !== 'string' || !config.connection_id.length
        || config.connection_id.length > 128 || !ID_PATTERN.test(config.connection_id)
      )) {
        errors.push({scope: 'device', path: 'protocol_config', message: 'MQTT connection_id 只能包含字母、数字、点、下划线和短横线，且不能超过 128 个字符。'});
      }
      if (typeof config.topic !== 'string' || !config.topic.length || config.topic.length > 65535) {
        errors.push({scope: 'device', path: 'protocol_config', message: 'MQTT topic 必须是长度 1～65535 的字符串。'});
      } else if (/[+#\0]/.test(config.topic)) {
        errors.push({scope: 'device', path: 'protocol_config', message: 'MQTT 发布 topic 不能包含 +、# 通配符或空字符。'});
      }
      if (config.qos !== undefined && ![0, 1, 2].includes(config.qos)) {
        errors.push({scope: 'device', path: 'protocol_config', message: 'MQTT qos 只能是 0、1 或 2。'});
      }
      if (config.retain !== undefined && typeof config.retain !== 'boolean') {
        errors.push({scope: 'device', path: 'protocol_config', message: 'MQTT retain 必须是 true 或 false。'});
      }
      if (config.publish_interval_ms !== undefined && config.publish_interval_ms !== null && (
        !Number.isInteger(config.publish_interval_ms) || config.publish_interval_ms < 1000
      )) errors.push({scope: 'device', path: 'protocol_config', message: 'MQTT 发布周期必须是大于或等于 1000 的整数毫秒数。'});
      if (config.payload_mode !== undefined && !['nested', 'flat', 'values'].includes(config.payload_mode)) {
        errors.push({scope: 'device', path: 'protocol_config', message: 'MQTT payload_mode 只能是 nested、flat 或 values。'});
      }
      return;
    }
    const extras = extraConfigKeys(config, [...common, 'unit_id']);
    if (extras.length) errors.push({scope: 'device', path: 'protocol_config', message: `Modbus 协议参数不支持字段：${extras.join('、')}。`});
    if (!Number.isInteger(config.unit_id) || config.unit_id < 1 || config.unit_id > 247) {
      errors.push({scope: 'device', path: 'protocol_config', message: 'Modbus unit_id 必须是 1 到 247 之间的整数。'});
    }
  }

  function validateDevice(device) {
    const errors = [];
    if (!device.id) errors.push({scope: 'device', path: 'id', message: '设备编号不能为空。'});
    else if (!ID_PATTERN.test(device.id)) errors.push({scope: 'device', path: 'id', message: '设备编号只能包含字母、数字、点、下划线和短横线。'});
    else if (device.id.length > 128) errors.push({scope: 'device', path: 'id', message: '设备编号不能超过 128 个字符。'});
    if (!device.name) errors.push({scope: 'device', path: 'name', message: '设备名称不能为空。'});
    else if (typeof device.name !== 'string' || device.name.length > 256) errors.push({scope: 'device', path: 'name', message: '设备名称不能超过 256 个字符。'});
    if (!Number.isInteger(device.default_interval_ms) || device.default_interval_ms < 1000) {
      errors.push({scope: 'device', path: 'default_interval_ms', message: '默认周期必须是大于或等于 1000 的整数毫秒数。'});
    }
    validateProtocolConfig(device, errors);
    const seen = new Set();
    const mqttFields = new Set();
    (device.signals || []).forEach((signal, index) => {
      validateSignal(signal).forEach(error => errors.push({...error, scope: 'signal', index}));
      if (signal.id && seen.has(signal.id)) {
        errors.push({scope: 'signal', index, path: 'id', message: `点位编号“${signal.id}”重复。`});
      }
      seen.add(signal.id);
      if (device.protocol === 'mqtt' && signal.binding?.field_name) {
        if (mqttFields.has(signal.binding.field_name)) {
          errors.push({scope: 'signal', index, path: 'binding.field_name', message: `MQTT JSON 字段名“${signal.binding.field_name}”重复。`});
        }
        mqttFields.add(signal.binding.field_name);
      }
    });
    return errors;
  }

  function validatedSubmission(device, extraErrors, submit) {
    const errors = [...(extraErrors || []), ...validateDevice(device)];
    if (errors.length) return {submitted: false, errors};
    return {submitted: true, errors: [], result: submit(device)};
  }

  function humanPath(location) {
    const path = location.filter(part => part !== 'body');
    const signalAt = path.indexOf('signals');
    let prefix = '';
    let fieldParts = path;
    if (signalAt >= 0 && Number.isInteger(path[signalAt + 1])) {
      prefix = `数据点 ${path[signalAt + 1] + 1} → `;
      fieldParts = path.slice(signalAt + 2);
    }
    const unionTags = new Set(['opcua', 'mqtt', 'modbus', 'increment', 'random_range']);
    fieldParts = fieldParts.filter(part => !unionTags.has(part));
    const field = fieldParts.join('.');
    return prefix + (FIELD_NAMES[field] || fieldParts[fieldParts.length - 1] || '请求');
  }

  function translateMessage(message) {
    const translations = {
      'Field required': '不能为空',
      'min must not exceed max': '最小值不能大于最大值',
      'bool signals do not support increment generators': '布尔类型不能使用递增生成器',
      'value_min must be less than value_max': '增长下限必须小于增长上限',
      'initial_value must be within value_min/value_max': '初始值必须位于增长范围内',
      'step_min must not exceed step_max': '最小步长不能大于最大步长',
      'increment step range cannot be only zero': '步长不能全部为 0',
      'Input should be a valid number': '必须是有效数字',
      'Input should be a valid integer': '必须是整数',
      'Input should be a valid boolean': '必须是 true 或 false',
      'Input should be a valid string': '必须是字符串',
      'Input should be 0, 1 or 2': '只能是 0、1 或 2',
      "Input should be 'nested', 'flat' or 'values'": '只能是 nested、flat 或 values',
      'Extra inputs are not permitted': '不支持此字段',
      'mapping extends beyond Modbus address 65535': '映射占用的寄存器超出 Modbus 地址 65535',
      'generator range exceeds Modbus wire_type after scale/offset': '生成范围经 scale/offset 换算后超出 Modbus 线类型范围',
      'bool random range must use bounds 0 or 1': '布尔随机范围只能使用 0 或 1',
    };
    let normalized = String(message).replace(/^Value error,\s*/, '');
    if (normalized.startsWith('duplicate MQTT json_field in device ')) {
      normalized = '设备内 MQTT JSON 字段名不能重复';
    } else if (normalized.startsWith('duplicate signal_id in device ')) {
      normalized = '设备内点位编号不能重复';
    }
    let result = translations[normalized] || normalized;
    result = result.replace(/^String should have at most (\d+) characters$/, '长度不能超过 $1 个字符');
    result = result.replace(/^String should have at least (\d+) character(?:s)?$/, '长度不能少于 $1 个字符');
    result = result.replace(/^Input should be greater than or equal to (.+)$/, '必须大于或等于 $1');
    result = result.replace(/^Input should be less than or equal to (.+)$/, '必须小于或等于 $1');
    Object.entries(FIELD_NAMES).filter(([raw]) => raw.includes('_') || raw.includes('.')).forEach(([raw, readable]) => {
      result = result.replaceAll(raw, readable);
    });
    return result;
  }

  function formatApiError(data) {
    if (typeof data === 'string') return data;
    const detail = data && data.detail;
    if (Array.isArray(detail)) {
      return detail.map(item => `${humanPath(item.loc || [])}：${translateMessage(item.msg || '配置无效')}`).join('\n');
    }
    if (typeof detail === 'string') return translateMessage(detail);
    return '请求失败，请检查配置后重试。';
  }

  return {
    collectSignal,
    defaultProtocolConfig,
    formatApiError,
    generatorPolicy,
    modbusBindingForValueType,
    protocolDraft,
    validatedSubmission,
    validateDevice,
    validateSignal,
  };
});
