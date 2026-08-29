# Foundry — Runtime Requirements v0.2

> 本文档取代另一 AI 草拟的 v0.1。所有与 v0.1 的实质性差异都有编号决定（[decision-log.md](decision-log.md)）或调研证据（[research/](research/)）支撑。
> **状态标记**：无标记 = 已确认（用户拍板或由已确认决定推导）；`[暂定 D-0xx]` / `[暂定 OQ-xx]` = Claude 建议、待用户确认，登记在 [decision-log.md](decision-log.md)（状态=暂定）或 [open-questions.md](open-questions.md)。

**平台**：Windows　**语言**：Python 3.12　**安装**：wheel，`pip --no-index --find-links` 离线安装

---

## 0. Why Foundry（动机与设计优先级）

v0.1 从头到尾没有回答"为什么要做这件事"。动机决定优先级，现在明确（[D-007](decision-log.md)）：

1. **公司环境硬约束**：公司电脑不允许安装 Codex CLI / Claude Code 等外部 agent 产品，模型访问只能走公司 Gateway 的 API；软件分发走内部源（JFrog Artifactory），Node/Rust 工具链可用性不确定，Python + 内部 wheel 目录是最确定的通道。
2. **学习目的**：吃透 coding-agent runtime 的设计。此前用 Claude Agent SDK 攒过 mini-codex，体验一般——Foundry 要赢在完全掌控与设计质量。

**由此推导的设计优先级**（冲突时按此排序裁决）：
可审计 > 少依赖/离线可装 > 实现清晰可维护 > 功能数量。

**章程（不变）**：Foundry 自己拥有 agent loop、tools、policy、provider、session 的全部代码与接口。可研读 Codex、Claude Code、gemini-cli、aider、OpenHands、luban 等公开源码借鉴设计，但不 fork、不 vendored、不在运行时调用它们、**不在协议层冒充它们**。

```text
个人电脑：Foundry -> OpenAI API key -> OpenAI 模型          （变更：原 ChatGPT 登录已确认 blocked，见 §3.1）
公司电脑：Foundry -> 公司 Gateway    -> 多家批准模型（含 Claude）
开发环境：Foundry -> ReplayBackend / 本地 OpenAI 兼容端点   （新增：无凭证可测）
```

三边共用同一套 agent loop 和 tools，只切换认证与协议 adapter 层。

## 1. 产品形态（v0.1 缺失，[D-004](decision-log.md)）

- **V1 核心形态 = 交互式终端会话**：用户在终端与 agent 对话，流式输出，副作用动作当场审批（ASK）。
- **架构强制**：UI 与 AgentRuntime 第一天解耦。runtime 对外只暴露**类型化事件流 + 异步审批请求/响应**；终端 UI 只是第一个订阅者。headless 模式（`foundry exec`，ASK 一律按 DENY 处理、fail-closed）因此近乎免费：M4 按余量进 V1，否则 V2（[OQ-16](open-questions.md)）。
- **CLI surface（V1 最小集）**：
  - `foundry` — 在当前目录开交互会话
  - `foundry login / logout` — 个人路径凭证管理
  - `foundry sessions [list|show|export <id>]` — 会话查看与导出
  - `foundry doctor` — 环境自检（Python 版本、git、长路径、代理/TLS 连通性）
- **输出契约**：五种终止状态映射到进程 exit code：`completed=0, partial=10, blocked=11, failed=12, cancelled=13`；最终报告含证据（验证命令 + exit code + diff 摘要）。
- **配置**：TOML，`~/.foundry/config.toml`；命名 profile（如 `personal` / `corporate`），profile 含 backend + model + policy 预设。全部分层与优先级唯一定义于 §4.3。

## 2. 架构组件

```text
Foundry CLI（终端 UI，事件流订阅者）
    │  Submission(op) ▼ / Event ▲       ← 双队列，审批 = 异步事件对
AgentRuntime（唯一的 loop）
    ├── ContextManager     （新增：transcript→模型上下文投影、截断、token 记账）
    ├── ModelBackend       （协议 adapter：OpenAI 兼容 / Responses / Replay / 本地端点）
    │     └── AuthProvider （凭证获取/存储/刷新，token 只在 HTTP 注入层可见）
    ├── PolicyEngine       （六步流水线，见 §4.1）
    ├── SessionStore       （versioned JSONL，单一事实源，可确定性重放）
    └── ToolExecutor       （文件/命令/Git 工具；Windows trusted host）
```

