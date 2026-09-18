# IoT 模拟器当前设备与连接清单

本文档记录当前正在运行的 IoT 模拟器配置、协议连接、设备、数据点和协议映射。内容读取自 SQLite 运行配置和管理 API，不以启动 YAML 中的旧默认值为准。

> 生成时间：2026-07-17 10:28（Asia/Shanghai）  
> 配置版本：v1 / revision 12  
> SQLite schema：v3  
> 当前设备：7 台  
> 当前数据点：11 个

## 1. 运行与持久化

| 项目 | 当前值 |
|---|---|
| 管理页面 | `http://127.0.0.1:8081` |
| 管理 API | `http://127.0.0.1:8081/api/v1` |
| SQLite 数据库 | `data/iot-simulator.db` |
| 启动 YAML | `config/example.yaml` |
| 全局随机种子 | `42` |
| 配置 revision | `12` |
| SQLite schema version | `3` |
| SQLite integrity check | `ok` |
| 数据库最后更新时间 | `2026-07-17T02:19:52.705845+00:00` |

启动命令：

```powershell
.\.venv\Scripts\iot-sim.exe serve --config config\example.yaml --database data\iot-simulator.db
```

持久化规则：

- `config/example.yaml` 只在数据库首次初始化时提供默认配置。
- 数据库初始化完成后，设备、数据点、协议连接和端口都从 SQLite 读取。
- 当前 Web 端口是 SQLite 中的 `8081`；YAML 中保留的 `8080` 不会覆盖它。
- 页面保存配置时更新 SQLite，不改写启动 YAML。
- MQTT 密码按要求保存在 SQLite 中，但管理 API、页面、校验响应和 YAML 导出统一显示为 `********`。
- 数据库文件及其备份包含 MQTT 密码，应限制文件访问权限。

迁移前备份：

```text
data/backups/iot-simulator-before-mqtt-v3-20260717-092935.db
```

## 2. 协议适配器状态

| 适配器 | 状态 | 已连接 | 最后错误 | 丢弃消息数 |
|---|---|---:|---|---:|
| OPC UA | `running` | 是 | 无 | 0 |
| MQTT | `running` | 是 | 无 | 0 |
| Modbus TCP | `running` | 是 | 无 | 0 |

## 3. 全局协议参数

### 3.1 Web 管理端

| 参数 | 值 |
|---|---|
| Host | `127.0.0.1` |
| Port | `8081` |

### 3.2 OPC UA Server

| 参数 | 值 |
|---|---|
| Enabled | `true` |
| Host | `0.0.0.0` |
| Port | `4840` |
| Endpoint path | `/iot-simulator/` |
| Endpoint | `opc.tcp://0.0.0.0:4840/iot-simulator/` |
| 本机客户端连接地址 | `opc.tcp://localhost:4840/iot-simulator/` |
| Server name | `PLCnext IoT Simulator` |
| Application URI | `urn:plcnext:iot-simulator` |
| Namespace URI | `urn:plcnext:iot-simulator:devices` |
| 访问方式 | 匿名、无加密、节点只读 |

### 3.3 MQTT 连接

当前只有一个规范化 MQTT 连接配置，4 台 MQTT 设备共用该连接。后续可在页面新增其他连接，并通过设备的 `connection_id` 进行选择。

| 参数 | 值 |
|---|---|
| Connection ID | `default` |
| Name | `Local MQTT` |
| Enabled | `true` |
| Broker host | `localhost` |
| Broker port | `1883` |
| Transport | `tcp` |
| MQTT version | `3.1.1` |
| Client ID | `plcnext-iot-simulator-local` |
| Keepalive | `60` 秒 |
| Username | `mqtt` |
| Password | 已保存在 SQLite，文档不记录明文 |
| Password environment variable | 未使用 |
| TLS | 未启用 |
| 最小重连间隔 | `1` 秒 |
| 最大重连间隔 | `30` 秒 |
| 最大在途消息数 | `20` |
| 最大队列消息数 | `1000` |

### 3.4 Modbus TCP Server

