# Foundry 决策日志

> 格式：每条决定一个编号。**状态**：`已确认`（用户拍板）/ `暂定`（Claude 建议、待用户确认）/ `已推翻`。
> 推翻一条决定时不删除，改状态并链接到新决定。

---

## D-001 全新独立 runtime，不 fork / 不调用现有 coding agent
- **日期**：2026-08-29　**状态**：已确认
- **内容**：Foundry 自己拥有 agent loop、tools、policy、provider、session 的全部代码和接口。可以研读 Codex、Claude Code、gemini-cli 等公开源码借鉴设计，但不 fork、不 vendored、不在运行时调用它们。
- **理由**：公司环境不允许安装官方 agent 产品（只能走公司 Gateway 的 API）；同时项目本身有学习目的（吃透 agent runtime 设计）。

## D-002 Python 3.12 + wheel 离线安装
- **日期**：2026-08-29　**状态**：已确认
- **内容**：固定 Python 3.12；构建标准 wheel；目标安装方式 `python -m pip install --no-index --find-links <internal-wheel-dir> foundry`；依赖锁版本、带 hashes；不要求 Node.js / Rust toolchain。
- **理由**：公司电脑软件源受限（JFrog Artifactory，Node/Rust 能否用视具体情况）；Python + 内部 wheel 目录是最确定可行的分发通道。

## D-003 V1 是 trusted-host，不宣称 sandbox
- **日期**：2026-08-29　**状态**：已确认
- **内容**：V1 只用于可信仓库。审批（policy）是行为约束，不是安全边界；文档和 CLI 首次运行时都要明确披露这一点。
- **理由**：Windows 上做真 sandbox（如 AppContainer/Job Object 限制）成本高且容易给人虚假安全感；诚实披露优于半吊子沙箱。

## D-004 交互式终端会话优先，headless 后补
- **日期**：2026-08-29　**状态**：已确认（用户第一轮回答）
- **内容**：V1 核心形态是交互式终端会话：对话、流式输出、副作用动作当场审批（ASK）。headless 一次性模式（`foundry exec` 之类）后续版本再补。
- **架构约束**：UI 与 AgentRuntime 从第一天解耦——runtime 对外只暴露事件流 + 审批回调接口，终端 UI 只是第一个消费者。这样 headless 模式只是换一个"自动回答审批"的消费者，不动 loop。

## D-005 ChatGPT 登录保持最高优先级
- **日期**：2026-08-29　**状态**：**已推翻 → [D-009]**（可行性研究确认 blocked，用户改选 API key）
- **内容**：个人路径的 ChatGPT 浏览器登录仍是第一优先硬性需求。
- **理由（用户原话大意）**：开发环境没有 OpenAI API key，无法用 API 验证；ChatGPT 订阅是唯一可用的真实模型访问，登录打通了才能做真实 E2E 验证。
- **风险与缓解**：这仍是全项目最不确定的一项（第三方使用 ChatGPT 订阅可能无受支持途径，见 [OQ-5](open-questions.md)）。缓解措施：
  1. runtime 主体开发不依赖真实模型——必须先有 **mock / record-replay ModelBackend**，让 loop、tools、policy、session 全部可离线测试；
  2. 评估用**本地 OpenAI 兼容端点**（LM Studio / Ollama 等）作为 dev-only backend 做冒烟验证（见 [OQ-8](open-questions.md)）；
  3. 可行性研究给出证据后再定 fallback 阶梯，不偷偷降级。

## D-006 公司 Gateway 是多模型的（含 Claude 等非 OpenAI 模型）
- **日期**：2026-08-29　**状态**：已确认（用户第一轮回答）
- **内容**：公司 Gateway 上挂多家模型。草稿"优先支持 OpenAI Responses-compatible"的假设**不成立为唯一假设**。
- **架构约束**：
  1. 内部消息 / 工具调用 / 流式事件必须是 **provider-agnostic 的自有规范**，各协议（Chat Completions / Responses / Anthropic Messages / Gateway 自有协议）各写窄 adapter；
  2. 工具面（尤其编辑工具的格式）不能绑死 OpenAI 特化格式（如 V4A apply_patch），需要按模型族可配置或选择中性格式（见 [OQ-7](open-questions.md)）；
  3. Gateway 具体协议、认证、模型清单待用户确认（见 [OQ-6](open-questions.md)）。