相对 v0.1 的新增组件及理由：
- **ContextManager**（批判-架构 #5）：v0.1 只限制了"工具输出大小"，没人拥有上下文预算。职责：per-tool 输出截断（显式 `[truncated]` 标记，完整输出落盘为 artifact）、旧工具输出的 observation masking、每轮 token 记账（读真实 usage 字段）、上下文逼近上限时的干净终止（V1 不做 LLM 摘要压缩，证据：arXiv 2508.21433 表明 masking 与 LLM 摘要等效）。
- **事件流协议**（批判-架构 #4）：类型化事件（`turn_started / message_delta / tool_begin / tool_output_delta / tool_end / approval_request / token_count / turn_complete / error / termination`）+ 版本号；**枚举的单一权威是 design.md 的 `events.py`**，本清单为引用。审批建模为 `approval_request` 事件与后续 `approval_decision` 提交，loop 挂起等待——不是工具内部的阻塞 `input()`。
- **ReplayBackend**（批判-架构 #3）：ModelBackend 的第三个实现，读取录制的 request/response 夹具。**验收标准：全部 loop/policy/tool 测试必须在无网络、无凭证的机器上通过。**

## 3. 模型路径

### 3.1 个人路径：OpenAI API key（v0.1 的 ChatGPT 登录已确认 blocked）

按 v0.1 §3.1 自己的规则（"限定研究后无受支持方案 → 标记 blocked，记录证据并询问"），2026-08-29 调研结论（证据：[research/auth.md](research/auth.md)）：
- 官方 "Sign in with ChatGPT" 只授予身份（姓名/邮箱/头像），不给推理与订阅额度，且需申请审核；
- Codex 订阅推理端点 `chatgpt.com/backend-api/codex` 有 originator 白名单，非 Codex 客户端 403；使用它必须冒充 Codex，违反 OpenAI ToS（绕过保护措施）与本章程，有真实封号案例；
- OpenAI 认可的程序化路径只有平台 API key。

**决定（[D-009](decision-log.md)，用户 2026-08-29 确认）**：个人路径 = OpenAI 平台 API key（Chat Completions / Responses，`api.openai.com`）。ChatGPT 登录标记 **blocked-with-evidence**，不实现、不冒充。

硬性需求：
- `foundry login` 引导录入 API key（或从环境变量读取）；凭证经 **DPAPI（ctypes CryptProtectData）** 加密存储于 `~/.foundry/auth.json`，原子写入；解密失败视为"未登录"引导重新 login，而非致命错误。
- token/key 不得进入日志、错误信息、事件流或模型上下文；enforcement 见 §6.1（同一脱敏函数应用于三个点：session 写入、事件发出、上下文组装）。
- **开发验证不依赖 key**：ReplayBackend 覆盖全部单测/回归；本地 OpenAI 兼容端点（LM Studio / Ollama，走同一个 Chat Completions adapter）做免费真实模型冒烟；真实云端 E2E 才消耗 key。

### 3.2 公司 Gateway（多模型，含 Claude）

已知事实（[D-006](decision-log.md) / [D-022](decision-log.md)）：Gateway 挂多家模型；**OpenAI 系走 Responses API**；token 由**内网 auth 流程**获取后配合 Gateway URL 使用；Claude 模型存在但线协议未验证。

硬性需求：
- **内部表示 provider-agnostic**：conversation / tool-call / tool-result / 流式事件采用 Foundry 自有中间表示（内容块结构对齐 MCP 形态，为未来桥接留门）；各线协议（Chat Completions / Responses / Anthropic Messages / Gateway 自有）各写窄 adapter，adapter 只做互转，不拥有 loop。
- backend 配置表（借鉴 codex `ModelProviderInfo`）：`{name, base_url, protocol, credential_source, http_headers, query_params, request_max_retries, stream_idle_timeout_ms, capabilities_override}`；每个生效配置值记录 provenance（来自哪一层）。
- **capability probing + 优雅降级**（luban 教训）：企业 Gateway 必然偏离标准（流式模式、并行 tool call、缓存、usage 字段）；每个可选特性都要有降级路径并在 session 中记录探测结果。**不静默模拟不支持的语义**。
- **CredentialSource 合同**（吸收 Codex 版）：`acquire / expiry / refresh / logout`，机制可插拔（HTTP 交换 / 内部可执行 / 浏览器 SSO 待发现）；凭证以不可打印的 handle 流转，仅 HTTP 传输层解析——认证与协议 adapter 分离。
- **M3 入场门**：先取得 Gateway 的 **tool-call 流式脱敏夹具**（普通响应 / 工具调用与续传 / usage / 限流 / 断流 / 畸形事件）再动手实现——"Responses-compatible"可能只覆盖对话而不覆盖 agentic 工具续传，这是最贵的返工风险。
- TLS/proxy：默认用 `ssl.create_default_context()`（自动信任 Windows 系统证书库——公司 MITM 代理场景零配置）；支持 `FOUNDRY_CA_BUNDLE`/`SSL_CERT_FILE` 覆盖；代理读 `HTTPS_PROXY`/`NO_PROXY`；proxy/自定义 CA/mTLS **非 V1 验收项**（能力保留），407 Negotiate 明确报错。
- 待确认（[OQ-6](open-questions.md)）：内网 auth 的具体机制、endpoint 形态、模型清单。

