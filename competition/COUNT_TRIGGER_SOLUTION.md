# 赛题七：按新增来源条目数触发记忆处理

## 目标与接口

RuntimeConfig 新增 memory_source_count_threshold 与 topic_memory_source_count_threshold。默认 None 关闭数量触发，仅接受严格正整数，拒绝布尔值、字符串、浮点数、零和负数。

按 scope 和 family 的成功 cursor 之后的持久化 Source journal 身份计算待处理数。重复 capture 和 receipt 信任升级不产生新身份。lineage-only Source 参与计数，生成证据仍遵守既有过滤规则。

## 实现

- config.py：严格可选正整数配置。
- processing_discovery.py：按 family 成功游标统计持久化身份，只查询最多 threshold 条位置，不读取正文。
- composition.py：为 Memory 和 Topic Memory 装配各自门槛。
- artifact_processing.py：在原 supervisor 中增加有界分页的数量准入。每 250ms 唤醒检查，仅在门槛开启时启用。持久化 request 在同一带 leadership fence 的事务中接受，事务成功后才推进内存页游标并排队。

时间准入与数量准入互相独立，任意满足即可启动。显式 flush 沿用原请求路径。已排队、执行、等待重试或尚未确认的请求不会因数量扫描重复接受。

不改变原生 worker 和成功游标提交逻辑：Memory 每次仅完成原有单窗，再按成功后的剩余数量重新判断；Topic Memory 继续既有 wave。失败重试和重启恢复沿用持久化 intent 与 cursor，不消费失败输入，不重新计入已成功消费的历史。

## 完成步骤

1. 从官方 2026-competition 分支建立独立实现分支，保留冻结 commit 9db1753434162c0aa7c9f1189a1722e617880a52 的历史。
2. 阅读 Runtime 配置、journal 身份、scope/family cursor 与 supervisor 请求持久化规则。
3. 添加两个严格配置字段并接入内置处理族。
4. 在原有自动调度旁增加数量准入，保留时间 OR 与显式 flush 语义。
5. 使用官方离线 provider 和真实 SQLite/native worker 验证重复采集、lineage 计数、单窗与 wave、scope/family 隔离、时间触发、重启及写入失败恢复。
6. 运行原 supervisor 回归测试、Ruff 与类型检查。
7. 将实现推送到公开 fork 的独立分支，记录完整 40 位 commit SHA。
8. 用户在平台选择 processing-01-count-trigger，填写公开仓库 HTTPS URL 与完整 SHA 后提测。此步骤未执行。

## 复现

安装官方锁定环境（Python 3.12）：

```bash
make -C competition install
make -C competition test-count-trigger
```

若运行环境设置了 SOCKS 代理但未安装 socksio，本地离线 provider 自测可清除该命令的 HTTP_PROXY、HTTPS_PROXY、ALL_PROXY 及对应小写环境变量；不修改产品依赖和官方测试。

判题只加载 src/，不依赖此文档或产品附带测试。公开自测不等同于平台最终通过。语雀题面标注 10 分，当前官方比赛分支已改为 20 分，实际以平台为准。

## 验证范围与限制

原 supervisor/config 回归测试：47 项通过。四个修改源码文件的 Ruff 检查、格式检查及 ty 类型检查通过。

全仓 pre-commit 已执行，但未整体通过：官方 competition 中其他题的接口缺失及类型问题等触发 214 条诊断；全仓格式 hook 也改动了既有公开测试及样例。已还原这些与本题无关的自动格式改动，未修改组织方判题代码或输入。

发布目标：公开仓库 `197388tt-source/powercontext-fork`，独立分支 `challenge-07-count-trigger`。平台提测由用户自行执行。

官方 processing-01-count-trigger 公开验收：17 项全部通过，耗时 239.53 秒。包括真实 native worker 的 Memory/Topic Memory 结果、精确引用读取、lineage-only 来源的两种写入顺序，以及两族 SQLite 实际持久化故障后的重启恢复。测试未跳过任何项，未替换官方测试或输入。
