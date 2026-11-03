# 统一参赛提交

## 提测填写

- 仓库：`https://github.com/197388tt-source/powercontext-fork`
- Branch：`competition/all-completed`
- Commit ID：使用这个分支最新提交的完整 40 位 SHA；可执行 `git rev-parse HEAD` 获取。
- 在 PowerContext 训练营的提测入口填写以上三个字段，只提交一次。平台会对这一提交运行全部 11 题的评测，不需要为每道题分别提交。
- Branch 填分支名，Commit ID 填提交 SHA；二者不可对调。

此文档与 `INTEGRATION_SOURCES.json` 是统一提交的入口。各单题说明中的独立分支用于追溯原始实现，最终提测统一使用本页的仓库和分支。

## 实现范围

| 赛题 | 本分支内容 |
| --- | --- |
| 一 | 保留官方冻结基线历史和公开 Fork 关联 |
| 二 | CSV 行级采集和原始记录回查 |
| 三 | Pi 会话分支采集和持久化断点 |
| 四 | 尚未实现财报 PDF 全文解析与证据页检索 |
| 五 | HTTP 重排与跨批次排序 |
| 六 | 上下文增量传输与完整重建 |
| 七 | 按来源数量触发处理 |
| 八 | Embedding 并发合并和缓存 |
| 九 | 来源撤回与恢复 |
| 十 | 按查询与预算选择原文证据 |
| 十一 | seekdb 单 SQL 混合候选检索 |

九个代码题的实现合并在同一棵代码树中。`composition.py` 同时保留数量触发的过滤器、来源生命周期的初始化和检索接入、seekdb 专用混合索引的组装。没有通过改动官方验收、样例、依赖或跳过测试获取通过结果。

## 复现与验证

环境：Linux x86_64、Python 3.12、uv。固定依赖沿用 `competition/requirements.txt`。

```bash
make -C competition install
make -k -C competition check-baseline test-csv test-pi test-rerank \
  test-incremental test-count-trigger test-embedding test-lifecycle \
  test-evidence test-hybrid
```

各题使用独立 Python 进程。包含尚未实现的 PDF 题时，可运行 `make -C competition test`，该题预期失败，不能将其解释为所有题均已完成。

统一回归工作流为 `.github/workflows/competition-all-completed.yml`。工作流对该分支运行上述公开验收和附加回归；seekdb 采用真实嵌入式引擎。嵌入式引擎需要非 loopback 接口与普通用户权限，工作流在 Ubuntu runner 中准备本地虚拟接口。

本地合并验证已通过 CSV 17、Pi 15、HTTP 重排 10、增量上下文 14、Embedding 8、证据选择 3、生命周期 4 项公开测试，以及 71 项附加和现有回归。数量触发包含实际等待与重启场景；seekdb 在当前宿主启动失败，须以统一工作流中的原生引擎结果为准。后续运行结果可直接查看该提交的 Actions 日志。

公开测试与 GitHub Actions 验证不是平台提测，不保证隐藏测试或最终成绩。此提交不调用平台提测接口。
