# 赛题九：来源撤回后的记忆停用与恢复

## 任务与完成步骤

1. 以官方 `2026-competition` 分支为基线，保留冻结版本 `9db1753434162c0aa7c9f1189a1722e617880a52` 的祖先关系。
2. 新增 `powercontext.builtin.runtime.source_lifecycle`，严格验证事件字段、类型、正整数版本和序号，拒绝额外字段。
3. 为每个 scope 注册 Source、Memory 和 Topic Memory 的原始引用，检查引用可读性、唯一身份、父节点和 all/any 策略。
4. 利用“父节点先注册、注册后不改图”的约束建立拓扑顺序，注册时立即计算派生节点资格。
5. 撤回和恢复 Source 时检查生命周期版本及事件序号，按拓扑顺序传播全部后代状态。
6. 调用原生 Memory 停用/恢复操作更新 manifest 和检索状态，保留正文版本；Topic 搜索与 Context 排除不可用节点。
7. 区分 generation 与 audit：生成拒绝不可用节点，审计始终返回注册时原始引用。
8. 在运行时数据库保存图、事件和目标状态。先保存目标状态再传播，失败后重放事件继续投影；原生状态相同的操作不产生新修订。
9. 在 Python 3.12 和官方固定依赖环境运行公开验收，并补测中断重启重试、成功重放、注册校验和 scope 隔离。
10. 将代码、测试和说明提交到公开 fork 的 `challenge-09-source-lifecycle` 分支，交付仓库 HTTPS URL 和完整 40 位提交 SHA。
11. 用户自行在平台选择赛题九，填写仓库 URL 和提交 SHA 后提测。本次不执行平台提测。

## 设计与适用范围

`runtime.lifecycle.for_scope(scope_id)` 提供 register_source、register_memory、register_topic、apply、resolve 异步接口。证据图按 scope 保存为数据库 JSON 记录；每次事件对该 scope 的图做一次拓扑求值，工作量 O(V+E)。原生 Memory 写入前读取实际状态，因此部分成功后重试可以补齐剩余变更。历史 MemoryCitation、ArtifactRef 和 SourceRef 不被替换。

Topic 的资格在运行时原生 Topic 搜索入口过滤，Context 共用该入口；正文 Artifact 及其精确版本仍然保留。过滤发生在搜索候选排序之后，因此包含大量不可用 Topic 的通用大规模场景可能返回少于请求 limit 的结果；题目约定每 scope 4 个 Topic，此行为不影响公开验收。

按题目约定每 scope 单写者、注册后不改图，不支持外部改写绑定 Memory 的正文版本。事件失败后需重试完成原生投影；异常发生到重试完成之间可能存在部分投影状态，不承诺跨多个 Memory 写入的原子可见性。

## 自测

```bash
make -C competition install
make -C competition test-lifecycle
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 competition/.venv/bin/python -m pytest -q -c competition/pytest.ini --confcutdir=tests tests/test_source_lifecycle.py
```

官方公开测试 4 项通过；自有回归测试 2 项通过。自有测试包含：第二条原生 Memory 停用写入故障、关闭并重开数据库后重放补齐、成功重放无新修订、同序号冲突拒绝、scope 隔离和无效依赖拒绝。公开验收通过不代表隐藏必测组保证通过；本题全部必测通过得 20 分，否则为 0 分。
