---
name: research-literature-agent
description: 在 PaperRadar 仓库中登记研究主题、增量追踪文献、复核保存的证据并生成研究简报。
---

# PaperRadar 科研文献追踪

以当前 PaperRadar 仓库为工作目录；先读根目录 AGENTS.md、README.md，再 source scripts/env.sh。
统一入口为 python -B scripts/radar.py。核心检索已包含必要解析器，不依赖外部 Skill 安装。

- 新主题：先 topics；根据研究问题创建独立 JSON，明确纳入和排除标准。register 后首次 track --mode baseline。
- 更新：track --topic TOPIC_ID --mode manual。基线失败后用 manual --full-range 补查，不重复 baseline。
- 报告：report --topic TOPIC_ID 只读取已有运行；用户要求更新时先检索。阅读新增记录的保存标题/摘要，再 review，必要时补分析卡并重新 report。
- 历史：history --topic TOPIC_ID；show PAPER_ID 检查身份、原始证据和最新来源元数据。
- 深入分析：仅在取得相应证据后总结。可使用用户另外安装的 paper-lookup 和 literature-review 扩展全文查找和综述；未安装时不宣称调用过。只有摘要时明确标注分析范围。

复核 JSON 和分析卡格式见仓库 docs/operations.md。更新不得覆盖人工判断；身份冲突交人工处理。
报告运行 ID、来源状态、新增/已有/变化数量、缺摘要、截断、待审核事项和输出路径。
不把新入库等同新发表或新突破，不依据标题推测方法、性能或创新性。
定时配置仅按用户明确请求执行，详见 docs/automation.md；普通手动追踪不创建任务。