## D-007 设计优先级排序：可审计 / 少依赖 / 清晰实现 > 功能堆砌
- **日期**：2026-08-29　**状态**：已确认（由动机推导）
- **内容**：动机 = 公司合规约束 + 学习目的。用户用 Claude Agent SDK 攒过 mini-codex，体验一般——Foundry 要赢在"完全掌控 + 设计质量"，而不是功能数量。
- **推论**：宁可工具面窄而可靠；每个依赖都要过"值不值得进离线 wheel 目录"的审查；决策和取舍必须留痕（本文档）。

## D-008 Claude consumer OAuth 不属于 V1
- **日期**：2026-08-29　**状态**：已确认（草稿继承）
- **内容**：个人路径 V1 只做 ChatGPT/OpenAI；Claude 订阅登录不做。公司路径里的 Claude 模型走 Gateway，与 consumer OAuth 无关。

## D-009 个人路径 = OpenAI 平台 API key；ChatGPT 登录标记 blocked-with-evidence
- **日期**：2026-08-29　**状态**：已确认（用户第二轮回答，基于可行性研究证据）
- **内容**：调研（[research/auth.md](research/auth.md)）确认：非 Codex 第三方工具没有受支持方式用 ChatGPT 订阅做推理——官方 "Sign in with ChatGPT" 只给身份不给推理；Codex 订阅端点有 originator 白名单（非 Codex 客户端 403），使用即须冒充 Codex，违反 OpenAI ToS 与本项目章程，有封号先例。按 v0.1 §3.1 约定流程：标记 blocked、留证据、询问用户。用户选择个人路径改用 OpenAI 平台 API key。
- **配套**：开发验证不依赖 key——ReplayBackend（离线全覆盖）+ 本地 OpenAI 兼容端点（LM Studio/Ollama 冒烟，用户已接受）；真实云端 E2E 才消耗 key。推翻 [D-005]。

## D-010 编辑格式 = 模型中性的锚定 search/replace 信封
- **日期**：2026-08-29　**状态**：已确认（用户第二轮回答）
- **内容**：apply_patch 采用"信封（Add/Delete/Update File）+ 锚定文本 search/replace hunks"（不依赖行号），补丁作为单个不透明字符串参数；小文件 whole-file 逃生通道；格式说明显式进 system prompt。
- **理由**：Gateway 多模型（含 Claude），Codex V4A 是 GPT 私有训练格式（官方支持列表仅 GPT-5.x），Claude 用之崩坏；Claude Code/OpenHands/aider 独立收敛到锚定文本替换；aider 实测宽容应用梯度降 9 倍错误率。备选"按模型族双格式"被否（测试面翻倍），留 per-model profile 的 `edit_format` 降级字段。

## D-011 脏工作区 = 警告继续 + 脏文件写操作强制 ASK
- **日期**：2026-08-29　**状态**：已确认（用户第二轮回答）
- **内容**：会话开始记录 baseline（HEAD SHA + 脏文件清单）；对 baseline 已脏文件的 apply_patch 强制 ASK；`git checkout -- / reset --hard / clean / stash drop` 等销毁性命令进内置不可放松 DENY（circuit breaker）。

## D-012 Session schema 第一天可重放；resume 功能推 V2
- **日期**：2026-08-29　**状态**：已确认（用户第二轮回答）
- **内容**：JSONL 记录完整到可逐字节重建每次模型请求（同时是 ReplayBackend 测试的基础）；`foundry resume` 的功能层（列表/选择/状态校验）推 V2。

