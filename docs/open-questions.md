# Foundry 未决问题追踪

> **状态**：`open`（待研究/待用户）/ `answered`（已有结论，链接到决策日志）/ `answered[暂定]`（Claude 已给定义并写入文档，用户可否决）/ `deferred`（明确推迟）。
> 新问题往后追加编号，不复用。

| # | 问题 | 状态 | 结论 / 去向 |
|---|---|---|---|
| OQ-1 | V1 产品形态：交互式会话还是一次性执行器？ | answered | 交互式优先，UI 与 loop 解耦 → [D-004](decision-log.md) |
| OQ-2 | 两条模型路径的开发顺序？ | answered | 原"ChatGPT 登录最优先"随 blocked 结论解除；里程碑重排见 [roadmap.md](roadmap.md)（本地端点解决"无 API 可验证"）→ [D-009](decision-log.md) |
| OQ-3 | 公司 Gateway 协议假设是否成立？ | answered(部分) | Gateway 多模型（含 Claude）→ provider-agnostic IR → [D-006](decision-log.md)；细节 → OQ-6 |
| OQ-4 | 自研的真实动机？ | answered | 公司合规约束 + 学习目的 → [D-007](decision-log.md) |
| OQ-5 | ChatGPT 第三方登录可行性 | answered | **blocked-with-evidence**（[research/auth.md](research/auth.md)）；个人路径改 API key → [D-009](decision-log.md) |
| OQ-6 | 公司 Gateway 具体信息：endpoint 形态（统一 OpenAI 兼容 / 按模型分协议）、认证方式（静态 key / SSO token / 轮换）、TLS/proxy 特殊要求（是否 NTLM 代理）、模型清单、协议差异文档 | **open** | 需用户回公司确认；确认前按可配置 adapter 设计推进（[requirements.md](requirements.md) §3.2），不阻塞 M0–M2 |
| OQ-7 | 编辑工具格式选型 | answered | 模型中性锚定 search/replace 信封 → [D-010](decision-log.md) |
| OQ-8 | 本地 OpenAI 兼容端点作 dev-only backend？ | answered | 接受（用户第二轮，随 D-009 一并确认）；仅开发冒烟，非产品路径 |
| OQ-9 | session resume 进不进 V1？ | answered | schema 可重放、功能 V2 → [D-012](decision-log.md) |
| OQ-10 | 文档/代码语言约定：文档中文、代码+标识符+注释英文？ | open | Claude 按此假设执行中；如有异议随时改 |
| OQ-11 | `read_artifact` 的本意？ | answered[暂定] | 定义为超限工具输出落盘取回 → [D-015](decision-log.md)；如与你本意不符请指出 |
| OQ-12 | 脏工作区策略 | answered | 警告继续 + 脏文件写强制 ASK → [D-011](decision-log.md) |
| OQ-13 | run_command 的唯一 shell 选型：PowerShell `-NoProfile -Command`（暂定，现代工具兼容性好）还是 cmd `/c`（语法更简单、分段器更好写）？ | **open** | 影响命令分段器（安全敏感）与 system prompt；M1 前须定 |
| OQ-14 | golden 验收任务集在哪个仓库上做：公开样例仓库（可进 repo、可分享）还是公司真实仓库（更真但不可移植）？ | **open** | Claude 倾向：公开小型样例仓库进 repo + 公司仓库作 M3 补充验收 |
| OQ-15 | managed policy 层（公司 DENY floor）由谁编写和下发：IT 推送 `C:\ProgramData\Foundry\policy.toml`？随内部 wheel 打包？还是 V1 先不做 managed 层（个人自用阶段）？ | **open** | 不阻塞设计（层机制已预留）；部署公司前须定 |
| OQ-16 | headless `foundry exec` 进 V1（M4 顺手做）还是 V1.5？ | open | 事件架构使其成本很低；M4 时按余量决定 |