### 3.3 开发/测试路径（新增）

- **ReplayBackend**：SessionStore 记录的请求/响应可逐字节重建每次模型调用（这也是 [D-012] resume-ready schema 的基础）；golden transcripts 进 repo 作回归夹具。
- **本地端点**：任何 OpenAI Chat Completions 兼容的 `base_url` 即可用（LM Studio/Ollama）；仅用于开发冒烟，不是产品路径。

## 4. Policy 与审批（trusted-host）

### 4.1 PolicyEngine：六步流水线（[D-013](decision-log.md)，采纳 Claude Code Agent SDK 公开规范）

```text
0 circuit breaker（硬编码表，先于一切；见下）
1 pre_tool 回调（可选扩展点，可 ALLOW/DENY/ASK/改写输入）
2 DENY 规则   ← 任意层的 DENY 不可被任何层的 ALLOW 覆盖
3 ASK 规则   （内置 ASK 规则也在此：如 D-011 脏文件写操作）
4 permission mode 基线
5 ALLOW 规则 （内置只读工具 ALLOW 规则在此）
6 交互审批（headless / dont_ask 下此步 = DENY，fail-closed）
```

**各机制在流水线中的确切位置**（评审修正：v0.2 初稿把"工具默认 ASK"写成了会杀死 accept_edits 与审批持久化的形态）：
- 只读工具（`list_files/search_text/read_file/read_artifact/git_status/git_diff`）的默认放行 = **内置 ALLOW 规则（第 5 步）**。
- mutator（`apply_patch/run_command`）的"默认 ASK" = **不是 ASK 规则**，而是什么都没命中时落入第 6 步交互审批。
- `accept_edits` 模式 = 第 4 步基线对 workspace 内 apply_patch 给 ALLOW（因为第 3 步无内置 ASK 规则拦它，故能生效）。
- D-011 脏文件强制 ASK = **内置 ASK 规则（第 3 步）**——正因位于 mode 之前，它能压过 accept_edits。
- 用户"永久批准"生成的 ALLOW 规则位于第 5 步，因此能对 mutator 生效（第 3 步没有兜底 ASK 规则挡路）。

**mode 基线定义**（每种一行，任何 mode 都不越过第 0/2 步）：
- `default`：未命中 → 第 6 步交互审批。
- `accept_edits`：workspace 内 apply_patch → ALLOW；其余同 default。
- `plan`：apply_patch 与非只读 run_command → DENY（带 reason 返回模型）；计划批准后切换 mode。
- `dont_ask`：未命中 → **DENY**（fail-closed；headless 复用此语义）。不存在"未命中即放行"的 mode。

不变量（写测试表验证）：回调的 ALLOW 不跳过 DENY/ASK 规则；DENY 逐层不可放松；**pre_tool 改写输入后从第 0 步重新进入流水线，breaker/规则/审批展示/执行全部绑定改写后的最终输入**；规则匹配前必须先做**命令分段**（见 §4.2）。测试表至少含：accept_edits 放行干净文件 patch；accept_edits 下脏文件 patch 仍 ASK；持久化 ALLOW 规则对 run_command 生效；dont_ask 未命中即 DENY。

