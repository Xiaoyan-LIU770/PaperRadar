# 安装、环境配置与故障排查

## 运行环境

使用 Python 3.11+（含 venv 和系统时区数据）、curl，以及 bash 或 zsh。macOS / Linux 或具备相同工具的 WSL 可按下列步骤配置；当前本地验收使用 macOS、Python 3.12.14，Windows 原生环境未验收，Linux CI 尚待托管运行。

核心和离线测试只用 Python 标准库，不需要 pip 安装依赖。必要的第三方解析器已随库提供。使用自然语言工作流还需要用户自己的 Codex 环境；独立 CLI 不依赖 Codex 或模型 API。

获取项目后，在仓库根目录执行：

```bash
python3 --version
curl --version
python3 -m venv .venv
source scripts/env.sh
python -B scripts/radar.py --help
python -B scripts/check_install.py
```

每次新开终端重新执行 `source scripts/env.sh`。它选择当前仓库的虚拟环境和源码路径，不读取 `.env`。如果已有 `.venv`，无需再次创建。

## 网络与 OpenAlex 凭证

arXiv 和 OpenAlex 检索需要网络。OpenAlex 匿名访问受当前服务策略和额度影响；需要时到 [OpenAlex 设置页面](https://openalex.org/settings/api)获取自己的 Key，通过安全方式注入 `OPENALEX_API_KEY` 进程环境。不要把 Key 写进仓库、命令参数或聊天。

程序不会读取其他项目的凭证或自动购买额度，也不调用模型 API。Codex 账户使用费用与本 CLI 分开。macOS 可选钥匙串适配器的启用条件见[自动化说明](automation.md)，默认不启用。

## 使用 Codex Skill

在 Codex 中打开当前仓库，明确要求读取 `.agents/skills/research-literature-agent/SKILL.md`，再描述需要建立、更新或复核的主题。这种方式直接使用已提供的文件，不依赖选择列表的自动发现。

Skill 为 Codex 提供步骤与证据约束；真正的检索、存储与报告由 `scripts/radar.py` 执行。一般主题初始为“待筛选”，Codex 阅读保存的标题和摘要后再应用审核；蓝光示例提供专用规则初筛。审核文件和分析卡格式见[操作说明](operations.md)。

仓库级 Skill 发现机制参见[官方文档](https://learn.chatgpt.com/docs/build-skills)。如果列表中未显示，可以重新打开应用或直接指定文件。本项目已验证 Skill 文件结构与 CLI 链路，尚未进行新用户环境中的自动发现和 Agent 行为验收。

核心无需额外安装 Skill。需要更多数据库、开放全文查找或综述方法时，可让 Codex 的 skill-installer 从 [K-Dense 上游仓库](https://github.com/K-Dense-AI/scientific-agent-skills)安装 paper-lookup 和 literature-review，保留许可证，不启用可选付费模型。安装后在 Skill 列表核对名称，再显式调用。此扩展安装不属于当前已执行的独立验收范围。

## 离线检查与测试

```bash
source scripts/env.sh
python -B scripts/check_install.py
python -B -m unittest discover -s tests -v
python -B tests/offline_demo.py
```

离线演示在 tests 下的临时目录模拟 HTTP 返回，执行解析、入库、重复检索和报告，结束后自动清理。合成 TEST DATA 不进入正式库。

安装检查包含 Python 语法、示例配置、curl、Skill 文件和解析器哈希。固定上游版本和许可见[第三方声明](../THIRD_PARTY_NOTICES.md)，哈希位于 `config/skills.lock.json`。历史测试结果、在线探测、CI 和未验证范围见[发布验收记录](release-audit.md)。

## 常见问题

| 现象 | 处理方法 |
| --- | --- |
| `No module named paperradar` | 在仓库根目录 source scripts/env.sh，确认 .venv 已创建 |
| curl 不存在 | 为系统准备 curl，再运行安装检查 |
| DNS / TLS 失败 | 检查网络与证书环境，查看 results 下对应运行的错误记录 |
| HTTP 429 | 查看请求日志，等待额度恢复或缩小范围，避免反复重试 |
| “主题已登记” | 使用 manual 更新；登记配置的调整方式见操作说明 |
| 基线检索失败 | 使用 manual --full-range 补查；不要重复登记或重建已有基线 |
| 文献缺摘要 | 保留待复核，补充摘要或全文后再分析 |
| Skill 哈希不符 | 恢复固定版本 vendor 文件，或审查新版后更新锁文件，不关闭校验 |

来源失败和成功但无新增含义不同。退出码、覆盖限制和详细命令见 [CLI 参考](cli.md)。
