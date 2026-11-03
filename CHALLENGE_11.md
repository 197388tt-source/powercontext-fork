# 赛题十一：seekdb 混合检索的单 SQL 候选查询

## 任务与交付

题目编号 `retrieval-02-hybrid-search`，总分 20 分，全部要求通过才得分。基于官方 `2026-competition` 分支开发，冻结基线为 `9db1753434162c0aa7c9f1189a1722e617880a52`。

交付仓库：<https://github.com/197388tt-source/powercontext-fork>。实现分支：`challenge-11-hybrid-search`。本分支只包含本题改动，不包含其他题分支的实现。

## 完整实施步骤

1. 从官方测评分支检出独立分支，保留冻结基线的提交历史，确认公开接口和固定依赖。
2. 阅读 `CompositeMemoryIndex`、OceanBase 的全文/向量索引、Memory backend 的当前 head 校验、融合及精确正文读取路径。
3. 新增 seekdb 专用混合索引：全文和向量分别作为带独立排序与 LIMIT 的子查询，通过 `UNION ALL` 组成一个 statement。
4. 两路都在数据库内施加 scope、Memory artifact、标签条件及候选上限；查询和向量等外部输入全部绑定参数。
5. 保留通道标识，恢复成 `MemorySearchChannels` 后沿用现有融合与去重；正文从对应 entry version 读取，引用使用已验证的请求 head，不要求未改动投影的旧 revision 等于新 head。
6. seekdb 配置且有 embedding 模型时启用新索引。FTS-only、vector-only、无 embedding 和其他数据库路径继续使用原有策略。沿用现有投影追加、删除、恢复及 embedding 完整性检查。
7. 执行官方公开测试，检查单 SQL、真实索引计划、跨 scope 隔离、历史 head 拒绝、增量投影、标签、修订、停用/恢复与单通道。
8. 运行附加真实数据库回归：384 维向量、同库双查询对照、每通道候选数限制、Memory artifact 隔离、标签 all/any、重复查询与带标签 EXPLAIN。
9. 运行静态检查和相关回归，提交源码、测试和说明；确认公开仓库及完整 40 位 SHA 可访问。
10. 用户自行决定何时在比赛平台填写仓库 URL、实际分支和完整 SHA。本次工作不执行平台提测。

## 实现入口

- `src/powercontext/builtin/persistence/seekdb/memory_index.py`：单 statement 候选检索。
- `src/powercontext/builtin/runtime/composition.py`：seekdb 运行时组装。
- `tests/builtin/persistence/test_seekdb_hybrid_candidates.py`：同库候选对照及带标签原生索引检查。
- `.github/workflows/challenge-11-hybrid.yml`：Linux 上的官方公开测试与附加回归，不调用比赛平台。

## 复现

要求 Linux x86_64、Python 3.12、uv。固定依赖来自未修改的 `competition/requirements.txt`，包括 `pylibseekdb==1.3.0.post4`，不需要模型密钥。

```bash
git clone --branch challenge-11-hybrid-search https://github.com/197388tt-source/powercontext-fork.git
cd powercontext-fork
make -C competition install
make -C competition check-baseline
make -C competition test-hybrid
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 competition/.venv/bin/python -m pytest -q \
  -c competition/pytest.ini --confcutdir=tests/builtin/persistence \
  tests/builtin/persistence/test_seekdb_hybrid_candidates.py
```

若宿主无法运行嵌入式 seekdb，使用官方 Docker 自测环境，或查看本分支 GitHub Actions。嵌入式引擎需要非 loopback 网络接口，并应由普通用户运行。官方 Docker 入口会配置虚拟接口。当前工作环境启动 seekdb 失败，因此真实数据库验证在 GitHub Ubuntu runner 执行。

## 验证边界

官方公开验收已在真实 `pylibseekdb==1.3.0.post4` 上通过（1 passed），并确认原生全文与向量索引均出现在同一条候选 SQL 的 EXPLAIN 中。附加真实数据库回归通过（1 passed），在 384 维向量、同库双查询对照及标签 all/any 条件下，验证了通道候选一致、每通道 LIMIT=3、Memory 隔离、重复查询稳定，且所有三种条件的 EXPLAIN 都包含两类原生索引。新增索引模块通过 Ruff 和 ty 检查，修改的源码与测试通过 Ruff 格式检查。

验证记录：<https://github.com/197388tt-source/powercontext-fork/actions/runs/41636983764>。相关宿主回归为 16 passed、3 skipped；跳过项是没有配置外部 OceanBase 的场景。

隐藏的 173 篇真实文档、20 条 query、固定向量及 qrels 未公开，无法在此复现正式 60 次查询的逐查询 Recall@10 / MRR@10、不低于基线的要求、平均 Recall >= 0.95 或完整 Memory.search p50 <= 500 ms。公开样例及合成回归通过均不能代替这些指标，也不据此宣称获得 20 分或端到端加速。