## D-013 PolicyEngine = 六步流水线 + deny-wins 合并律 + circuit breaker
- **日期**：2026-08-29　**状态**：暂定（Claude 依据 Claude Code Agent SDK 公开规范提出）
- **内容**：`pre_tool 回调 → DENY → ASK → mode 基线 → ALLOW → 交互审批(headless=DENY)`；规则跨层拼接、deny-from-anywhere-wins；固定优先序拒绝数字优先级；"can't parse → ASK"安全阀；硬编码 circuit breaker（.git、~/.foundry、销毁性命令）。

## D-014 依赖预算：stdlib 优先，运行时仅 rich（+可选 prompt_toolkit）
- **日期**：2026-08-29　**状态**：暂定（Claude 依据 Windows/Python 调研提出）
- **内容**：HTTP/SSE 自研于 stdlib（换 Windows 系统证书库零配置，公司 MITM 代理免配置）；DPAPI/Job Object 走 ctypes；不用 httpx/requests/keyring/psutil/pydantic/textual。理由与落选对比见 [design.md](design.md) §11。

## D-015 read_artifact = 超限工具输出的落盘取回
- **日期**：2026-08-29　**状态**：暂定（v0.1 未定义，Claude 采批判建议定义）
- **内容**：artifact = 本会话内工具输出超出上下文预算而落盘的完整原文，按 artifact_id 寻址，只读，仅限 session 目录。与 ContextManager 截断策略配对。

## D-016 并发模型 = asyncio 核心
- **日期**：2026-08-29　**状态**：暂定（Claude 提出）
- **内容**：streaming/取消/超时用 asyncio 表达（Windows ProactorEventLoop 支持子进程）；同步工具体 `asyncio.to_thread` 包装。接口签名以此冻结——事后从 sync 改 async 等于重写。

## D-017 finish 工具 = 终止状态与 ValidationClaim 的唯一产生通道
- **日期**：2026-08-29　**状态**：暂定（对抗评审发现 §6.3 门禁缺产生机制后补）
- **内容**：V1 工具面 8→9：`finish{status, summary, claims:[{claim_text, command_event_id}]}`；runtime 核验（事件存在、exit code 一致、git 核对、HEAD 未移动）后才发 Termination；不符降级 `partial`。交互会话普通 turn 不调 finish 正常结束；会话无 finish 关闭按上下文记 `cancelled`/`partial`。
- **理由**："completed 门禁机器可执行"若无结构化产生通道，就退化为解析自由文本的君子协定——验收项"造假被拒"将无实现载体。

## 评审修正记录（2026-08-29 对抗评审，详见 git history 与评审归档）
- §4.1 修机制矛盾：只读默认 = 内置 ALLOW 规则（步 5）；mutator 默认 = 落步 6 审批（非 ASK 规则，否则 accept_edits 与审批持久化永不生效）；脏文件 ASK = 内置 ASK 规则（步 3，压过 accept_edits）；补 mode 基线定义（dont_ask = fail-closed DENY）。
- 审批"永久"规则改写入用户层（按 workspace 键控）而非 workspace 内文件；breaker 加 `<workspace>/.foundry/` 写保护——堵 accept_edits 下自我提权。
- 只读白名单加参数约束（路径过 containment）；裸 git 移出白名单（防绕过硬化 git 工具）。
- apply_patch 语义统一为**逐文件原子**（原文混用 codex 全原子与 aider 部分应用）。
- 秘密 choke point 范围修正：字节级 exact-match、先于 base64、覆盖 artifact 写/读与事件发出；model_request 不落盘 auth 头。
- breaker 表 canonical 化（别名归一前置；补 `git restore`/`stash clear` 与 PowerShell/cmd 删除形式）。
- ReplayBackend 匹配契约：序号回放 + 结构断言（非逐字节，否则 prompt 微调红全套）；`foundry record` 重录工作流进 M0。
- M1 拆 M1a（文件工具，不被 OQ-13 阻塞）/ M1b（run_command + 分段器）；`responses` adapter 降为按需新增。
