# PLCnext IoT 虚拟设备模拟器

跨项目协作入口：[六项目开发分工与全链路联调手册](../PLCnext-Hub-Backend/docs/architecture/six-project-integration-guide.md)。

一个配置驱动、单进程的工业 IoT 数据模拟器。MQTT 设备通过共享客户端发布 JSON；OPC UA
设备暴露只读节点；Modbus TCP 设备通过共享端口和 Unit ID 暴露只读数据点。所有值由同一个
生成与调度内核产生，并可通过本地 Web 控制台管理。

## 快速开始

要求 Python 3.12+。项目依赖已精确锁定；开发机可使用虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
iot-sim validate --config config/example.yaml
iot-sim serve --config config/example.yaml --database data/iot-simulator.db
```

浏览器打开 <http://127.0.0.1:8080>。默认协议端口为 OPC UA `4840`、Modbus TCP
`1502`；示例 MQTT broker 名为 `mosquitto`，本机直接运行时请在管理页改为实际 broker 地址。
全新数据库首次启动时设备列表为空，请在“设备管理”页面新增设备和数据点。

Docker 运行模拟器：

```powershell
docker compose up --build simulator
```

连同仅用于演示的匿名 Mosquitto 一起启动：

```powershell
docker compose --profile demo up --build
```

Compose 使用命名卷 `simulator-data` 持久化 SQLite 数据库，并将示例 YAML 以只读方式挂载。
容器重建后设备配置仍会保留；如需从示例重新初始化，请显式删除该命名卷。

## 配置结构

运行时配置只以 SQLite 持久化，`version: 1` 表示业务配置 schema，`config_revision` 用于并发
修改检测。启动 YAML 示例见 [`config/example.yaml`](config/example.yaml)，它只允许
`version`、`random_seed`、`web`、`opcua`、`mqtt`、`modbus`，用于空数据库的首次默认值复制。
启动 YAML 严禁包含 `config_revision`、`devices` 或点位；首个数据库配置固定为 revision 0、空设备。
设备、点位、协议参数的后续修改和 `desired_state` 全部通过页面/API 写入 SQLite。

SQLite 数据库 schema 为 `user_version=3`，使用 metadata、四类全局 settings、设备、协议配置、
点位、生成器和 mapping 子表组成的标准关系图，不保存整配置 JSON/blob。设备和点位显式保存
顺序；无界 seed 与生成器/缩放数值按独立 canonical number token 文本无损保存，包括大整数和
`-0.0`。数据库启用 WAL、`synchronous=FULL`、foreign keys 和 5 秒 busy timeout；完整关系图在
单个事务中替换，并用乐观 revision 检测并发。可通过 `--database` 或
`IOT_SIM_DATABASE` 指定数据库路径，通过 `--config` 或 `IOT_SIM_CONFIG` 指定首次引导 YAML。
首次并发启动会对 SQLite busy/locked 做有界重试，多个进程最终读取同一份引导配置。
数据库一旦存在配置记录，后续启动完全忽略 YAML，旧数据库升级不会因旁边保留旧格式 YAML 而
改变。管理 API 的修改也不会回写启动 YAML。

旧 `user_version=1` 单文档数据库会在首次打开时原子迁移到关系 schema v3（先建成 v2 关系图，再补上具名 MQTT 连接和设备 `connection_id`）；成功后才删除旧表并提交，失败则完整保留 v1 数据且不会回退启动 YAML。

- 全局 `opcua`：监听地址、端口、endpoint path、application/namespace URI。
- 全局 `mqtt`：broker、共享 client ID、协议版本、keepalive、重连和有界队列。密码通过
  `password_env` 指定的环境变量读取。
- 全局 `modbus`：监听地址和端口。
- SQLite 完整配置中的 `devices[]`：设备编号、单一协议、默认周期、期望状态和点位。
- SQLite 完整配置中的 `signals[]`：数值类型、可选点位周期、生成器及协议 binding。

最小周期为 1000 ms。递增生成器支持随机 step 区间及 `wrap`/`clamp`；随机区间生成器支持
整数、浮点精度和 bool。全局 seed 会按设备/点位稳定派生，便于复现。

Modbus 地址为零基 PDU 地址。`int32/uint32/float32` 占两个寄存器，`float64` 占四个；加载时
会拒绝地址重叠、越界和错误的 bit/register 类型组合。

## 管理 API

OpenAPI 页面位于 <http://127.0.0.1:8080/docs>，主要接口：

- `GET/PUT /api/v1/config`、`POST /api/v1/config/validate`
- `GET /api/v1/config/export`、`POST /api/v1/config/import`
- `GET/POST /api/v1/devices`
- `GET /api/v1/device-types`、`POST /api/v1/devices/from-type`
- `GET/PUT/DELETE /api/v1/devices/{deviceId}`
- `POST /api/v1/devices/{deviceId}/actions/start|stop`
- `GET /api/v1/status`、`GET /api/v1/devices/{deviceId}/status`

`GET /api/v1/device-types` 返回随版本发布的设备类型目录，以及按当前配置算出的建议编号和地址。`POST /api/v1/devices/from-type` 把所选类型展开成一台普通设备，再走与 `POST /api/v1/devices` 相同的写入路径。类型本身不入库，之后改目录不会改已有设备。

配置写入使用异步串行锁、完整 schema/冲突校验和 SQLite 事务。协议应用失败时按预期 revision
恢复上一版数据库配置和运行态，失败尝试不会消耗 revision。Modbus 拓扑或共享端点变化会重启整个 Modbus adapter；接口以 HTTP 202
返回受影响的 adapter 列表。Web 监听地址或端口会持久化，但需要重启进程后生效，响应中的
`restart_required` 会包含 `web`。

已有运行配置时，YAML 导入内容必须携带当前 `config_revision`；旧 revision 会返回 HTTP 409，
不会被自动改写成最新 revision。仅在数据库尚无配置的恢复模式下，导入 revision 会重置为 0
并创建首个数据库 revision。导入/导出 YAML 是完整 `AppConfig` 配置备份，可携带设备和点位；
它与自动启动 YAML 是两个不同边界，不会被自动加载。

暂停设备只停止生成和 MQTT 发布，OPC UA/Modbus 仍返回最后值；恢复时不会追补暂停期间周期。

## 协议语义和限制

- OPC UA：匿名、NoSecurity、只读；客户端可以 Browse、Read 和 Subscription。
- MQTT：一个共享连接，设备身份位于 topic/payload；不模拟设备级 Session、LWT 或独立认证。
- Modbus：仅 TCP，支持四类表；所有写功能码返回 Illegal Function。
- broker 离线时 MQTT 后台重连，只保留最新快照；队列满会丢弃并累计状态计数。
- v1 面向隔离测试网，不提供 Web 认证、TLS、证书信任、RTU/ASCII 或亚秒级承诺。

首次启动 YAML 损坏、含 `devices`/`config_revision` 或其他多余字段时，协议运行层不会启动，
管理服务会进入恢复模式；可通过“导入配置备份（YAML）”写入一份完整配置。若 SQLite 已有配置，
启动 YAML 即使损坏或被修改也不影响启动；反之，SQLite 文件损坏时不会静默回退到 YAML。

## 验证

```powershell
pytest -q
```

100 台设备、1000 点、1 秒周期的 30 分钟验收脚本：

```powershell
python scripts/load_test.py
```

调试时可用 `--duration 10` 做短时冒烟；脚本检查点位覆盖、调度间隔 p95 偏差和后半程内存增长。

测试覆盖配置校验与原子写入、生成器、Modbus 编解码和只读行为、OPC UA/Modbus 真实客户端
冒烟、REST CRUD、状态切换及配置回滚。第三方许可证见
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

## 开发联调的固定运行环境与退出取证

2026-10-26 当前联调用的是既有 `PLCnext-Engineer/tests/nativegateway-soak.db`，不是默认数据目录。旧 `.venv` 引用了已删除的解释器；当前采用 CPython3.12.13 的 `.venv-runtime` 和 `requirements.lock`。退出原因及4项无数据库隔离检查见 [排查记录](docs/managed-service-20261026.md)。

已有托管实例时不要再次启动。需启动时，在仓库目录运行（已有DB/配置必须存在）：

```powershell
Start-Process -FilePath "$PWD/.venv-runtime/Scripts/python.exe" -WindowStyle Hidden -WorkingDirectory $PWD -ArgumentList @('scripts/run_managed.py', '--database', 'D:/work/code/PLCnext-Engineer/tests/nativegateway-soak.db', '--config', 'D:/work/code/PLCnext-Engineer/tests/mock-bootstrap.yaml', '--state-dir', '.local/mock-service')
```

`state.json` 是最后一次状态变化，不是实时心跳；实时健康查看 `http://127.0.0.1:18080/api/v1/status`。日志在 `.local/mock-service/`，包含子进程stdout/stderr和实际退出码；正常退出不重启，300秒内3次异常退出就停止。不会自动安装Windows服务、登录启动项或修改配置数据库。

操作员停止标记是 `.local/mock-service/stop.request`；停止后应确认11502/18080均不再监听。Windows虚拟环境可能有解释器子进程，若停止后还有监听，应按日志核对本实例子进程身份后处理，不能结束其他Python服务。重新启动前由操作员显式移除停止标记。当前自动退出原因仍待下次退出日志确认，不把增加守护当作已经找到旧实例的死因。
