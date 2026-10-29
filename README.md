# PaperRadar

**持续发现相关文献，把时间留给阅读与判断。**

PaperRadar 是面向科研人员、由 Codex 辅助操作的文献发现与持续追踪工具。定义研究方向后，它从 arXiv 和 OpenAlex 检索文献、去重并保存历史；你可以让 Codex 按自己的标准阅读新增文献的证据，辅助筛选并整理研究简报，让重复搜索和整理成为可延续的工作流。

## 核心功能

- **自然语言驱动**：告诉 Codex 你想研究什么，借助项目自带 Skill 完成检索、复核与报告。
- **持续文献追踪**：建立主题基线，后续更新识别新收录文献与元数据变化。
- **辅助筛选**：让 Codex 结合主题标准和保存的标题、摘要，说明哪些论文值得进一步阅读。
- **本地文献管理**：用 SQLite 保存文献、来源、历史和人工判断，提供 JSON / CSV 导出。
- **证据可追溯**：保留检索响应和审核依据，方便回看原文片段、核对版本与修正判断。
- **多研究主题**：分别维护不同方向的检索配置、收录历史和筛选结果。

## 快速开始

从本仓库页面获取项目，可通过 **Code → Download ZIP** 下载并解压。以下操作均在 PaperRadar 文件夹中进行。

准备 Python 3.11+、curl 和 bash / zsh；使用 Codex 方式还需可用的 Codex 环境。首次准备只需：

```bash
python3 -m venv .venv
source scripts/env.sh
```

实际检索需要联网；OpenAlex 如要求凭证，按[环境配置说明](docs/getting-started.md)设置自己的 `OPENALEX_API_KEY`。每次打开新终端，重新执行 `source scripts/env.sh` 即可。

### 方式一：使用 Codex（推荐）

在 Codex 中打开本仓库，发送：

> 请读取本仓库 `.agents/skills/research-literature-agent/SKILL.md` 并按其中流程操作。使用 `config/examples/molecular-gnn.json` 示例，建立 `molecular-gnn` 主题；如果主题已存在就更新。完成首次检索后，按配置中的纳入和排除标准阅读保存的标题与摘要，辅助筛选值得关注的论文，说明依据并生成中文简报。只有摘要时标注“基于摘要分析”。

显式指定文件即可使用随库工作流，无需等待 Skill 自动出现在选择列表。你也可以把示例方向换成自己的研究问题，让 Codex 先协助拟定检索词和筛选标准，再登记新主题。

### 方式二：通过命令行使用

准备好环境后，使用同一个示例完成首次检索：

```bash
python -B scripts/radar.py register molecular-gnn --config config/examples/molecular-gnn.json
python -B scripts/radar.py track --topic molecular-gnn --mode baseline
```

检索结束会生成简报，打开 `reports/weekly/` 中最新的 Markdown 文件即可阅读；文献表在 `database/` 下。CLI 负责检索、存储与报告，通用主题的文献先标记为“待筛选”，再由你或 Codex 完成证据复核。

## 使用示例：持续关注分子性质预测

假设你正在研究图神经网络在分子性质预测中的应用。首次检索建立文献基线后，可以告诉 Codex：

> 复核 molecular-gnn 主题本次新增的文献，优先关注研究任务、评估方法与泛化能力。区分摘要明确报告的内容和还需要阅读全文确认的问题，保存筛选依据，并更新简报。

下一次阅读文献时，继续要求：

> 更新 molecular-gnn 主题，保留已有历史和人工判断。按原有筛选标准整理新增文献，告诉我哪些值得读、为什么值得读，以及哪些信息还不清楚。

也可以直接发起一次更新：

```bash
python -B scripts/radar.py track --topic molecular-gnn --mode manual
```

每次更新保留运行记录，方便回看发现时间和来源变化。希望固定周期执行时，再按[定时追踪指南](docs/automation.md)配置；日常手动使用无需启用后台任务。

## 工作原理

**研究主题 → 文献检索 → 去重与保存 → 辅助筛选 → 增量追踪 → 研究简报**

Python 程序处理可重复的数据工作，Codex 按研究者设定的标准协助理解证据。规则初筛、Codex 分析和人工判断各有记录，最终阅读取舍由研究者决定。

## 文档

- [安装、环境配置与故障排查](docs/getting-started.md)
- [主题配置、筛选规则与数据管理](docs/operations.md)
- [CLI 命令参考](docs/cli.md)
- [Codex Skill 使用指南](docs/getting-started.md#使用-codex-skill)
- [配置定时追踪](docs/automation.md)

## 开源协议与致谢

PaperRadar 自有代码采用 [MIT License](LICENSE)。感谢 arXiv、OpenAlex 提供文献接口，以及 K-Dense 提供解析器和科研工作流方法。第三方代码保留原始许可，详见[第三方声明与致谢](THIRD_PARTY_NOTICES.md)。
