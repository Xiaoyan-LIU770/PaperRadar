# 配置、复核与数据结构

## 主题配置

主题 ID 使用小写英文、数字、连字符或下划线，最长 64 字符。登记后配置保存在 SQLite 的 topics 表，编辑原 JSON 不会更新已登记主题；当前不提供覆盖主题配置命令，调整检索方案可登记一个新 ID，保留旧主题历史。

| 字段 | 用途 |
| --- | --- |
| topic_zh | 中文研究问题，必填 |
| date_from | 起始日期 YYYY-MM-DD，必填 |
| arxiv_query | arXiv 带字段检索表达式，必填；程序追加日期约束 |
| openalex_search | OpenAlex 检索词，必填 |
| max_per_source | 每次请求上限 1–50，默认 50 |
| overlap_days | 增量回查天数，默认 35 |
| screening_version | generic 表示待人工/Codex筛选；blue-peled-v1 是蓝光专用示例 |
| inclusion_criteria_zh / exclusion_criteria_zh | 供研究者和 Codex 复核使用，程序不会自动理解自然语言标准 |
| openalex_groups | 可选数组，元素含 id、query；仅窗口策略使用 |

实际截止日期在每次运行时刷新。baseline 和 --full-range 默认每来源一页；manual 从最近双来源成功截止日回查。未成功的运行不推进锚点。`--strategy blue-peled-v2` 是兼容保留的窗口策略名称：它优先使用 openalex_groups，通用主题未设置组时用自己的 openalex_search，最多 24 次 OpenAlex 请求。默认 legacy-v1。成功返回仍可能截断，不是系统综述的穷尽检索。

## 逐条审核

先 `python scripts/radar.py show PAPER_ID`。在 config 中创建审核 JSON（PAPER_ID 替换为真实库内 ID）：

```json
{
  "PAPER_ID": {
    "level": "中",
    "reason": "根据已保存摘要填写具体纳入理由",
    "evidence": [{"field": "abstract", "quote": "替换为摘要中逐字存在的短语"}],
    "recommend_close_reading": false
  }
}
```

运行 `python scripts/radar.py review --topic TOPIC_ID --file config/review.json`。
证据必须逐字存在，已有审核不可批量覆盖。当前审核状态使用历史兼容标签“Codex已逐条复核”；它表示应用了审核文件，不是程序完成了语义评价。

分析卡存入 reports/weekly_analysis.json 数组，字段为 paper_id、question、method、finding、author_proposed_feature、analysis_basis、evidence_field、evidence_quote、full_text_read。摘要卡填写 analysis_basis 为“基于摘要分析”、evidence_field 为 abstract、full_text_read 为 false。可附 comparison_ids（该次运行前已属于主题的 ID）和 comparison（明确标为综合推断）。生成器核对证据片段和引用身份，解释是否真实仍由研究者负责。无分析卡时报告保留未知，不自动生成研究结论。

## 数据与输出

- database/papers.sqlite：长期库；papers 保留原始判断，topic_papers 保留各主题关系与 first_seen。
- observations / metadata_changes：最新来源元数据及变化，不静默覆盖旧摘要和人工判断。
- review_pairs / unresolved_observations：相似标题及冲突标识，不能当作已合并版本。
- runs：运行历史、来源状态、首次收录和新增数量。
- results/运行ID/：配置、逐次原始响应、请求日志、规范化记录与 manifest。
- database/papers.json、papers.csv：导出；reports/weekly/：Markdown 简报与引用校验 JSON。
- logs/tracking_runs.jsonl：运行历史。发布仓库时不能包含这些个人运行产物。

CLI 的 --db、--output-root 须位于当前仓库内，且须放在子命令之前。隔离数据库必须配套隔离输出目录；测试快照不能写入默认正式库。请勿并发启动多个追踪进程；SQLite 事务保护入库，但外围导出不是多进程作业队列。

## 创建自己的主题

复制 config/examples/molecular-gnn.json 到 config/topics 下的新文件（先创建目录），修改研究问题、日期、双来源检索词及纳入/排除标准，然后用新 ID 登记并建立基线。一般主题使用 generic；config/examples/blue-peled.json 的 blue-peled-v1 是蓝光钙钛矿专用示例。自然语言标准用于研究者和 Codex 复核。

## 备份与分享

备份前停止运行中的追踪，同时保留 database、results、reports 与个人主题配置，维持数据库和证据链的一致性。这些运行文件属于用户自己的研究资料，默认在忽略规则中；分享工作目录前仍需检查实际文件。

源代码在 src/paperradar，入口在 scripts，公开示例在 config/examples，随库解析器在 vendor，测试在 tests，项目 Skill 在 .agents/skills。环境排错见[安装指南](getting-started.md)，完整命令见 [CLI 参考](cli.md)。