- 决策词汇固定 `ALLOW / ASK / DENY`，规则形如 `tool` 或 `tool(pattern)`（fnmatch），固定优先序 deny > ask > allow，**拒绝数字优先级**（gemini-cli 教训）。
- DENY 返回给模型一个机器可读 reason 作为 tool result（loop 存活，模型可调整）；用户 Abort 才终止 turn。
- 审批粒度：`一次 / 本会话（仅内存）/ 永久`。**永久规则写入 `~/.foundry/` 用户层配置（按 workspace 键控），不写 workspace 内文件**——否则 accept_edits 下模型可自我提权（评审发现）；生成规则为**精确串匹配**（无模式泛化），下次 policy 评估时生效。审批 UI 展示的命令/diff 与实际执行对象必须是同一字符串（what-you-approve-is-what-runs）；ASK 超时默认 DENY。模型输出永远不能触发规则持久化。
- **审批绑定与失效**（吸收 Codex 版）：一次审批绑定「归一化操作 + cwd + env 策略 + 有效期」，任一字段变化即失效需重新审批；执行前重新校验时效性不变量（防审批与执行之间状态漂移）。
- policy 决定落盘时记录：rule ID、策略版本/摘要、操作摘要、reason、时间——事后可复核"当时为什么放行"。
- **内置 circuit breaker（第 0 步，硬编码表，任何 ALLOW 规则/mode/回调不可覆盖）**：

  | 类别 | 条目（canonical 形式，分段器先做别名归一：`rm/ri/del/erase → Remove-Item` 等） |
  |---|---|
  | 保护写路径 | `.git/`、`~/.foundry/`（凭证/policy/audit）、session 目录、`<workspace>/.foundry/`（若存在） |
  | 销毁性 git | `checkout -- <path>`、`restore`（覆盖工作区形式）、`reset --hard`、`clean`、`stash drop`、`stash clear` |
  | 递归删除 | 仓库根/用户主目录/盘根为目标的 `Remove-Item -Recurse`、`rmdir /s`、`del /f /s`、`rm -rf` 等价形式 |

- **plan mode**：只读探索，见 mode 基线；成本极低、价值高。
- 内置只读安全命令白名单（不提示），**带参数约束**（评审修正）：路径参数必须通过与文件工具相同的 workspace containment 检查（拒绝盘符绝对/UNC 参数）；**裸 `git` 不入白名单**——模型应使用硬化的 git_status/git_diff 内置工具，经 run_command 的 git 走正常 policy（否则白名单绕过 §5.2 的硬化）。
- 启动时校验规则：匹配不到任何工具或永远不可能命中的规则要警告（silent-dead-rule 是 Claude Code 踩过的坑）。

### 4.2 run_command 的诚实设计

命令行为安全判定的前提是解析命令，而 Windows 有 cmd/PowerShell/git-bash 三种语法，字符串前缀匹配已被反复证明可绕过（Claude Code CVE-2025-66032 等）。V1 规则：
- 固定**唯一 shell = Windows PowerShell 5.1**（`powershell.exe -NoProfile -Command`，[D-018](decision-log.md)）：预装零依赖；分段器按 5.1 语法（`;`、管道 `|`；**5.1 无 `&&`/`||`**）；system prompt 明确告知模型"无 `&&`，用 `;` 代替"。
- 自动 ALLOW 只匹配保守分段后的每一段；命令含替换/链式/重定向等元字符而无法可信分段时，**一律 ASK**（"can't parse → ASK" 安全阀）。
- DENY 字符串匹配仅作纵深防御，文档明确其非边界属性。
- 执行：`cwd` 强制显式、超时、输出上限；**Job Object（ctypes，KILL_ON_JOB_CLOSE）托管进程树**，cancel/timeout 一次性击杀全部子孙，`taskkill /T /F` 兜底；无持久 shell 会话（stateless per call，mini-swe-agent 教训）。
- **env 过滤**：子进程默认得到最小核心集（PATH/SYSTEMROOT/TEMP/PYTHON* 等），默认剔除 `*KEY*/*TOKEN*/*SECRET*/*PASSWORD*/AWS_*`；用户可显式 passthrough；Foundry 自身凭证永不进入子进程 env。
- 输出捕获 bytes-first：UTF-8 优先解码、OEM(cp936) 回退、`errors='replace'`；子进程注入 `PYTHONUTF8=1`；session 保留原始 bytes（base64）以便事后重解码。

### 4.3 配置分层与仓库信任（本节为分层的唯一权威定义，§1 与 design.md 引用此处）

