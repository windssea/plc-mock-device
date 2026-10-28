# mock 自动退出排查与当前托管运行

2026-10-26。恢复的是既有 `D:/work/code/PLCnext-Engineer/tests/nativegateway-soak.db`（schema3、完整性ok、配置revision5、50台设备）和 `mock-bootstrap.yaml`，没有新建测试库或改设备点表。

## 已知事实与结论边界

检查时11502/18080均未监听，旧 mock 进程已经不存在。旧 `PLCnext-Engineer/tmp/p05-p06/mock-mcp-20261026-uv.err.log` 记录了 Proactor/WinError10054，但 stdout 在该异常之后仍处理HTTP200，最后记录到本地13:18:44；没有最终退出码或退出栈。Windows事件中也没有找到能够确认该次退出原因的证据。因此**自动关闭的直接原因尚未确定**，不能把10054回调异常直接判为致命原因。

旧 `.venv` 指向已删除的 Python3.14 可执行文件；单独的启动日志明确报找不到解释器。这能解释后续重启失败，不能倒推先前运行实例为什么退出。

建立独立 `.venv-runtime`，固定CPython3.12.13并按 `requirements.lock` 安装。旧虚拟环境保留。新进程恢复后，状态接口50台、无配置错误；PLC只读观察250/250 GOOD，真实DATA/STORED及开发库收据哈希通过。没有把NTP、Hub或PLC性能测试作为mock健康证据。

## 托管与后续取证

`scripts/run_managed.py` 使用排他目录锁，启动前检查18080冲突，永不替换别人的服务；记录 supervisor/child PID、Python路径、父PID、独立stdout/stderr、退出码十进制及十六进制、运行时长和健康变化。进程非零退出后5/10秒退避，300秒内3次退出就停止；正常退出不重启。健康异常只记录，不因一次超时重启服务。

现有实例日志：`.local/mock-service/events.jsonl`、`state.json` 及每次child日志。没有安装登录任务或系统服务，也不承诺电脑重启后自动启动。后续再次关闭应先保存这些文件和Windows事件，再按具体exitCode/最后stderr定位；如果supervisor自身被结束，日志最后事件与父进程关系也需一并核对。

测试仅运行 `python -m unittest discover -s tests -p test_managed_supervisor.py -v`：正常退出不重启、非零退出退避和熔断、已有端口不替换、目录排他锁，共4项通过。测试注入子进程/端口/时钟，没有杀当前服务、访问PLC或创建数据库；没有执行会创建临时SQLite的全量pytest。

启动和停止说明见 [README](../README.md)。新守护只增加下一次退出的可诊断性，不表示已经证明并修复旧实例的未知退出原因。
