# 赛题十：按查询和预算选择原文证据

题目编号：`context-01-evidence-selection`，20 分。

## 要完成的工作与步骤

1. 从官方 `2026-competition` 分支开发，保留冻结基线 `9db1753434162c0aa7c9f1189a1722e617880a52` 的历史。
2. 在产品 `src/` 的 `PrepareContextRequest` 中增加严格布尔参数 `evidence_selection`，默认 `False`。
3. 开启时仅处理检索阶段传入的 Memory 候选，以查询选择正文中的原文片段，不生成摘要、不调用模型或在线服务。
4. 为每个片段保存 scope、Memory revision、entry ID、entry version ID，以及半开 Python 字符区间 `[start,end)`；文本必须等于原文切片。
5. 在 `PreparedContextBuild.fragments` 返回片段元组，确保 `origins` 和最终 JSON items 与其逐项对应。
6. 保留既有 trust policy 和 PREPARED_CONTEXT_V1 envelope，JSON 中写入 trust、citation、content、start、end。
7. 对完整序列化结果计算 UTF-8 字节数，将 policy、标记、JSON、引用和内容一起纳入 max_bytes；无可用片段返回合法空结果。
8. 检查查询变化、后部证据、多片段、Unicode、边界预算、跨 scope 和版本身份；关闭开关时验证普通 prepare 兼容。
9. 使用 Python 3.12 和官方固定依赖运行公开测试，并确认冻结基线可达。
10. 将实现、测试和说明上传至公开产品仓库的 `challenge-10-evidence-selection` 分支，记录完整 40 位 commit SHA。
11. 由参赛者自行在平台选择赛题十，填写产品仓库 HTTPS URL、分支和完整 commit SHA，再发起提测。本次工作不操作平台提测。

## 实现

- `src/powercontext/builtin/runtime/models.py`：新增默认关闭的严格布尔开关。
- `src/powercontext/builtin/runtime/prepared_context.py`：新增 `PreparedMemoryFragment` 和 `PreparedContextBuild.fragments`，开启时进入证据选择路径；预算探测使用相同选择结果。
- `src/powercontext/builtin/runtime/evidence_selection.py`：原文分句、查询词规范化、候选内逆文档频率与长度归一化评分、重复句去重、有限宽度束搜索组合、相邻区间合并。整句无法装入时尝试查询词附近的原文切片。所有试装均测量完整 UTF-8 输出。
- `tests/builtin/runtime/test_evidence_selection.py`：补充行为回归测试。

算法只读当前查询和已传入候选，不读取题号、隐藏标注、gold 区间或外部文件。词形规范化仅用于排序，输出始终直接切取原文。相同正文在不同 scope 或不同版本中保留独立身份。

选择模式优先于 Code 和 assembly 选项，仅输出 Memory 证据。未开启时沿用原有 prepare 路径；`fragments` 默认为空元组。

## 复现

```bash
make -C competition install
make -C competition check-baseline
make -C competition test-evidence
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 competition/.venv/bin/python -m pytest -q -o addopts='' \
  tests/builtin/runtime/test_prepared_context.py \
  tests/builtin/runtime/test_evidence_selection.py
```

## 已验证与边界

- 官方公开证据选择测试：3 项通过，包含真实 Memory 写入、检索及 citation 回查。
- 原有上下文组装测试：71 项通过。
- 新增证据选择测试：14 项通过，覆盖多预算、Unicode 与 JSON 转义、跨 scope/版本、非连续片段、后部证据、相邻区间合并、长句切片、合法空结果、默认开关和预算探测。
- 修改的产品源码和新增测试通过 Ruff 与定向 ty 检查。全仓 `prek run -a` 未通过：比赛基线中其他题目尚未实现的接口及公开辅助代码存在类型诊断；未为消除这些诊断改动公开资产。
- 冻结基线检查通过；未修改组织方公开测试、评分规则、样例或固定依赖。

隐藏质量验收使用 24 个 claim、每次三篇摘要和两档预算（1400、1800 bytes），共 48 次请求。要求平均 evidence token recall ≥ 0.65，且完整覆盖官方证据区间的请求比例 ≥ 40%；功能和质量必须全部通过才能得 20 分。

本地公开样例不含 gold 标签，无法据此报告隐藏质量指标或保证满分。当前选择器采用词项相关性，对于低词面重合的同义改写、多句推理、无查询词的邻接证据可能漏选；束搜索是近似预算优化，不保证全局最优。

## 提交信息

- 仓库：https://github.com/197388tt-source/powercontext-fork
- 分支：`challenge-10-evidence-selection`
- 提交时使用交付消息中的完整 commit SHA，勿留空而误用默认分支。
- 该分支基于官方比赛分支独立开发，仅交付本题，不宣称已合并其他赛题实现。
- 本次代码与说明由 AI 助手协助实现，并执行上述公开测试与回归验证。