- **设置类（标量，如 model、shell、超时）**：CLI flag > 环境变量（`FOUNDRY_*`）> 项目本地（.gitignored）> profile（用户配置内选定）> 用户 `~/.foundry/config.toml` > 内置默认。**secrets 只能来自环境变量或凭证存储，禁止出现在仓库配置与普通 CLI 参数中**。
- **policy 规则类**：各层规则列表**拼接**后统一按 deny > ask > allow 评估（层间先后不影响结果，deny-from-anywhere-wins）；例外：**managed 层**（管理员 ACL 目录如 `C:\ProgramData\Foundry\policy.toml`）的 DENY 为地板，且仓库签入层只接受 deny/ask。
- **仓库内配置只能收紧**（新增 DENY/ASK），永远不能新增 ALLOW；endpoint、credential、headers、proxy 等连接类配置只能来自机器本地层。首次在新目录使用仓库提供的任何配置/说明文件前，一次性信任提示，记录于 `~/.foundry/trusted.json`。
- **仓库说明文件（[D-019](decision-log.md)，M2 交付）**：V1 支持读取项目根 `FOUNDRY.md`（兼容 `AGENTS.md`），声明构建/测试命令与仓库注意事项，注入上下文（信任门控 + 字节上限）；验证命令优先级：任务指定 > 仓库文件声明 > 模型自选。
- **"公司固定 DENY 不可覆盖"的诚实定位**（批判-安全 #4）：在未被篡改的安装内，运行时任何途径（任务、审批、CLI flag）都不能放松 managed DENY——这可以做到；对抗本机管理员故意改源码/配置——做不到，真正的边界在 Gateway 服务端（模型白名单、请求日志）。此定位必须写进披露文档，不做过度承诺。

### 4.4 威胁模型与披露（新增章节，v0.1 完全缺失 prompt injection）

- **威胁模型声明**：所有 tool result（文件内容、命令输出、git diff）都是不可信输入，可能携带指向模型的注入指令。无 sandbox 时唯一真实防线是 PolicyEngine 独立于模型意图对每个副作用把关。
- "可信仓库"的定义写实：= 你愿意让其中任意文件内容既被执行、也被当作指令读取的仓库。
- 首次运行 + 每会话开始打印固定披露：无 sandbox；每个被批准的命令以完整用户权限运行（可读写所有文件含凭证、访问网络、读环境变量）；仅在可信仓库使用。
- V1 明确非目标：不防御恶意仓库内容；不防本机管理员绕过 managed policy。V2 方向（restricted token / 容器）写进 roadmap 以示阶段性选择。
- 终端渲染安全：模型输出与工具 stdout 渲染前剥离 ANSI/OSC 序列（OSC 52 剪贴板注入是真实攻击面），rich markup 一律转义。
- 不自动 commit、push、PR、publish 或 deploy（继承 v0.1）。

## 5. Agent loop 与 tools

### 5.1 Loop

- 形态：`while 模型返回 tool calls：policy → 执行 → 回填 results → 重采样`；模型给出无 tool call 的最终消息即 turn 结束。中途用户输入进入队列，下一次采样时合并（codex mid-turn steering）。
- 每轮请求为无状态全量历史（stateless full-history），便于 replay 与不持久化状态的 Gateway。
- 限制：软性连续工具轮数上限（可配置）、单命令超时、每任务 token 上限（可选配置）；同一文件连续编辑失败 2-3 次后强制 read_file（SWE-agent 错误复合曲线教训）。
- **失败指纹**（吸收 Codex 版）：重复失败按「归一化操作 + 错误类」计数，**错误文本变化不重置计数器**；超过小阈值即终止或转人工，避免模型换个措辞无限重试。
- **协议层必须接受一轮 N 个 tool call**（Responses/Claude 都会并行发）；V1 执行层按顺序串行，每个独立过 policy，全部回填后进入下一轮。
- malformed / 未知 / 越权 tool call 不执行，返回结构化错误给模型。
- 错误分类学：`TransientError（429/5xx/网络，指数退避重试，尊重 Retry-After）/ AuthError（refresh 后重试一次）/ FatalError（context 超限、内容拒绝）/ PolicyDenied`；retry 逻辑归 AgentRuntime，不散落各 backend；持续限流以 `blocked(rate_limited)` 终止而非无限等待。
- 并发模型：**asyncio 核心**（[D-016](decision-log.md)；streaming、取消、超时的自然表达；Windows ProactorEventLoop 支持子进程）；同步工具用 `asyncio.to_thread` 包装。

### 5.2 V1 工具面（9 个，各自带输出上限与截断标记）

