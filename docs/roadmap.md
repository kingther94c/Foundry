# Foundry — Roadmap

> 排序原则（批判-架构 #1 采纳）：最大不确定项不上关键路径；每个里程碑以"可运行 + 可验收"收口。
> v0.1 的排序错误已修正：原"ChatGPT 登录最优先"随 blocked 结论（[D-009](decision-log.md)）自然解除；个人路径 API key 与公司 Gateway 同协议族，骨架先行不再有认证阻塞。

## M0 — Walking skeleton（接口冻结里程碑）

**目标**：`foundry` 能在样例仓库开一个交互会话，用只读工具回答一个问题，全程事件流驱动。

- 冻结四大接口：IR（conversation.py）、事件协议（events.py）、ModelBackend 协议、SessionStore 行信封。
- loop + ContextManager 投影（无 masking）+ 2 个工具（`read_file`、`list_files`）+ 最小 rich UI。
- backend：`replay` + `openai_compat`（用**本地端点**冒烟——无凭证可验证，回应"开发环境没有 API"的约束）。
- SessionStore 落盘 + 首个 golden transcript 回归测试。
- **验收**：无网络无凭证机器上全测试绿；本地模型端到端答对一个代码问答。

## M1 — 全工具面 + Policy

**目标**：能改代码、能跑命令、能被拦住。

- 8 个工具全量（apply_patch 锚定格式 + 宽容梯度；run_command Job Object/env 过滤/编码回退；workspace 边界模块）。
- PolicyEngine 六步流水线 + 命令分段器 + circuit breaker + 审批 UI（once/session/always）+ audit.jsonl。
- 脏工作区策略（baseline + 脏文件 ASK）。
- **验收**：需求 §8.3 负面用例全绿（越权拒绝、路径逃逸样例、DENY 不可覆盖、取消无孤儿进程、ASK 超时=DENY）。

## M2 — 个人路径 E2E + 验收任务集

**目标**：真实云端模型跑通 golden 任务。

- `foundry login`（API key + DPAPI 存储）；错误分类学 + retry/backoff；token 记账 + observation masking；终止状态机 + ValidationClaim 核验。
- golden 任务集 5–10 个（修测试/加测试/重构/答疑）+ 失败遥测报告脚本。
- **验收**：需求 §8.2 + §8.5（个人半边）；`completed` 造假用例被拒。

## M3 — 公司 Gateway

**目标**：公司电脑可用。

- Gateway 信息落地（[OQ-6](open-questions.md)）：协议 adapter 选型收敛、credential source、TLS/proxy 实测（含 `FOUNDRY_CA_BUNDLE`）。
- capability probing + 降级路径；per-model profile（Claude 系 edit_format/prompt 变体）；managed policy 层（ProgramData DENY floor）。
- **验收**：公司环境跑通 ≥1 个 golden 任务；capability probe 结果落盘；managed DENY 实测不可放松。

## M4 — 打包加固 + 披露

**目标**：可分发、可交代。

- wheelhouse 流水线（pip-compile hashes → download → CI 完备性检查）；干净机安装验收（需求 §8.1）。
- 威胁模型文档 + 首次运行披露文案；秘密 choke point 审计；`foundry doctor`。
- headless `foundry exec`（ASK→DENY fail-closed，JSONL 事件输出）——事件架构应使其接近免费，视余量决定进 V1 或顺延。

## V2 候选（明确不承诺顺序）

session resume（schema 已就绪）｜LLM 摘要压缩（Compacted 事件已预留）｜`update_plan` 可见计划｜skills/斜杠命令｜subagent seam 启用｜MCP 桥（IR 已对齐）｜shadow-git checkpoint/undo｜Python-only repo map（stdlib ast）｜restricted-token 沙箱方向调研。
