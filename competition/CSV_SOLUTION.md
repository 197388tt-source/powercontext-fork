# CSV 行级采集与回查：实现与复现

本实现基于官方 `2026-competition` 分支，保留冻结基线提交历史。

## 实现范围

- `src/powercontext/sources/csv_rows.py` 提供全部六个指定公共接口。
- 使用标准库 csv、io、json、urllib.parse，不增加产品依赖，不访问模型或网络。
- 严格 UTF-8、文件开头 BOM、原始换行、逗号、引号转义均按 csv.reader(strict=True) 处理。
- 全文校验后一次返回 tuple，拒绝空/重复表头、主键缺失/空白/重复、宽度错误及无效编码。
- 保留列序、空字符串、前导零和字段首尾空格。
- identity 为 csv:{编码文档ID}:{编码主键值}，长度上限 256，与行号和非主键字段无关。
- Source Definition 名称 csv-row、版本 1，resolve 返回 CAPTURED Source，read 返回完整 CSVRowInput。
- 标准 TextEvidence 使用紧凑、非 ASCII 转义 JSON，metadata 恰好五个指定键。
- 沿用原生 registry、manifest、observation、Runtime ingestion 和 MemoryCitation 流程。

## 从零复现步骤

1. 安装 Git、uv，使用 Linux x86_64 和 Python 3.12。
2. 克隆参赛仓库并切换 `competition/csv-row-evidence` 分支。
3. 在仓库根目录执行 `make -C competition install`，安装官方固定测试依赖。
4. 执行 `make -C competition check-baseline`，确认冻结基线可达。
5. 执行 `make -C competition test-csv`，应为 17 passed。
6. 执行下列附加回归检查，覆盖 ID 长度边界、中文/分隔符编码、含空格表头、原子失败和直接 resolve：

```bash
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 competition/.venv/bin/python -m pytest -q \
  -c competition/pytest.ini --confcutdir=tests \
  tests/test_csv_rows.py tests/test_sources.py tests/test_source_observations.py
```

7. 在平台选择赛题二，填写仓库 HTTPS URL、分支和该实现的完整 40 位 commit SHA，启动提测并查看反馈。
8. 若平台没有分支/SHA输入，先核实它使用哪个分支；当前实现位于独立分支，不能假定默认分支包含实现。

## 已执行的验证

官方公开测试 17 项全部通过，包含 12 条机场记录的字段保真和真实 SQLite Runtime 写入、关闭重开、Memory 精确引用回查。附加及现有 Source 回归检查 23 项通过。公开测试通过不等于官方补充测试或最终成绩通过。

## 题面差异

语雀页面写 10 分，当前公开分支题面写 20 分；功能契约一致，最终分值以平台为准。

新增代码通过 Ruff 格式、lint 和 ty 类型检查。全仓 prek 检查未全通过：公开赛题资产存在格式和类型诊断；本提交不修改官方测试与样例。
