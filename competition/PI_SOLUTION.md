# Pi 会话分支采集与断点恢复

## 任务与实现

在 `src/powercontext/sources/pi_session.py` 实现 PiSessionInput、PiEventInput、parse_pi_session、PI_EVENT_SOURCE_DEFINITION 和 PiSessionConnector。

1. UTF-8/BOM JSONL 全文解析，校验 v3 session header、唯一 ID 和先出现的 parent；沿 leaf 祖先链输出可见事件。
2. 保留字符串、text、toolCall、toolResult、bashExecution、custom_message 和两类摘要；跳过 thinking/image/控制节点，但保留控制节点的父子连接。
3. Source 为 CAPTURED，保存完整原始 record；基于 session/event ID 的稳定 SHA-256 identity；标准 projection 包含全文和五个元数据字段。
4. connector 完整解析后校验所有已导入事件的原始记录摘要，再提交新增事件；由 ConnectorLifecycle 持久化 checkpoint，失败不推进；切分支、追加、重启均依据 checkpoint 去重。
5. 不新增依赖，不执行会话中的命令，不访问在线模型或网络，不截断大工具正文。

## 验证与复现

要求 Linux x86_64、Python 3.12、Git、uv。在仓库根目录：

```bash
make -C competition install
make -C competition check-baseline
make -C competition test-pi
make -C competition test-csv
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 competition/.venv/bin/python -m pytest -q -c competition/pytest.ini --confcutdir=tests tests/test_pi_session.py tests/test_csv_rows.py tests/test_sources.py tests/test_source_observations.py
```

Pi 官方公开测试 15 passed，CSV 公开测试 17 passed。补充测试覆盖大工具结果无截断回读、写入失败不推进 checkpoint、非法父节点，以及现有 Source 回归。公开测试通过不代表最终隐藏测试或比赛成绩。

## 提测步骤

1. 使用参赛仓库 `https://github.com/197388tt-source/powercontext-fork`。
2. 选择分支 `competition/pi-session-history`，其中也保留 CSV 实现。
3. 使用该分支实际完整 40 位 commit SHA；可运行 `git rev-parse HEAD` 获得。
4. 平台选择赛题三，填仓库 HTTPS URL、分支和 commit SHA。若没有分支输入，核实平台究竟读取哪个分支；不要提交旧默认分支 archive/powermem。
5. 私有仓库需要授权评测平台读取；此实现不改变仓库可见性。
6. 启动提测，查看反馈。网页 10 分与当前公开包 20 分有差异，以比赛平台为准；题面规定必测全过才得分。

不需要安装 Pi 或配置 LLM API Key：本题处理离线记录，公开测试自带样例。
