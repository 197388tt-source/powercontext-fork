# 上下文增量传输实现与验收

实现位于 `src/powercontext/builtin/runtime/context_pack.py`，Runtime 接口位于 `application.py` 的 `ScopedContextApplication.prepare_incremental`。

完整实现 ContextPack、ContextPackDelta、ContextPackError、apply_context_pack_delta，以及首次完整包和后续增量 prepare。采用默认 512 bytes 的 UTF-8 有界确定性分块与最长公共前缀复用；普通 prepare 不变，引用随正文原样传输，不使用进程内共享缓存。序列化采用固定字段、标准 Base64、排序紧凑 JSON、SHA-256 和包含自身的完整 pack 字节数。解码拒绝缺失、额外或重复字段、非法 UTF-8/Base64、篡改摘要及非法前缀。

## 操作步骤

1. 基于官方 2026-competition 分支保留冻结版本历史。
2. 实现完整包的分块、序列化、摘要与固定点字节数。
3. 实现前缀 delta 与接收端独立重建、base 和 target 摘要校验。
4. 接入既有 prepare，空结果编码为空 pack。
5. 执行公开协议与 Runtime 测试，补充非法 wire 与多字节边界测试。
6. 执行 40 轮真实数据成本测量，累计首次 pack 及全部 delta 的 wire bytes。
7. 将实现推送到可访问的产品仓库，提交仓库 HTTPS URL、分支和完整 40 位 SHA。
8. 在平台选择赛题六进行提测，查看功能与传输成本验收结果。

公开测试命令：`make -C competition test-incremental`。

## 独立成本测量

`competition/measure_incremental.py` 接收 QMSum 固定版本的 ES2004a.json，验证原文件 SHA-256。原文件来自 Yale-LILY/QMSum，commit `83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e`，路径 `data/ALL/test/ES2004a.json`，MIT 许可。下载地址：https://raw.githubusercontent.com/Yale-LILY/QMSum/83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e/data/ALL/test/ES2004a.json 。

按原序筛选 120–2000 字节且含 remote/design/control 的真实 turn，保留 speaker 和原正文。先采集 8 条，第 11 轮追加第 9 条，四个 10 轮段查询分别为 remote/control/design/remote。产品源码不知晓该 workload。每轮使用原生 prepare 和 prepare_incremental，并从序列化数据恢复接收端 pack，与普通正文逐字节比较。

运行：`PYTHONPATH=src .venv/bin/python competition/measure_incremental.py /path/ES2004a.json`。

40 轮非空，4 种不同 Context。结果：

| 累计项目 | 字节 |
| --- | ---: |
| 普通正文 UTF-8 | 141470 |
| 首次完整包与后续 delta | 29130 |
| 每轮完整 v1 pack | 196050 |
| 每轮独立 gzip level=9,mtime=0 | 64650 |

相对正文节省 79.41%，相对完整 pack 节省 85.14%，相对逐轮 gzip 节省 54.94%。逐轮明细见 INCREMENTAL_MEASUREMENT.json。这是自定义独立测量，不是组织方隐藏 workload，不代表平台通过或最终得分。

## 提交说明

平台只加载 src/；附带测试和报告不替代官方验收。语雀页面为 10 分，当前官方比赛分支题面为 20 分，以平台实际配置为准。需要在平台填入本提交的仓库地址、独立实现分支和完整 SHA 后执行提测。
