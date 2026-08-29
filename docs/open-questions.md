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
| OQ-6 | 公司 Gateway **剩余未知**：内网 auth 的具体机制（HTTP 交换 / 内部可执行 / 浏览器 SSO）、endpoint 形态、模型清单 | **open（M3 前须定）** | 协议部分已关闭：OpenAI 系走 Responses、token 来自内网 auth → [D-022](decision-log.md)。M3 入场门 = 先取 tool-call 流式脱敏夹具 |
| OQ-7 | 编辑工具格式选型 | answered | 模型中性锚定 search/replace 信封 → [D-010](decision-log.md) |
| OQ-8 | 本地 OpenAI 兼容端点作 dev-only backend？ | answered | 接受（用户第二轮，随 D-009 一并确认）；仅开发冒烟，非产品路径 |
| OQ-9 | session resume 进不进 V1？ | answered | schema 可重放、功能 V2 → [D-012](decision-log.md) |
| OQ-10 | 文档/代码语言约定：文档中文、代码+标识符+注释英文？ | open | Claude 按此假设执行中；如有异议随时改 |
| OQ-11 | `read_artifact` 的本意？ | answered | 定义为超限工具输出落盘取回 → [D-015](decision-log.md)（第三轮确认） |
| OQ-12 | 脏工作区策略 | answered | 警告继续 + 脏文件写强制 ASK → [D-011](decision-log.md) |
| OQ-13 | run_command 的唯一 shell 选型？ | answered | **PowerShell 5.1**（预装零依赖；分段器按 5.1 语法，无 `&&`；prompt 告知模型）→ [D-018](decision-log.md) |
| OQ-14 | golden 验收任务集在哪个仓库上做？ | answered | 自建小型样例仓库 fixture 进 repo；公司仓库作 M3 补充 → [D-020](decision-log.md) |
| OQ-15 | managed policy 层（公司 DENY floor）由谁编写和下发：IT 推送 `C:\ProgramData\Foundry\policy.toml`？随内部 wheel 打包？还是 V1 先不做 managed 层（个人自用阶段）？ | **open** | 不阻塞设计（层机制已预留）；部署公司前须定 |
| OQ-16 | headless `foundry exec` 进 V1（M4 顺手做）还是 V2？ | answered | **进 V1**——事件架构使其成本极低，已实现（ASK→DENY fail-closed，`--json` 事件流） |
| OQ-17 | 仓库说明文件 `FOUNDRY.md`/`AGENTS.md` 进 V1 吗？ | answered | 进 V1，M2 交付（信任门控 + 字节上限；验证命令优先级 任务>仓库>模型）→ [D-019](decision-log.md) |
| OQ-18 | 是否开源、用什么 license？ | answered | 暂不开源，license 推迟 → [D-021](decision-log.md) |
| OQ-19 | session 保留策略默认值（条数 / 天数 / 体积上限）？artifact 是否需静态加密？ | open（M2 前须定） | Claude 倾向：默认保留 30 天 + 体积上限，用户可手动删除；不承诺未经验证的加密 |
