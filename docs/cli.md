# CLI 命令参考

在仓库根目录先执行 `source scripts/env.sh`。统一入口：

```bash
python -B scripts/radar.py --help
```

## 主题与检索

```bash
python -B scripts/radar.py register molecular-gnn --config config/examples/molecular-gnn.json
python -B scripts/radar.py topics
python -B scripts/radar.py track --topic molecular-gnn --mode baseline
python -B scripts/radar.py track --topic molecular-gnn --mode manual
```

register 登记新主题，不覆盖已有配置；baseline 用于首次收录。有基线后使用 manual 更新，首次收录不等于本周新发表。基线失败后使用 manual --full-range 补查。

| track 参数 | 说明 |
| --- | --- |
| --topic TOPIC_ID | 目标主题，建议始终显式填写；兼容默认值为 blue-peled-stability |
| --all-topics | 更新所有已登记主题，优先于 --topic |
| --mode baseline | 首次建立基线 |
| --mode manual | 手动更新，默认模式 |
| --mode scheduled-simulation | 手动模拟一次调度，不创建定时任务 |
| --mode scheduled | 已由用户配置的任务可调用，命令本身不注册任务 |
| --mode scheduled-test | 隔离定时验收模式，禁止写默认正式库 |
| --full-range | 从主题起始日期补查 |
| --strategy legacy-v1 | 每来源一页，默认策略 |
| --strategy blue-peled-v2 | 兼容名称的多组窗口策略，通用主题使用自己的检索组 |
| --snapshot FILE | 隔离测试快照，禁止写默认正式库 |

每来源每次请求最多 50 条；窗口策略最多 24 次 OpenAlex 请求。默认回查最近双来源成功截止日前 35 天。成功返回仍可能截断，迟收录和只更新元数据的旧论文可能漏检。完整参数、缺摘要、覆盖缺口及失败状态保存在 `results/运行ID/manifest.json`。配置字段和策略细节见[操作说明](operations.md)。

`track` 退出码 0 表示来源请求成功，2 表示部分或全部来源失败。失败不等于零新增；全部失败时新增数为未知，部分失败只统计成功返回的记录。失败运行不推进成功时间锚点。其他异常通常以非零退出码结束，需查看错误。

## 查看、复核与报告

```bash
python -B scripts/radar.py history --topic molecular-gnn
python -B scripts/radar.py report --topic molecular-gnn
```

| 命令 | 参数与用途 |
| --- | --- |
| register TOPIC_ID --config FILE | 读取 JSON 并登记主题 |
| topics | 列出已登记主题与配置 |
| history --topic TOPIC_ID | 查询运行历史 |
| show PAPER_ID | 查看保存证据、最新来源元数据及分析卡 |
| review --topic TOPIC_ID --file FILE | 应用审核 JSON，保留已有人工审核 |
| report --topic TOPIC_ID | 生成该主题最新一次运行的简报 |
| report --topic TOPIC_ID --run RUN_ID | 为指定运行生成报告；填写属于该主题的运行 ID |
| report --topic TOPIC_ID --output FILE | 自定义报告路径 |

PAPER_ID 和 RUN_ID 从历史、导出或报告中取得。`track` 已自动生成初步简报，复核和分析卡更新后再执行 report。report 只读取已有运行，不发起检索；成功退出也不代表完成新的文献更新。

## 数据与输出位置

全局参数 `--db PATH`、`--output-root PATH` 放在子命令之前，默认分别为 `database/papers.sqlite` 和仓库根目录。输入与输出路径须位于当前仓库内。隔离库必须配套隔离输出目录，防止覆盖正式导出。文件布局与备份方法见[数据管理说明](operations.md#数据与输出)。