| 参数 | 值 |
|---|---|
| Enabled | `true` |
| Host | `0.0.0.0` |
| Port | `1502` |
| 本机客户端连接地址 | `localhost:1502` |
| 访问方式 | 遥测只读 |

## 4. 设备总览

| 设备编号 | 名称 | 协议 | 默认数据周期 | 期望状态 | 实际状态 | 数据点数 |
|---|---|---|---:|---|---|---:|
| `opcua-001` | OPC UA 示例设备 | OPC UA | 1000 ms | `running` | `running` | 1 |
| `mqtt-001` | MQTT 示例设备 | MQTT | 1000 ms | `running` | `running` | 1 |
| `modbus-001` | Modbus 示例设备 | Modbus TCP | 1000 ms | `running` | `running` | 1 |
| `test` | test2 | OPC UA | 1000 ms | `running` | `running` | 1 |
| `mqtt-temp-001` | Temperature Sensor 001 | MQTT | 1000 ms | `running` | `running` | 2 |
| `mqtt-power-001` | Power Meter 001 | MQTT | 1000 ms | `running` | `running` | 3 |
| `mqtt-tank-001` | Tank Sensor 001 | MQTT | 1000 ms | `running` | `running` | 2 |

全部设备均为 `enabled=true`，当前没有设备错误。

## 5. 设备详细配置

### 5.1 `opcua-001` — OPC UA 示例设备

设备参数：

| 参数 | 值 |
|---|---|
| Protocol | `opcua` |
| Enabled | `true` |
| Desired state | `running` |
| Default interval | `1000 ms` |
| Object name | 未单独配置，使用默认设备对象 |

数据点：

| 点位 | 名称 | 单位 | 类型 | 周期 | 生成器 | OPC UA 映射 |
|---|---|---|---|---:|---|---|
| `temperature` | 温度 | `°C` | `float32` | 1000 ms | `random_range`：18～35，保留 2 位小数 | NodeId=`opcua-001.temperature`；BrowseName=`Temperature`；DisplayName=`Temperature`；DataType=跟随 `float32` |

### 5.2 `mqtt-001` — MQTT 示例设备

设备参数：

| 参数 | 值 |
|---|---|
| Connection ID | `default` |
| Topic | `devices/mqtt-001/telemetry` |
| QoS | `0` |
| Retain | `false` |
| Publish interval | 未单独设置，使用设备默认 `1000 ms` |
| Payload mode | `nested` |

数据点：

| 点位 | 名称 | 单位 | 类型 | 周期 | 生成器 | MQTT 字段 |
|---|---|---|---|---:|---|---|
| `counter` | 计数器 | `count` | `uint32` | 1000 ms | `increment`：初始 0，范围 0～10000，步长 1～3，越界 `wrap` | `counter` |

报文结构：

```json
{
  "deviceId": "mqtt-001",
  "timestamp": "UTC ISO-8601",
  "values": {
    "counter": 123
  }
}
```

### 5.3 `modbus-001` — Modbus 示例设备

设备参数：

| 参数 | 值 |
|---|---|
| Protocol | `modbus` |
| Unit ID | `1` |
| Enabled | `true` |
| Desired state | `running` |
| Default interval | `1000 ms` |

数据点：

| 点位 | 名称 | 单位 | 值类型 | 生成器 | 表 | PDU 地址 | Wire type | 寄存器长度 | Scale / Offset | Byte / Word order |
|---|---|---|---|---|---|---:|---|---:|---|---|
| `pressure` | 压力 | `bar` | `float32` | `random_range`：1～10，保留 2 位小数 | `input_register` | `0` | `float32` | `2` | `1.0 / 0.0` | `big / big` |

该点占用 Input Register 地址 `0～1`，客户端使用 Unit ID `1` 读取。

### 5.4 `test` — test2

设备参数：

| 参数 | 值 |
|---|---|
| Protocol | `opcua` |
| Enabled | `true` |
| Desired state | `running` |
| Default interval | `1000 ms` |
| Object name | 未单独配置，使用默认设备对象 |

数据点：