| 工具 | 说明 | 默认 policy |
|---|---|---|
| `list_files` | mtime 排序，条目/深度上限 | ALLOW |
| `search_text` | 命中上限（~50），过多时提示收窄而非静默截断 | ALLOW |
| `read_file` | 窗口式（~250 行）带行号与省略计数；登记 read-before-edit 状态 | ALLOW |
| `apply_patch` | 见 §5.3 | 落第 6 步交互审批；accept_edits 基线下 workspace 内 ALLOW（机制见 §4.1） |
| `run_command` | 见 §4.2 | 落第 6 步交互审批 |
| `read_artifact` | 读取本会话因超限落盘的完整工具输出。artifact_id 为**不透明 token**，只经当前会话内存索引解析（不接受任何路径语义），仅限本会话产物；输出与其他工具输出走同一截断+脱敏路径回入上下文（v0.1 未定义，现定义 [D-015](decision-log.md)） | ALLOW |
| `git_status` / `git_diff` | 硬化调用：`git --no-pager -c core.fsmonitor= -c core.hooksPath=`，剥离 `GIT_*` env，`GIT_TERMINAL_PROMPT=0` | ALLOW |
| `finish` | 模型显式提交任务收口：`{status, summary, claims:[{claim_text, command_event_id}]}`；runtime 据 §6.3 核验后发 Termination 事件（[D-017](decision-log.md)，ValidationClaim 的唯一产生通道） | ALLOW |

工具描述文字 = 短段落 + 一个示例（SWE-agent ACI 证据：简洁、合并、带护栏的工具面实测提分）。V1 冻结为此 9 个，不再增补；`update_plan`（可见计划）列为 V2 候选。

### 5.3 apply_patch（[D-010](decision-log.md)：模型中性锚定格式）

- 格式：**信封 + 锚定文本 search/replace hunks**——信封支持 Add/Delete/Update File（借鉴 V4A 的文件操作结构），Update hunk 为不依赖行号的精确锚定文本替换（借鉴 Claude Code str_replace 与 aider SEARCH/REPLACE 的收敛结论）；补丁作为**单个不透明字符串参数**传输（code-in-json 教训）。理由：Gateway 多模型（含 Claude），V4A 是 GPT 私有训练格式，Claude 用之格式崩坏。
- 格式说明显式写进 system prompt，不依赖"模型天生会"；grammar 文档与解析器同源生成（codex #2578 教训）。
- 应用语义（评审修正，原文混用了 codex 原子性与 aider 部分应用两种互斥结论）：**逐文件原子**——先解析并定位**全部**文件的全部 hunk；任一 hunk 定位失败的文件整个不触盘，其余全部 hunk 通过的文件以 temp + `os.replace` 原子写入；返回 per-文件/per-hunk 状态报告，指示模型**只重发失败文件的全部 hunks**。此语义同步写进 prompts/ 的模型侧格式说明（错误文案与行为不得分叉）。宽容梯度：精确匹配 → CRLF/BOM 归一 → 行尾空白容差 → **失败带提示**（引用最近似的真实行）；>80% 模糊匹配默认关闭（静默错位比失败更糟）。
- 唯一性：锚文本命中 0 或 >1 处时结构化报错（含出现次数）。
- read-before-edit：编辑未读文件拒绝；文件自上次读取后变化时校验锚文本仍唯一命中。
- 保持目标文件原有编码与行尾风格写回；非 UTF-8 文件显式报错。
- 可选 post-edit 语法检查（Python: `compile()`/pyflakes 级别）结果附在 tool result（SWE-agent +3pt 证据）。
- 逃生通道：小文件 whole-file 重写模式；弱模型 backend 可在 profile 级降级为 whole-file。
- 补丁体绝不经 argv 或子进程传输（Windows ~32KB argv 上限，codex #15003）。

### 5.4 Workspace 边界

