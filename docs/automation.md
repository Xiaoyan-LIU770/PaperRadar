# 可选的自动化追踪

本仓库不包含任务 ID、登记状态或已启用的定时任务。`scheduled` 和 `scheduled-simulation` 都只是一次 CLI 调用，不会注册计划。

先完成 README 手动基线与增量运行，再由用户在具备本地项目访问能力的桌面应用中配置任务。任务必须指向用户自己的这个仓库和持久数据库，避免每次使用空工作目录。若用隔离工作树，需自行设计持久数据位置；当前 CLI 要求数据位于其工作仓库内。

提示词模板（由用户补入自己的目录、主题、时间与时区）：

> 在我指定的 PaperRadar 仓库运行，使用该仓库 research-literature-agent。先 source scripts/env.sh，再 python -B scripts/radar.py track --topic TOPIC_ID --mode scheduled。阅读新增记录的保存证据，完成适当复核并生成报告。汇报运行 ID、来源失败、缺摘要、截断和待审核事项。不创建其他任务、不修改 Git、不读取其他项目凭证。

本地任务需要电脑开机、应用运行、项目可访问，以及网络和目录权限；进程能否取得环境变量应在无人值守环境另行验证。不要把 API Key 写进提示词。默认只读取进程环境中的 OPENALEX_API_KEY；随库保留的 macOS 钥匙串适配器只有存在显式启用策略才读取专用项，仓库不分发启用策略，也不提供默认写入凭证步骤。

在 Scheduled 页面核查首轮触发时间、运行目录、数据库新增记录和报告，确认失败能被发现后再长期使用。本次开源验收未创建任务，也未验证新用户环境中的真实定时触发。

依据：[官方 Scheduled tasks 文档](https://learn.chatgpt.com/docs/automations?surface=app)。应用界面、套餐和工作区权限可能影响可用性。
