# Foundry

从零自研的本地 coding-agent runtime：Python 3.12、Windows、离线 wheel 安装。自己拥有 agent loop、tools、policy、provider、session 的全部代码与接口；不 fork、不调用、不冒充任何现有 coding agent。

**当前阶段：blueprint（需求与设计）**，尚无实现代码。

## 文档地图

| 文档 | 内容 |
|---|---|
| [docs/requirements.md](docs/requirements.md) | 需求 v0.2（取代外部草拟的 v0.1）：动机、产品形态、模型路径、policy、工具面、session、打包、验收 |
| [docs/design.md](docs/design.md) | 设计方案：包结构、核心接口（IR/事件/policy/backend）、关键机制、依赖决策 |
| [docs/roadmap.md](docs/roadmap.md) | 里程碑 M0–M4 与 V2 候选 |
| [docs/decision-log.md](docs/decision-log.md) | 全部编号决定（含被推翻的），每条带理由与出处 |
| [docs/open-questions.md](docs/open-questions.md) | 未决问题追踪（当前 open：OQ-6 公司 Gateway 细节、OQ-10 语言约定、OQ-15 managed policy 下发、OQ-16 headless 时点） |
| [docs/research/](docs/research/) | 调研笔记：codex 深挖、Claude Code、gemini-cli/luban、aider/OpenHands/SWE-agent、ChatGPT 认证可行性、Windows/Python 约束、对 v0.1 的三视角批判 |

## 一句话状态

个人路径 = OpenAI API key（ChatGPT 登录已确认 blocked，证据在 [docs/research/auth.md](docs/research/auth.md)）；公司路径 = 多模型 Gateway（含 Claude）→ 内部表示 provider-agnostic；编辑格式 = 模型中性锚定 search/replace；trusted-host 无沙箱、诚实披露、PolicyEngine 六步流水线为唯一防线。