- workspace = 显式传入或默认 cwd 的单一目录，**必须位于 git 仓库内**（否则启动拒绝并给出明确错误）；monorepo 允许以子目录为 workspace；多仓库任务 = 非目标。
- **文件工具的路径参数 = workspace 相对逻辑路径**（吸收 Codex 版）：绝对路径、设备路径、UNC 一律拒绝——越界在接口层就不可表达。
- 路径 containment：`realpath(strict)` 双侧解析 + `normcase` + `commonpath`，逐组件检查 reparse point（junction/symlink，`st_reparse_tag`）；显式拒绝 ADS（`file:stream`）、设备名（CON/NUL/COM1…含带扩展名形式）、UNC/`\\?\` 前缀、盘符相对路径（`C:foo`）、尾部点/空格。目标环境**无法创建 symlink 且无 Developer Mode**，故 V1 一律拒绝 reparse point 而不做"安全目标"分类。hardlink 与 TOCTOU 列为已知接受风险写进威胁模型。
- 诚实披露：workspace 限制只约束文件工具；run_command 天然不受其约束，真正的闸门是 policy。
- **脏工作区（[D-011](decision-log.md)）**：警告后继续；会话开始记录 baseline（HEAD SHA + `git status --porcelain=v2` + 相关未跟踪文件的元数据/摘要——单次 `git diff` 不足以覆盖未跟踪文件）；对 baseline 中已脏文件的 apply_patch **强制 ASK**；销毁性 git 命令内置 DENY（见 §4.1 circuit breaker）。
- **乐观并发写保护**（吸收 Codex 版）：每次写入前校验目标文件自上次读取以来未变（内容摘要），变化则以 `stale` 失败并回传最新上下文，绝不覆盖；结果记录新旧摘要。分支名醒目显示但**不承载安全语义**（不硬编码 main 特殊行为）。

## 6. Session 与完成条件

### 6.1 SessionStore

- 位置：`~/.foundry/sessions/<session-id>/events.jsonl` + 同目录 `artifacts/<sha256>`（**内容寻址**，吸收 Codex 版：事件只引用 digest/大小/媒体类型/截断态，artifact 不可按任意路径取回）；永不放在 workspace 内，文件工具对该目录内置 DENY；原子追加；保留期用户可配置。
- 行信封从第一天冻结：`{ts, ordinal, type, v, payload}`；首行 header 记录 `{schema_version, foundry_version, session_id, workspace, profile, model}`。演进规则：只增不改不删，reader 跳过未知类型。**事件类型集的单一权威是 design.md §9**（git baseline 记为 `git_baseline` 事件而非 header 字段，与"ref 移动记为事件"的 §6.3 语义一致）。
- 记录事件类别：model request/response（完整到可重建请求——**resume-ready，[D-012]**；**auth/Authorization 头除外**，重放时由 HTTP 层重新注入，绝不落盘）、tool call/result（含 policy 决定与 reason）、approval（展示串 + 用户决定）、command（argv、exit code、时长、原始输出 bytes）、token usage、validation、termination、capability probe、git_baseline。
- **Resume 功能本身推 V2**；V1 只承诺 schema 可重放（这同时是 ReplayBackend 测试的基础，一石二鸟）。
- **崩溃恢复语义**（吸收 Codex 版）：读取时容忍截尾的最后一条记录；**没有终止事件的 session 一律判为 `interrupted`，绝不可被误认为 completed**；终止/审批/命令完成事件写入后立即 flush。
- 秘密处理（可验收表述，范围经评审修正）：
  - (a) **字节级 exact-match**：对 Foundry 自持凭证的 UTF-8 与 UTF-16LE 字节编码，在唯一写入 choke point 上、**在 base64 编码原始输出之前**做替换——保证范围 = "已知自持凭证的字面字节序列"，不做更大承诺；
  - (b) **artifact 落盘与 read_artifact 回读经过同一 choke point**（否则超限输出成为旁路）；
  - (c) 同一脱敏函数应用于三点：session/artifact 写入、事件发出（ToolOutputDelta/Error 离开 runtime 之前）、上下文组装；
  - (d) 常见 token 格式/高熵串模式扫描标注 best-effort；
  - (e) session log 本身按敏感数据对待（用户 profile 下、文档声明）。
- telemetry：无。本地 JSONL 即全部记录。

### 6.2 审计日志

独立于 session 的 `~/.foundry/audit.jsonl`（luban 模式）：每个工具调用（含 DENY）追加 `{ts, workspace, tool, target, decision, outcome}`；文件工具硬编码不可修改它。这是 no-sandbox V1 的诚实补偿控制。

### 6.3 完成条件（机器可执行）

- **产生通道（评审修正：原文缺失）**：验证声明经 `finish` 工具提交（§5.2，[D-017](decision-log.md)）——模型调用 `finish{status, summary, claims}`，runtime 核验后才发 Termination；交互会话中不调用 finish 的普通 turn 正常结束（对话继续），会话最终状态在 finish 或会话关闭时落定（无 finish 而关闭 → 按上下文记 `cancelled`/`partial`）。
- `completed` 门禁：finish 时 runtime 自动执行 git_status/git_diff 核对；每条 `ValidationClaim{claim_text, command_event_id}` 与事件流交叉核验（事件存在、为 command_exec 类型、exit code 与声明一致）——任一不符则拒绝 `completed`（降级 `partial` 并说明）。
- baseline 完整性：完成检查时校验 HEAD 未被移动（模型经 run_command commit/reset 会污染证据链）；任何 ref 移动记为事件并降级 `partial`。
- 只读任务（答疑/评审）走 `completed(no_changes)` 路径：git status 与 baseline 一致即满足。
- **claims 可以为空**：显式声明"本任务未执行验证"是有效披露；编造或推断的成功不是（吸收 Codex 版）。
- **变更归属分离报告**：最终报告区分「本次会话触碰的文件」与「baseline 已脏 / 会话期间被并发改动的文件」；只声称能证明的文件版本，不声称行级归属。
- 其他状态：`partial / blocked / failed / cancelled`（外加恢复时判定的 `interrupted`），每种都必须带 termination reason 事件；一个 session **有且仅有一次**终止事件。

## 7. 打包与依赖

- Python 3.12；纯 Python wheel（`py3-none-any`）；build backend hatchling（锁版本）。
- **依赖预算（[D-014](decision-log.md)，枚举制，新增依赖需过决策记录）**：
  - 运行时必选：`rich`（终端渲染，4 个纯 py wheel）
  - 运行时可选：`prompt_toolkit`（审批/输入增强，+wcwidth 共 2 wheel）
  - **明确不用**：httpx/requests（stdlib http.client + ssl 自研 ~300 行，换取 Windows 系统证书库零配置与 0 wheel）、keyring（DPAPI ctypes 直连，且其后端有 2560 字节上限）、psutil（Job Object ctypes）、pydantic（dataclasses + 手写校验）、textual、tree-sitter。
- 锁定：`requirements.in → pip-compile --generate-hashes → wheelhouse（pip download --only-binary :all:）`；安装 `pip install --no-index --find-links wheelhouse --require-hashes foundry`；CI 检查 wheelhouse 对 cp312/win_amd64（或纯 py3）完备。
- 代码分层：单 wheel，但 `foundry.core` 不得 import `foundry.cli`（CI 强制），未来拆包零成本。
- 全部 `open()` 显式 `encoding='utf-8'`；CI 开 `-X warn_default_encoding`。

## 8. V1 验收标准（v0.1 完全缺失）

1. **安装验收**：干净 Windows 11 + Python 3.12，无外网，`pip --no-index` 从 wheelhouse 安装成功并跑通 `foundry doctor`。
2. **golden 任务集**（自建小型样例仓库 fixture 进 repo，[D-020](decision-log.md)；5–10 个）：修失败测试、加测试、小重构、只读答疑各类至少一个；每个规定预期终止状态与证据形态。回归方式（评审修正）：ReplayBackend 按序号回放 + 对请求做**结构断言**（工具调用序列/关键字段），请求全文 diff 输出为测试产物供人工审查；"逐字节重建"单独作为 resume-ready 测试。prompt/loop 改动须过全套结构断言；夹具重录工作流（`foundry record`，对本地端点重录）为 M0 交付物。
3. **负面用例**：越权/未知/malformed tool call 被拒且 loop 存活；DENY 不可被 ALLOW 覆盖（含 managed 层）；路径逃逸样例（junction、ADS、`..`、设备名、盘符相对、UNC）全部被拒；取消运行中的多级子进程树无孤儿残留；ASK 超时 = DENY；`completed` 在验证声明造假（引用不存在事件）时被拒。
4. **canary 泄漏套件**（吸收 Codex 版）：以金丝雀凭证跑通全流程，断言其**不出现在**控制台、prompt、session journal、artifact、异常字符串与诊断导出中——这是"凭证不泄漏"从声明变为可验收的唯一方式。
5. **脏工作区矩阵**：staged / unstaged / untracked / 重命名 / 删除 / 非 UTF-8 与二进制 / 并发修改 / 编辑 baseline 已脏文件——证明既有工作不被丢弃也不被错误归属；**测试夹具复原不得使用破坏性 git 命令**。
6. **崩溃恢复**：截尾 journal 不可被判为 completed；每种终止状态都能从脱敏日志重建。
7. **离线测试验收**：全部单元/集成测试在无网络、无凭证机器上绿。
8. **真实 E2E**：个人 API key 与（可用时）公司 Gateway 各跑通至少一个 golden 任务。

## 9. 非目标（V1 明确不做）

sandbox（诚实 trusted-host）；session resume（schema 就绪，功能 V2）；LLM 摘要压缩（V1 用 masking + 干净终止）；MCP（内部表示对齐其形态即可）；subagents（留 seam）；多仓库任务；浏览器/网络工具；embeddings/RAG；自动 commit；skills/斜杠命令（V2）；Claude consumer OAuth；NTLM/Kerberos 代理。
