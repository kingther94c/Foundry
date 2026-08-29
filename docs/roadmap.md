# Foundry — Roadmap

> 排序原则（批判-架构 #1 采纳）：最大不确定项不上关键路径；每个里程碑以"可运行 + 可验收"收口。
> v0.1 的排序错误已修正：原"ChatGPT 登录最优先"随 blocked 结论（[D-009](decision-log.md)）自然解除；个人路径 API key 与公司 Gateway 同协议族，骨架先行不再有认证阻塞。

## 状态（2026-08-29）

| 里程碑 | 状态 | 备注 |
|---|---|---|
| M0 骨架 + 接口冻结 | **完成** | IR/事件/backend/session 四接口已冻结；replay + `foundry record` 就绪 |
| M1a 文件工具 + policy | **完成** | 攻击表、决策表全绿 |
| M1b run_command + 分段器 | **完成** | Job Object 进程树清理实测无孤儿 |
| M2 个人路径 E2E + 验收集 | **完成** | golden 场景 8 个；`finish` 证据链；`foundry report` |
| M3 公司 Gateway | **部分** | `responses` adapter + 合同测试已就绪；**入场门未过**——真实 Gateway 夹具待取（[OQ-6](open-questions.md)） |
| M4 打包加固 + 披露 | **完成** | 净 venv `--no-index` 安装实测通过；威胁模型文档；`foundry exec` 已进 V1 |

233 个测试在无网络、无凭证机器上通过。真实 E2E（个人 API key / 公司 Gateway）仍待用户在有凭证的环境执行。

## M0 — Walking skeleton（接口冻结里程碑）

**目标**：`foundry` 能在样例仓库开一个交互会话，用只读工具回答一个问题，全程事件流驱动。

- 冻结四大接口：IR（conversation.py）、事件协议（events.py）、ModelBackend 协议、SessionStore 行信封。
- loop + ContextManager 投影（无 masking）+ 2 个工具（`read_file`、`list_files`）+ 最小 rich UI。
- backend：`replay` + `openai_compat`（用**本地端点**冒烟——无凭证可验证，回应"开发环境没有 API"的约束）；`foundry record` 夹具重录工作流。
- SessionStore 落盘 + 首个 golden transcript 回归测试。
- **验收（评审修正，拆绑定/冒烟）**：绑定 = 无网络无凭证机器上 replay 套件全绿（机器可判）+ 崩溃恢复用例（截尾 journal 判 `interrupted` 而非 completed）；冒烟 = 对本地端点（可选开发环境，需自装 LM Studio/Ollama）完成一次含 ≥1 个 read_file 调用、以 `completed(no_changes)` 干净终止的会话——终止状态与事件序列机器断言，回答质量人工目检、不作门禁。

## M1a — 文件工具 + Policy 流水线（评审修正：原 M1 对独立开发者过肥，拆二）

**目标**：能改代码、能被拦住。**不被 OQ-13 阻塞，可立即开工。**

- apply_patch（锚定格式 + 逐文件原子 + 宽容梯度）、workspace 边界模块、其余文件/git 工具。
- PolicyEngine 六步流水线（先覆盖文件工具）+ 审批 UI（once/session/always）+ 脏工作区策略（baseline + 脏文件 ASK 规则）。
- **验收**：路径逃逸样例全拒（junction、ADS、`..`、设备名、盘符相对、UNC）；越权/malformed tool call 被拒且 loop 存活；accept_edits/脏文件/持久化规则三用例（需求 §4.1 测试表）；ASK 超时=DENY；**脏工作区矩阵**（需求 §8.5，含并发修改致 `stale` 拒写）。

## M1b — run_command + 分段器（shell 已定：PowerShell 5.1，[D-018](decision-log.md)）

**目标**：能跑命令、能清理。

- run_command（Job Object 进程树、env 过滤、编码回退、输出上限+artifact 溢出）+ 命令分段器（别名归一 + can't-parse→ASK）+ circuit breaker 全表 + audit.jsonl。
- **验收**：DENY 不可被 ALLOW 覆盖（用户/项目层）；取消运行中的多级子进程树无孤儿残留；分段器攻击样例表全绿；breaker 别名绕过样例全拒。

## M2 — 个人路径 E2E + 验收任务集

**目标**：真实云端模型跑通 golden 任务。

- `foundry login`（API key + DPAPI 存储）；错误分类学 + retry/backoff；token 记账 + observation masking；`finish` 工具 + 终止状态机 + ValidationClaim 核验；`foundry sessions [list|show|export]`（评审修正：V1 CLI 需求补 owner）；`FOUNDRY.md` 仓库说明文件（[D-019](decision-log.md)）。
- golden 任务集 5–10 个（修测试/加测试/重构/答疑；样例仓库 = 自建 fixture 进 repo，[D-020](decision-log.md)）+ 失败遥测报告脚本。
- **验收**：需求 §8.2 + §8.5（个人半边）；`completed` 造假用例（引用不存在事件/exit code 不符）被拒。

## M3 — 公司 Gateway

**目标**：公司电脑可用。**入场门（[D-022](decision-log.md)）：先取得 Gateway 的 tool-call 流式脱敏夹具**（普通响应 / 工具调用与续传 / usage / 限流 / 断流 / 畸形事件），再动手实现——"Responses-compatible"可能只覆盖对话而不覆盖 agentic 工具续传。

- `responses` adapter（必选）+ CredentialSource（内网 auth 机制，[OQ-6](open-questions.md)）+ TLS/proxy 实测（含 `FOUNDRY_CA_BUNDLE`）。
- capability probing + 降级路径；per-model profile（Claude 系 edit_format/prompt 变体）；managed policy 层（ProgramData DENY floor）。
- **验收**：公司环境跑通 ≥1 个 golden 任务；**canary 泄漏套件**（需求 §8.4）在真实凭证路径上通过；token 过期/重获取/限流/断流/畸形调用/超时各有有界测试；capability probe 结果落盘；managed DENY 实测不可放松。Claude 支持按观察到的协议单独决定接受或推迟。

## M4 — 打包加固 + 披露

**目标**：可分发、可交代。

- wheelhouse 流水线（pip-compile hashes → download → CI 完备性检查）；干净机安装验收（需求 §8.1）。
- 威胁模型文档 + 首次运行披露文案；秘密 choke point 审计；`foundry doctor`。
- headless `foundry exec`（ASK→DENY fail-closed，JSONL 事件输出）——事件架构应使其接近免费，视余量决定进 V1 或顺延。

## V2 候选（明确不承诺顺序）

session resume（schema 已就绪）｜LLM 摘要压缩（Compacted 事件已预留）｜`update_plan` 可见计划｜skills/斜杠命令｜subagent seam 启用｜MCP 桥（IR 已对齐）｜shadow-git checkpoint/undo｜Python-only repo map（stdlib ast）｜restricted-token 沙箱方向调研。
