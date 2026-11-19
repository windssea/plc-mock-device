(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.DeviceTypeForm = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const ID_PATTERN = /^[A-Za-z0-9_.-]+$/;
  const MAX_DEVICE_NUMBER = 9999;
  // GET /api/v1/device-types does not publish Modbus table or wire width.
  // This preview mirrors the code catalog in iot_sim/device_types.py.
  const MODBUS_LAYOUT = {
    'modbus.pressure': [
      {id: 'pressure', table: 'input_register', wire_type: 'float32', width: 2},
    ],
  };
  const WIRE_WIDTH = {
    bool: 1, int16: 1, uint16: 1, int32: 2, uint32: 2, float32: 2, float64: 4,
  };

  function canonicalNumber(prefix, deviceId) {
    const marker = `${prefix}-`;
    if (!deviceId.startsWith(marker)) return null;
    const suffix = deviceId.slice(marker.length);
    if (!/^\d+$/.test(suffix)) return null;
    const number = Number(suffix);
    if (!Number.isSafeInteger(number) || number < 1 || number > MAX_DEVICE_NUMBER) return null;
    if (`${prefix}-${String(number).padStart(3, '0')}` !== deviceId) return null;
    return number;
  }

  function autoName(type, deviceId) {
    const number = canonicalNumber(type.id_prefix, deviceId);
    if (number === null) return `${type.name} ${deviceId}`;
    return `${type.name} ${String(number).padStart(3, '0')}`;
  }

  function applyTopicPattern(pattern, deviceId) {
    return String(pattern).replaceAll('{device_id}', () => deviceId);
  }

  function suggestionForDeviceId(type, deviceId) {
    const id = String(deviceId ?? '').trim();
    const mqtt = type?.protocol === 'mqtt';
    const topic = mqtt
      ? (id && type.topic_pattern ? applyTopicPattern(type.topic_pattern, id) : '')
      : null;
    const nodeIds = {};
    if (type?.protocol === 'opcua') {
      for (const signal of type.signals || []) {
        nodeIds[signal.id] = `${id}.${signal.id}`;
      }
    }
    return {
      device_id: id,
      name: id ? autoName(type, id) : '',
      topic,
      node_ids: nodeIds,
    };
  }

  function draftFromType(type) {
    const suggestion = type?.suggestion || {};
    const defaults = type?.defaults || {};
    return {
      type,
      deviceId: suggestion.device_id || '',
      name: suggestion.name || '',
      nameDirty: false,
      enabled: true,
      desiredState: 'running',
      connectionId: defaults.connection_id ?? '',
      topic: suggestion.topic || '',
      topicDirty: false,
      qos: defaults.qos ?? '',
      retain: Boolean(defaults.retain),
      payloadMode: defaults.payload_mode || '',
      publishIntervalMs: defaults.publish_interval_ms ?? '',
      unitId: suggestion.unit_id ?? '',
      addressBase: type?.protocol === 'modbus' ? 0 : '',
      nodeIds: {},
    };
  }

  function syncDraftForDeviceId(draft, deviceId) {
    const suggestion = suggestionForDeviceId(draft.type, String(deviceId ?? '').trim());
    const next = {...draft, deviceId: String(deviceId ?? ''), nodeIds: {...(draft.nodeIds || {})}};
    if (!draft.nameDirty) next.name = suggestion.name;
    if (!draft.topicDirty) next.topic = suggestion.topic || '';
    return next;
  }

  function modbusRows(type) {
    if (MODBUS_LAYOUT[type?.id]) return MODBUS_LAYOUT[type.id];
    return (type?.signals || []).map(signal => {
      const table = signal.table || '';
      const wireType = signal.wire_type || signal.value_type || '';
      const width = table === 'coil' || table === 'discrete_input'
        ? 1
        : (WIRE_WIDTH[wireType] || 1);
      return {id: signal.id, table, wire_type: wireType, width};
    });
  }

  function modbusAddressLines(type, addressBase) {
    const base = Number.isInteger(addressBase) ? addressBase : 0;
    const cursors = {};
    return modbusRows(type).map(row => {
      const start = Object.prototype.hasOwnProperty.call(cursors, row.table) ? cursors[row.table] : base;
      cursors[row.table] = start + row.width;
      return `${row.id}，表 ${row.table}，wire ${row.wire_type}，宽度 ${row.width}，PDU ${start}`;
    });
  }

  function summarize(items) {
    const title = `请修正以下 ${items.length} 项后再创建：`;
    return {title, items, text: [title, ...items.map(item => item.message)].join('\n')};
  }

  function validateFromTypeRequest(draft) {
    const type = draft?.type || {};
    const items = [];
    if (!type.id) items.push({field: 'type_id', message: '请选择设备类型。'});
    const deviceId = String(draft?.deviceId ?? '').trim();
    if (deviceId && (!ID_PATTERN.test(deviceId) || deviceId.length > 128)) {
      items.push({field: 'device_id', message: '设备编号只能包含字母、数字、下划线、点和连字符，最长 128。'});
    }
    if (type.protocol === 'modbus') {
      const unitText = String(draft.unitId ?? '').trim();
      if (unitText !== '' && (!/^\d+$/.test(unitText) || Number(unitText) < 1 || Number(unitText) > 247)) {
        items.push({field: 'unit_id', message: 'Unit ID 必须是 1 到 247 的整数。'});
      }
      const addressText = String(draft.addressBase ?? '').trim();
      if (addressText !== '' && (!/^\d+$/.test(addressText) || Number(addressText) > 65535)) {
        items.push({field: 'address_base', message: '起始 PDU 地址必须是 0 到 65535 的整数。'});
      }
    }
    if (type.protocol === 'mqtt') {
      const topic = String(draft.topic ?? '');
      if (topic && (topic.includes('+') || topic.includes('#') || topic.includes('\0'))) {
        items.push({field: 'topic', message: 'topic 不能包含通配符 +、# 或空字符。'});
      }
      const intervalText = String(draft.publishIntervalMs ?? '').trim();
      if (intervalText !== '' && (!/^\d+$/.test(intervalText) || Number(intervalText) < 1000)) {
        items.push({field: 'publish_interval_ms', message: '发布周期必须为空或不小于 1000 ms。'});
      }
    }
    return items.length ? summarize(items) : {title: '', items, text: ''};
  }

  function buildFromTypeRequest(draft) {
    const type = draft.type;
    const deviceId = String(draft.deviceId ?? '').trim();
    const suggestion = suggestionForDeviceId(type, deviceId);
    const request = {
      type_id: type.id,
      enabled: draft.enabled !== false,
      desired_state: draft.desiredState === 'stopped' ? 'stopped' : 'running',
    };
    if (deviceId) request.device_id = deviceId;
    const name = String(draft.name ?? '').trim();
    if (draft.nameDirty && name && (!deviceId || name !== suggestion.name)) request.name = name;
    if (type.protocol === 'mqtt') {
      const connectionId = String(draft.connectionId ?? '').trim();
      if (connectionId) request.connection_id = connectionId;
      const topic = String(draft.topic ?? '');
      const suggestedTopic = suggestion.topic || '';
      if (draft.topicDirty && topic && (!deviceId || topic !== suggestedTopic)) request.topic = topic;
      if (draft.qos !== '' && draft.qos != null) request.qos = Number(draft.qos);
      request.retain = Boolean(draft.retain);
      if (draft.payloadMode) request.payload_mode = draft.payloadMode;
      const intervalText = String(draft.publishIntervalMs ?? '').trim();
      if (intervalText !== '') request.publish_interval_ms = Number(intervalText);
    } else if (type.protocol === 'modbus') {
      const unitText = String(draft.unitId ?? '').trim();
      if (unitText !== '') request.unit_id = Number(unitText);
      const addressText = String(draft.addressBase ?? '').trim();
      if (addressText !== '') request.address_base = Number(addressText);
    } else if (type.protocol === 'opcua') {
      const nodeIds = {};
      for (const [signalId, value] of Object.entries(draft.nodeIds || {})) {
        const text = String(value ?? '').trim();
        if (text) nodeIds[signalId] = text;
      }
      if (Object.keys(nodeIds).length) request.node_ids = nodeIds;
    }
    return request;
  }

  function stripValueError(message) {
    return String(message ?? '').replace(/^Value error,\s*/, '');
  }

  function fieldFromLoc(loc) {
    const names = new Set([
      'device_id', 'name', 'type_id', 'unit_id', 'topic', 'node_ids', 'connection_id',
      'address_base', 'qos', 'retain', 'payload_mode', 'publish_interval_ms',
      'enabled', 'desired_state',
    ]);
    for (let index = loc.length - 1; index >= 0; index -= 1) {
      const segment = String(loc[index]);
      if (segment === 'addresses' || segment === 'address' || segment === 'address_base') return 'address_base';
      if (segment === 'node_id' || segment === 'node_ids') return 'node_ids';
      if (names.has(segment)) return segment;
    }
    return null;
  }

  function fieldFromMessage(message) {
    if (message.includes('device already exists') || message.includes('device id space')) return 'device_id';
    if (message.includes('unit_id')) return 'unit_id';
    if (message.includes('topic')) return 'topic';
    if (message.includes('node_id') || message.includes('runtime node')) return 'node_ids';
    if (message.includes('connection_id')) return 'connection_id';
    if (message.includes('address')) return 'address_base';
    return null;
  }

  function errorsFromApi(payload) {
    const detail = payload?.detail;
    if (typeof detail === 'string') {
      if (detail.includes('configuration was rolled back')) {
        return summarize([{field: null, message: detail}]);
      }
      if (detail.includes('revision conflict')) {
        return summarize([{field: null, message: `${detail}\n请重新打开对话框以刷新建议`}]);
      }
      return summarize([{field: null, message: detail}]);
    }
    if (Array.isArray(detail)) {
      return summarize(detail.map(item => {
        const message = stripValueError(item?.msg);
        const loc = Array.isArray(item?.loc) ? item.loc : [];
        return {field: loc.length === 0 ? fieldFromMessage(message) : fieldFromLoc(loc), message};
      }));
    }
    return summarize([{field: null, message: detail == null ? '创建失败' : stripValueError(detail)}]);
  }

  function deviceTypeResultMessage(id, result) {
    const restarted = result?.restarted_adapters || [];
    let text = `已从类型创建 ${id}`;
    if (restarted.length) text += `，已重启适配器：${restarted.join('、')}。`;
    const pending = Array.isArray(result?.restart_required) ? result.restart_required : [];
    if (pending.length) text += `仍需重启后生效：${pending.join('、')}。`;
    return text;
  }

  return {
    buildFromTypeRequest,
    validateFromTypeRequest,
    errorsFromApi,
    suggestionForDeviceId,
    deviceTypeResultMessage,
    draftFromType,
    syncDraftForDeviceId,
    modbusAddressLines,
  };
});
