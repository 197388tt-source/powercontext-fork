# 赛题四：财报 PDF 全文解析与证据页检索

## 交付目标

实现 `powercontext.sources.pdf_pages` 的 `PDFDocumentInput`、`PDFPageInput`、
`parse_pdf_document`、`PDF_PAGE_SOURCE_DEFINITION` 和 `search_pdf_pages`。
提交产品仓库 HTTPS URL 和 40 位 commit SHA；本实现不包含平台提测操作。

## 完成步骤

1. 从 `2026-competition` 建立工作分支，保留冻结基线历史。
2. 验证非空文档 ID，通过 pypdf 读取完整原始字节。顺序提取所有物理页，空白页保留，页码从 1 开始。
3. 对完整原始字节计算 SHA-256。空文件、非 PDF、损坏文件、需要非空密码的文件统一以 ValueError 失败，不返回部分页。
4. 将文档 ID、完整摘要、物理页码组成无歧义 JSON 身份后散列，生成有界稳定的页 Source 名称。
5. 定义版本 1 的 `pdf-page` CAPTURED Source；read 返回原页输入。标准文本投影为 `DOCUMENT_ID\nPAGE_TEXT`，metadata 仅含题目规定的三个字段。
6. 通过 scoped Memory 的公开 FTS API 查询，按词项拆分和通用财务术语扩展召回候选页；使用候选集词频、长度归一化和查询排名融合排序。
7. 使用返回 hit 的完整 Memory revision、entry ID、entry version ID 构造 citation，经 memory.get 验证只引用一个 pdf-page Source，按 Source 去重并截断至 limit。
8. 运行官方公开测试，验证 503 页原始 PepsiCo PDF、真实持久化、重复提交幂等、重启检索和精确回查。
9. 运行补充测试，检查含正文的页边界、空文档 ID、非空密码、后部 stream 损坏、长 ID、修订身份、零上限、空问题和无匹配问题。
10. 上传公开仓库，记录分支和完整提交。平台提测由参赛者自行决定和执行。

## 本地验证命令

```bash
make -C competition install
make -C competition check-baseline
make -C competition test-pdf
PYTHONPATH=src:competition PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  competition/.venv/bin/python -m pytest -q -c competition/pytest.ini \
  --confcutdir=competition tests/test_pdf_pages.py
ruff check src/powercontext/sources/pdf_pages.py tests/test_pdf_pages.py
ruff format --check src/powercontext/sources/pdf_pages.py tests/test_pdf_pages.py
ty check --python competition/.venv src/powercontext/sources/pdf_pages.py
```

## 验证结果及限制

官方公开测试 6 项通过，补充测试 6 项通过；Ruff 和新增源码的 ty 检查通过。
官方测试包含真实 Runtime 入库、重启、引用回查；没有修改公开测试、样例或判题逻辑。
全仓 `prek run -a` 未通过：基线样例存在 JSON 格式问题、公开测试有 lint 问题，
默认开发环境还缺少竞赛专用模块/依赖并报告既有类型错误。检查器对基线文件的自动格式修改已撤销，
未将这些无关改动提交；新增源码在固定竞赛环境中的独立类型检查通过。

公开测试不分发质量 gold，也不计算正式 evidence-page Recall@20。因而这些测试不能证明
每页词项数位于冻结值的 90%–110%、证据词项召回与整页 F1 均达到 0.95，
或者公开及隐藏集 Recall@20 均达到 0.70。未读取答案、gold、预解析文本或证据页清单来构建检索。
正式评测涉及 5 份完整财报、1151 页、24 个问题，所有必测行为通过得 50 分，否则得 0 分。

查询最多执行 32 次 FTS，每次最多取 100 个 hit。每个候选查询独立使用 scoped Memory 当前视图，
不缓存跨调用数据，也不绕过存储或持有外部页索引。排序只使用实际返回的页文本与问题，
不调用在线服务、模型或向量 API。该实现面向静态已入库评测；不保证多轮查询期间并发修改 Memory 时的统一快照。
这是一条专门交付赛题四的分支，不声称包含其他赛题的全部代码。
