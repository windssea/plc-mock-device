# 2026-10-29 mock 停止与恢复

08:48 后联调停止时，11502/18080无监听，旧 supervisor 与服务子进程均不存在；旧 `mainline-soak-20261028/events.jsonl` 没有 child_exited。不能据旧10054日志宣称本次mock崩溃。

Windows Application事件37016/37017显示Codex sandbox service在08:48:45被要求停止并停止，37019显示08:48:48重新运行。时间与本机Hub、Python、mock消失吻合，支持宿主生命周期连带结束进程的解释；没有逐进程退出码。Windows最后启动是10月21日。

09:56恢复原有 `D:/work/code/PLCnext-Engineer/tests/nativegateway-soak.db`、`mock-bootstrap.yaml`，使用 `.venv-runtime/Scripts/python.exe scripts/run_managed.py`；state-dir为 `.local/mainline-recovery-20261029`。没有建立新的SQLite。新记录确认配置revision5、50台，11502/18080恢复监听，HTTP config_error为空。新supervisor日志PID66872，实际端口进程PID45488（虚拟环境解释器存在父子进程）。

PLC仍Running、boot84，并非设备工程停了；Hub重新启动时漏掉授时响应开关，是mock恢复后仍没有心跳/数据的另一原因。Hub已将dev单权威选择写入配置；普通dev重启后双方恢复250/250 GOOD及真实入库。

当前托管并未安装系统服务/登录自启，supervisor自身被宿主结束时不能保证子进程继续运行。退出日志和有界重启只覆盖supervisor仍存活的子进程退出；不宣称所有自动关闭场景已根治。

本轮未修改mock源码或点表；仓库其他未提交UI/设备类型改动不属于本次恢复。详细业务测试见 [Hub恢复与验证记录](../../PLCnext-Hub-Backend/docs/verification/2026-10-29-communication-recovery.md)。
