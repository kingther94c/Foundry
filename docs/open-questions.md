# Foundry 未决问题追踪

> **状态**：`open`（待研究/待用户）/ `answered`（已有结论，链接到决策日志）/ `deferred`（明确推迟）。
> 新问题往后追加编号，不复用。

| # | 问题 | 状态 | 结论 / 去向 |
|---|---|---|---|
| OQ-1 | V1 产品形态：交互式会话还是一次性执行器？ | answered | 交互式优先，UI 与 loop 解耦 → [D-004](decision-log.md) |
| OQ-2 | 两条模型路径的开发顺序？ | answered | ChatGPT 登录保持最优先（开发环境无 API key，这是唯一真实模型访问）→ [D-005](decision-log.md) |
| OQ-3 | 公司 Gateway 协议假设是否成立？ | answered(部分) | Gateway 多模型（含 Claude）→ 内部抽象 provider-agnostic → [D-006](decision-log.md)；具体协议细节仍开放 → OQ-6 |
| OQ-4 | 自研的真实动机？ | answered | 公司合规约束 + 学习目的 → [D-007](decision-log.md) |
| OQ-5 | **ChatGPT 第三方登录可行性**：非 Codex 工具能否合规使用 ChatGPT 订阅做推理？若无受支持方案，fallback 阶梯怎么排？ | open | 可行性研究进行中（后台 research agent）；证据出来后带选项问用户 |
| OQ-6 | 公司 Gateway 具体信息：endpoint 形态（统一 OpenAI 兼容 / 按模型分协议）、认证方式（静态 key / SSO token / 轮换）、TLS/proxy 特殊要求、模型清单 | open | 需用户回公司确认；确认前按可配置 adapter 设计 |
| OQ-7 | 编辑工具格式选型：V4A apply_patch（OpenAI 特化）vs str-replace（模型中性）vs unified diff？多模型 Gateway 下是否需要按模型族配置工具面？ | open | 等 aider/codex 研究结论，设计文档给建议再确认 |
| OQ-8 | 是否接受**本地 OpenAI 兼容端点**（LM Studio / Ollama）作为 dev-only backend，用于无账号冒烟验证？（不进正式产品路径） | open | Claude 倾向"接受"；待用户确认 |
| OQ-9 | session resume（中断后恢复会话）是否进 V1？ | open | Claude 倾向"V1 只做记录与事后查看，resume 进 V2"；待用户确认 |
| OQ-10 | 文档/代码语言约定：文档中文、代码+标识符+注释英文？ | open | Claude 按此假设执行中；如有异议随时改 |
| OQ-11 | `read_artifact` 工具的本意是什么？（草稿列了但未定义——读命令输出产物？测试报告？） | open | 待用户澄清或在需求 v0.2 中重新定义/删除 |
| OQ-12 | 脏工作区策略："保护用户已有修改"具体指什么？（拒绝在脏仓库上跑 / 记录 baseline 区分归属 / 禁止触碰用户已改文件？） | open | 设计文档给建议再确认 |