| 点位 | 名称 | 单位 | 类型 | 周期 | 生成器 | OPC UA 映射 |
|---|---|---|---|---:|---|---|
| `test` | test | `V` | `float32` | 1000 ms | `increment`：初始 0，范围 0～100，步长 1～2，越界 `wrap` | NodeId=`test`；BrowseName/DisplayName 未单独配置 |

### 5.5 `mqtt-temp-001` — Temperature Sensor 001

设备参数：

| 参数 | 值 |
|---|---|
| Connection ID | `default` |
| Topic | `iot-simulator/test/mqtt-temp-001/telemetry` |
| QoS | `1` |
| Retain | `false` |
| Publish interval | `1000 ms` |
| Payload mode | `nested` |

数据点：

| 点位 | 名称 | 单位 | 类型 | 周期 | 生成器 | MQTT 字段 |
|---|---|---|---|---:|---|---|
| `temperature` | Temperature | `C` | `float32` | 1000 ms | `random_range`：18～32，保留 2 位小数 | `temperature` |
| `humidity` | Humidity | `%` | `uint16` | 1000 ms | `random_range`：35～85，整数 | `humidity` |

报文结构：

```json
{
  "deviceId": "mqtt-temp-001",
  "timestamp": "UTC ISO-8601",
  "values": {
    "temperature": 23.5,
    "humidity": 60
  }
}
```

### 5.6 `mqtt-power-001` — Power Meter 001

设备参数：

| 参数 | 值 |
|---|---|
| Connection ID | `default` |
| Topic | `iot-simulator/test/mqtt-power-001/telemetry` |
| QoS | `1` |
| Retain | `false` |
| Publish interval | `2000 ms` |
| Payload mode | `flat` |

数据点：

| 点位 | 名称 | 单位 | 类型 | 周期 | 生成器 | MQTT 字段 |
|---|---|---|---|---:|---|---|
| `voltage` | Voltage | `V` | `float32` | 1000 ms | `random_range`：218～242，保留 2 位小数 | `voltage` |
| `current` | Current | `A` | `float32` | 1000 ms | `random_range`：1～15，保留 2 位小数 | `current` |
| `energy` | Energy | `kWh` | `float64` | 1000 ms | `increment`：初始 1000，范围 1000～9999，步长 0.1～0.5，越界 `wrap` | `energy` |

报文结构：

```json
{
  "deviceId": "mqtt-power-001",
  "timestamp": "UTC ISO-8601",
  "voltage": 230.5,
  "current": 8.2,
  "energy": 1001.3
}
```

### 5.7 `mqtt-tank-001` — Tank Sensor 001

设备参数：

| 参数 | 值 |
|---|---|
| Connection ID | `default` |
| Topic | `iot-simulator/test/mqtt-tank-001/telemetry` |
| QoS | `0` |
| Retain | `false` |
| Publish interval | `1000 ms` |
| Payload mode | `values` |

数据点：

| 点位 | 名称 | 单位 | 类型 | 周期 | 生成器 | MQTT 字段 |
|---|---|---|---|---:|---|---|
| `level` | Liquid Level | `%` | `float32` | 1000 ms | `increment`：初始 20，范围 0～100，步长 0.5～2，越界 `wrap` | `level` |
| `pressure` | Pressure | `kPa` | `float32` | 1000 ms | `random_range`：95～110，保留 2 位小数 | `pressure` |

报文结构：

```json
{
  "level": 42.5,
  "pressure": 102.3
}
```

## 6. 协议索引

### 6.1 MQTT Topic 索引

| 设备 | Connection ID | Topic | QoS | Retain | 发布周期 | Payload mode |
|---|---|---|---:|---:|---:|---|
| `mqtt-001` | `default` | `devices/mqtt-001/telemetry` | 0 | false | 1000 ms | `nested` |
| `mqtt-temp-001` | `default` | `iot-simulator/test/mqtt-temp-001/telemetry` | 1 | false | 1000 ms | `nested` |
| `mqtt-power-001` | `default` | `iot-simulator/test/mqtt-power-001/telemetry` | 1 | false | 2000 ms | `flat` |
| `mqtt-tank-001` | `default` | `iot-simulator/test/mqtt-tank-001/telemetry` | 0 | false | 1000 ms | `values` |

