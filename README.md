# Foundry

从零自研的本地 coding-agent runtime：Python 3.12、Windows、离线 wheel 安装。自己拥有 agent loop、tools、policy、provider、session 的全部代码与接口；不 fork、不调用、不冒充任何现有 coding agent。

**当前状态：M0–M4 已实现并可运行**，1111 个测试在无网络、无凭证的机器上通过（含 6 轮对抗评审后的安全回归，与 540 个自动生成的熔断表不变量组合）。第 6 轮之后另做了一次净室验证：重建 wheelhouse、`--no-index` 装进全新 venv，再跑一次真实任务（跑挂测试 → 打补丁 → 重跑转绿 → 引用自己工具输出里的 event id 完成），14 项检查全过。

## 快速开始

```bash
python -m pip install --no-index --find-links wheelhouse foundry
foundry doctor
foundry login
foundry
```

或离线构建 wheelhouse：

```bash
python scripts/build_wheelhouse.py
```

## 命令

| 命令 | 说明 |
|---|---|
| `foundry` / `foundry run [任务]` | 交互式会话（默认） |
| `foundry exec <任务> [--json]` | 无人值守执行；ASK 一律 DENY（fail-closed） |
| `foundry record <任务> --output f.json` | 把会话录成 replay 夹具 |
| `foundry sessions [id]` | 列出或查看会话 |
| `foundry report [--json]` | 补丁首次成功率、命令失败、拒绝次数、token 统计 |
| `foundry login` / `logout` | 凭证管理（DPAPI 加密） |
| `foundry doctor` | 环境自检 |

退出码：`completed=0 partial=10 blocked=11 failed=12 cancelled=13 interrupted=14`

## 设计要点

- **一个 loop**：`while 模型返回 tool calls：policy → 执行 → 回填 → 重采样`。provider adapter 只做协议互转，不拥有 loop、不碰 policy、不调工具。
- **六步 policy 流水线**：熔断表 → pre_tool 回调 → DENY → ASK → mode 基线 → ALLOW → 交互审批（无人应答即 DENY）。任意层的 DENY 不可被任何 ALLOW 翻转。
- **证据链**：`finish` 的每条验证声明必须引用真实命令事件且 exit code 相符，否则 `completed` 降级 `partial`。空 claims 是有效披露，编造的不是。
- **无沙箱、诚实披露**：审批减少失误而非恶意。边界与非目标见 [docs/threat-model.md](docs/threat-model.md)。
- **依赖预算**：运行时只有 `rich`（共 5 个纯 Python wheel）。HTTP/SSE、DPAPI、Job Object 全部走 stdlib 与 ctypes——`ssl.create_default_context()` 信任 Windows 系统证书库，公司 MITM 代理零配置。

## 代码地图

```text
src/foundry/core/     runtime（唯一的 loop）、policy、tools、backends、session、workspace、winapi
src/foundry/cli/      终端 UI：事件订阅者 + 审批 UI + report
src/foundry/prompts/  版本化的 system prompt
tests/                1111 个测试：golden 场景、攻击表、熔断表不变量
```

`foundry.core` 不 import `foundry.cli`，也不 import 任何第三方包——两条都有测试强制（[tests/test_architecture_config.py](tests/test_architecture_config.py)）。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/requirements.md](docs/requirements.md) | 需求 v0.2 |
| [docs/design.md](docs/design.md) | 设计方案：包结构、核心接口、关键机制 |
| [docs/threat-model.md](docs/threat-model.md) | 保护什么、不保护什么、执行点在哪 |
| [docs/roadmap.md](docs/roadmap.md) | 里程碑与状态 |
| [docs/decision-log.md](docs/decision-log.md) | 24 条编号决定（含被推翻的） |
| [docs/open-questions.md](docs/open-questions.md) | 未决问题 |
| [docs/research/](docs/research/) | 10 份调研笔记 |

## 未决

个人路径 = OpenAI API key（ChatGPT 登录已确认 blocked，证据在 [docs/research/auth.md](docs/research/auth.md)）。公司 Gateway 的 `responses` adapter 已实现并有合同测试，但**真实协议行为待验证**——M3 入场门是先取得 Gateway 的 tool-call 流式脱敏夹具（[OQ-6](docs/open-questions.md)）。
