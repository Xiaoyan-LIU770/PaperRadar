# 第三方代码与致谢

PaperRadar 自有整理代码使用根目录 MIT License，版权标注为 2026 PaperRadar contributors；没有用该许可替换第三方许可。

## 随库再分发

vendor/paper-lookup/scripts/ 中的 _common.py、arxiv_atom.py、openalex_abstract.py 来自 [K-Dense Scientific Agent Skills](https://github.com/K-Dense-AI/scientific-agent-skills)，固定上游 commit `593377bb048752abb0152deab9fbb1083a6087df`。文件原样保留，版权为 2025 K-Dense Inc.，MIT 许可全文见 [vendor/paper-lookup/LICENSE.md](vendor/paper-lookup/LICENSE.md)。本次逐文件 SHA-256 与原安装锁定记录一致；哈希保存在 config/skills.lock.json，运行时校验。

## 说明性依赖

paper-lookup 2.4 与 literature-review 1.11 的方法说明用于指导检索和证据表达；完整 Skills 不随本仓库再分发，扩展使用时从上述上游安装并保留原许可证。核心 CLI 和测试不需要用户安装它们，也不调用这些 Skills 的可选付费模型或 PDF 工具。

方法致谢：Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026). Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents. [arXiv:2609.00065](https://arxiv.org/abs/2609.00065)。本次核对了在线页面的作者、标题和年份；不把论文作为软件授权证明。

arXiv 与 OpenAlex 为数据来源。运行后取得的摘要、全文和元数据不因软件使用 MIT 而自动获得再分发许可；本仓库未附带真实检索语料。