测试设备订阅通配符：

```text
iot-simulator/test/#
```

### 6.2 OPC UA NodeId 索引

| 设备 | 点位 | NodeId | 类型 | 单位 |
|---|---|---|---|---|
| `opcua-001` | `temperature` | `opcua-001.temperature` | `float32` | `°C` |
| `test` | `test` | `test` | `float32` | `V` |

### 6.3 Modbus 地址索引

| 设备 | Unit ID | 表 | 地址范围 | Wire type | Byte order | Word order |
|---|---:|---|---|---|---|---|
| `modbus-001` | 1 | `input_register` | `0～1` | `float32` | `big` | `big` |

## 7. 运行值快照

以下数值采集于 2026-07-17 10:28:42（Asia/Shanghai）。模拟数据会持续变化，此表只用于确认各设备正在产生数据。

| 设备 | 最新值快照 |
|---|---|
| `opcua-001` | `temperature=19.1` |
| `mqtt-001` | `counter=1008` |
| `modbus-001` | `pressure=4.94` |
| `test` | `test=66.35474503142575` |
| `mqtt-temp-001` | `temperature=21.82, humidity=84` |
| `mqtt-power-001` | `voltage=234.81, current=8.2, energy=1152.633323124053` |
| `mqtt-tank-001` | `level=61.68349231061338, pressure=96.47` |

所有样本质量均为 `Good`，所有设备实际状态均为 `running`。

## 8. SQLite 对象关系

当前配置使用标准关系表持久化，不把设备对象整体保存为 JSON。主要表如下：

| 对象 | 表 |
|---|---|
| 应用元数据与 revision | `app_metadata` |
| Web 参数 | `web_settings` |
| OPC UA 全局参数 | `opcua_settings` |
| MQTT 默认兼容参数 | `mqtt_settings` |
| MQTT 多连接 | `mqtt_connections` |
| Modbus 全局参数 | `modbus_settings` |
| 设备基础信息 | `devices` |
| OPC UA / MQTT / Modbus 设备参数 | `opcua_device_configs`、`mqtt_device_configs`、`modbus_device_configs` |
| 数据点基础信息 | `signals` |
| 递增 / 区间随机生成器 | `increment_generators`、`random_range_generators` |
| OPC UA / MQTT / Modbus 点位映射 | `opcua_signal_mappings`、`mqtt_signal_mappings`、`modbus_signal_mappings` |

## 9. 配置核对备注

- 当前 4 台 MQTT 设备都引用 `default` 连接；系统已支持多连接，但尚未给不同设备配置不同 Broker。
- 所有数据点的 `interval_ms` 当前都为 `null`，所以统一继承设备默认的 `1000 ms` 数据生成周期。
- `mqtt-power-001` 每 `1000 ms` 产生一次数据、每 `2000 ms` 发布一次，报文使用发布时的最新快照。
- `opcua-001` 的 `data_type` 为空，`test` 的 BrowseName、DisplayName、DataType 均为空；运行时会按点位配置推断默认值。
- OPC UA 温度单位为 `°C`，MQTT 温度单位为 `C`，两者目前存在轻微命名差异。
- `test`、`level`、`energy` 是浮点递增值且未配置输出精度，运行值或 MQTT JSON 中可能出现较长小数。
- 数据库、适配器、设备数和数据点数交叉核对一致，未发现运行错误或丢弃消息。

## 10. 管理说明

- 协议连接：进入“协议与配置”页面管理。
- MQTT 多连接：在“MQTT 连接”区域新增或编辑，设备通过 `connection_id` 选择。
- 设备与数据点：进入“设备管理”页面新增、编辑、复制、暂停或删除。
- 修改 Web 端口后必须重启模拟器进程；修改协议连接时对应 adapter 会自动重载。
- MQTT 密码留空保存表示保留原密码；输入新密码表示替换；勾选清除才会删除 SQLite 中的密码。
- 不要通过直接修改 YAML 来更新已经初始化的运行配置。
