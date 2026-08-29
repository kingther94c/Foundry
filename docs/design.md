# Foundry — 设计方案 v0.1

> 配套 [requirements.md](requirements.md) v0.2。本文回答"怎么建"：包结构、核心接口、数据流与关键机制。接口以 Python 伪代码给出——它们是**先冻结的交付物**（章程："先定义自己的接口，再独立实现"）。
> 借鉴出处标注为（codex）/（claude-code）/（gemini-cli）/（aider）/（openhands）/（luban），细节见 [research/](research/)。

## 1. 包结构

```text
foundry/
├── core/                    # 不得 import foundry.cli（CI 强制）
│   ├── events.py            # 事件与提交（op）类型，协议版本号
│   ├── conversation.py      # provider 无关的中间表示（IR）
│   ├── runtime.py           # AgentRuntime：唯一的 loop
│   ├── context.py           # ContextManager：投影/截断/masking/token 记账
│   ├── policy/              # PolicyEngine：规则、模式、六步流水线、命令分段器
│   ├── tools/               # ToolExecutor + 8 个工具实现 + workspace 边界
│   ├── backends/            # ModelBackend 协议 + adapters（openai_compat / responses / replay）
│   ├── auth.py              # AuthProvider + DPAPI 存储
│   ├── session.py           # SessionStore（JSONL）+ audit log
│   ├── httpc.py             # stdlib HTTP/SSE 客户端（~300 行）
│   └── winapi.py            # ctypes：DPAPI、Job Object、reparse tag
├── cli/                     # 终端 UI：事件订阅者 + 审批 UI（rich / prompt_toolkit）
├── prompts/                 # 版本化资产：base system prompt、patch 格式说明、permissions 段模板
└── __main__.py              # 入口：以 UTF-8 模式自举
```

## 2. 中间表示（IR）——一切的地基

两个 backend 共用一套 loop 的前提。**先冻结，任何 adapter 只做 IR ↔ 线协议互转。**

```python
# conversation.py（示意；全部 frozen dataclass，禁止 dict 裸奔）
class ContentBlock:      # MCP 形态对齐：text | tool_use | tool_result | image(预留)
    ...

@dataclass(frozen=True)
class Message:           # role: system | user | assistant | tool
    role: str
    blocks: tuple[ContentBlock, ...]

@dataclass(frozen=True)
class ToolCall:
    call_id: str         # 回填 result 时按此关联；一轮可有 N 个（并行 tool call 协议层必须接受）
    name: str
    arguments: str       # 原始 JSON 串；解析失败 = malformed，不执行

@dataclass(frozen=True)
class ToolResult:
    call_id: str
    blocks: tuple[ContentBlock, ...]
    is_error: bool

@dataclass(frozen=True)
class ModelTurn:         # 一次采样的产物
    text: str | None
    tool_calls: tuple[ToolCall, ...]
    usage: Usage         # 真实 usage 字段，不估算（luban：估算低 36%）
    raw: bytes           # 原始响应，进 session 以便 replay/审计
```

## 3. 事件协议（core ↔ UI 的唯一通道）

```python
# events.py — 双队列（codex）：UI 提交 Op，core 广播 Event
Op    = UserInput | ApprovalDecision | Interrupt | Shutdown
Event = TurnStarted | MessageDelta | ToolBegin | ToolOutputDelta | ToolEnd \
      | ApprovalRequest | TokenCount | TurnComplete | Termination | Error
PROTOCOL_VERSION = 1
```

- `ApprovalRequest{request_id, kind: command|patch|other, display: str, detail, options: [once, session, always, deny, abort]}`；loop 在对应 future 上挂起，直到 `ApprovalDecision{request_id, choice}` 提交。**display 串与实际执行对象同源**（what-you-approve-is-what-runs）。
- 审批是事件而非阻塞 `input()` ⇒ 审批中可 Interrupt、headless 可映射 ASK→DENY、未来 IDE 前端免费。
- UI 渲染任何模型/工具文本前：剥 ANSI/OSC 序列 + rich markup 转义（终端注入是真实攻击面）。

## 4. AgentRuntime：loop 形态

```python
async def run_turn(user_input):
    enqueue(user_input)
    while True:
        history = context_manager.project(session)        # transcript → 模型可见上下文
        turn = await backend.sample(history, tools=profile.tool_schemas)
        session.record_model_turn(turn)
        if not turn.tool_calls:
            return end_of_turn(turn)                       # 普通 turn 结束，对话继续；
                                                           # 任务收口经 finish 工具 → 终止状态机（§9）
        for call in turn.tool_calls:                       # V1 串行；每个独立过 policy
            decision = await policy.evaluate(call)
            result = await execute_or_reject(call, decision)
            session.record_tool(call, decision, result)
        # 软上限：连续工具轮数、每任务 token 上限；触发 → 干净终止 partial/blocked
```

- 中途用户输入进队列，下一次 `project()` 时合并（codex mid-turn steering）。
- 采样请求 = 无状态全量历史；同一 thread 用稳定 `prompt_cache_key`（backend 能力允许时）。
- 错误分类学（`TransientError/AuthError/FatalError/PolicyDenied`）由 adapter 负责映射、runtime 统一处理 retry（指数退避 + Retry-After + 上限）；AuthError → refresh 单飞（进程内锁 + 文件锁防多实例竞争）后重试一次。
- 取消传播链归 runtime 所有：Ctrl+C → Interrupt op → cancel 在途 HTTP 读 → TerminateJobObject 击杀子进程树 → `Termination(cancelled)` 落盘。
- asyncio 核心；阻塞型工具体 `asyncio.to_thread`。

## 5. ContextManager

- `project(session) -> list[Message]`：**transcript ≠ 模型上下文**，投影是显式函数（openhands 事件流思想），保证同一 session 重放得到逐字节相同请求。
- 组装顺序（codex 分层）：`[base system prompt] + [由 policy 配置生成的 permissions 段——告诉模型真实的自动放行/审批边界] + [FOUNDRY.md/AGENTS.md（信任门控，字节上限；D-019，M2 交付）] + [environment context: OS/shell/cwd/git 状态] + [对话历史]`。
- 输出预算：每个工具输出有字节上限，超限时 head+tail 截断 + `[truncated: N bytes, artifact_id=…]` 标记，完整输出落盘 session 目录由 `read_artifact` 取回。
- **observation masking**（V1 的"压缩"）：早于最近 N（默认 5）轮的工具输出投影为一行 stub（`[output elided; re-run tool if needed]`）；system/任务/最近轮完整保留。证据：与 LLM 摘要等效且零额外依赖（arXiv 2508.21433；SWE-agent +3pt）。LLM 摘要压缩 = V2，届时以 `Compacted` 事件进 log（openhands condensation-as-event）。
- token 记账：累计真实 usage，逼近 backend 上下文上限 → 干净终止 `partial(context_exhausted)`，绝不让请求 400。

## 6. PolicyEngine

```python
# 六步流水线（claude-code Agent SDK 公开规范，逐步可测）
def evaluate(call) -> Decision:            # Decision = ALLOW | ASK(reason) | DENY(reason)
    1. pre_tool 回调（配置的 Python callable 或 stdin-JSON 子进程；可改写输入）
    2. DENY 规则（全层合并；命中即终——任何 ALLOW 不可翻转）
    3. ASK 规则
    4. mode 基线（default/accept_edits/plan/dont_ask）
    5. ALLOW 规则
    6. 交互审批（headless: DENY）
```

- 规则语法：`tool` / `tool(pattern)`，fnmatch，目标键 per tool（run_command→分段后命令串；文件工具→workspace 相对路径）。固定优先序，无数字优先级（gemini-cli 反例）。
- 层合并：**分层唯一权威 = 需求 §4.3**（设置类按优先链；规则类跨层拼接后 deny>ask>allow，managed DENY 为地板，仓库签入层只接受 deny/ask）。
- 命令分段器：针对唯一指定 shell 写保守 tokenizer，**分段前先做别名归一**（`rm/ri/del/erase → Remove-Item` 类，breaker 匹配依赖它）；shell 已定 = Windows PowerShell 5.1（D-018），操作符集：`;`、管道 `|`（**5.1 无 `&&`/`||`**，模型误用得 parse error，prompt 已预告）；`$( )`、反引号、`&` 调用符、重定向、env 前缀 → 不可信分段 → ASK。分段器单独成模块、表驱动测试（这是全项目安全敏感度最高的 300 行）。
- circuit breaker 表（第 0 步，先于一切）：见需求 §4.1 的显式表（含 `<workspace>/.foundry/` 写保护——审批持久化机制的配套，防 accept_edits 下自我提权）。
- **pre_tool 改写输入 → 从第 0 步重入流水线**；breaker/规则/审批展示/执行全部绑定最终输入（不变量，进测试表）。
- 审批持久化：`always` 写入 **`~/.foundry/` 用户层配置、按 workspace 键控**（不写 workspace 内文件）；生成规则为精确串匹配、无模式泛化，下次评估生效；模型输出不能触发持久化；ASK 超时 DENY。
- 启动时 lint 规则集：未知工具名、必然 dead 的规则 → 警告。

## 7. ToolExecutor 与工具实现要点

- 统一入口：`execute(call: ToolCall, ctx) -> ToolResult`；每个工具 = `validate(args) -> Invocation`（失败即 malformed，不进审批）+ `run(inv)`（gemini-cli validate-then-execute）。
- 工具带 `kind: readonly | mutator` 标签驱动默认 policy 与（V2）并行度。
- **workspace 边界检查**（文件工具共用）：双侧 `realpath(strict)` + `normcase` + `commonpath`；逐组件 `lstat` 查 reparse tag；拒绝 ADS/设备名/UNC/`\\?\`/盘符相对/尾点尾空格。独立模块 + 攻击样例测试集。
- `run_command`（winapi.py）：`CreateJobObjectW + KILL_ON_JOB_CLOSE + AssignProcessToJobObject`；`CREATE_NEW_PROCESS_GROUP`；优雅取消先 `CTRL_BREAK_EVENT` 后 `TerminateJobObject`；`taskkill /T /F` 兜底；Popen→Assign 之间的毫秒级窗口列为已知风险。bytes 捕获 → UTF-8 优先 / OEM 回退（比较 U+FFFD 数取优）；子进程 env = 过滤后的最小集 + `PYTHONUTF8=1`。
- `apply_patch`：纯进程内 Python（补丁体绝不过 argv/子进程——codex #15003）；**逐文件原子语义**（需求 §5.3）：全量解析定位后，任一 hunk 失败的文件整个不触盘，通过的文件 temp+`os.replace` 写入，per-文件/per-hunk 状态报告指示只重发失败文件；宽容梯度 精确→CRLF/BOM→行尾空白→失败带"最近似行"提示；锚文本 0/>1 命中结构化报错；保留原编码与 EOL 风格；可选 post-edit `compile()` 检查。补丁格式说明由解析器常量生成进 prompts/（同源，防漂移；应用语义同文说明）。
- `git_status/git_diff`：硬化参数 + 剥 `GIT_*` env（防 fsmonitor/hooks/pager 代码执行面）。
- `read_artifact(artifact_id, offset?)`：artifact_id 为不透明 token，仅经当前会话内存索引解析（拒绝一切路径语义），仅限本会话产物；输出走与其他工具相同的截断+脱敏路径。
- `finish(status, summary, claims)`：任务收口工具；runtime 按需求 §6.3 核验 claims 与 git 状态后发 Termination（D-017）。

## 8. ModelBackend 与 AuthProvider

```python
class ModelBackend(Protocol):
    async def sample(self, messages, tools, params) -> AsyncIterator[StreamEvent]  # 终以 ModelTurn 收束
    async def capabilities(self) -> Capabilities   # 每 session 探测一次并落盘（luban probing）

# Capabilities: parallel_tool_calls, streaming, prompt_caching, usage_fields, custom_tools, max_context
```

- Adapters（V1）：`openai_compat`（Chat Completions——个人 API key、本地端点、多数企业网关）、`replay`（夹具驱动，测试唯一指定）。**`responses` 与 `anthropic_messages` 同为按需新增**（OQ-6 的 Gateway 答案或个人路径实际需要触发时指派里程碑并记决策；codex 教训：不为"以防万一"养双协议）。M0 冻结 ModelBackend 协议前，用 Responses 线协议做一次纸面走查（IR↔Responses 映射表）以保住冻结承诺。
- backend 配置表字段对齐 codex `ModelProviderInfo`（需求 §3.2）；每 model profile 额外携带：`edit_format: anchored_patch | whole_file`、`tool_schema_dialect`、`system_prompt_variant`。
- 流式：SSE 逐行解析（httpc.py）；idle 超时；断流按 TransientError 重试（上限）。
- AuthProvider：`get_credentials() / login() / logout() / refresh()`；凭证对象只注入 httpc 请求头，ContextManager/工具层拿不到引用（架构性隔离）；DPAPI 加解密（winapi.py），解密失败 = 未登录。

## 9. SessionStore 与终止状态机

- 行信封 `{ts, ordinal, type, v, payload}`；类型集：`session_meta / model_request / model_response / tool_call / tool_result / policy_decision / approval / command_exec / token_usage / capability_probe / validation_claim / git_baseline / termination`。
- 写入 choke point 单一函数：exact-match 替换自持凭证（100% 可验收）+ best-effort 模式扫描；command 输出存原始 bytes(base64)。
- `model_request` 完整到可重建（**auth/Authorization 头除外**——不落盘，重放时由 HTTP 层重注入）⇒ ReplayBackend 直接以 session 文件为夹具；resume（V2）= 同一重放机制。**Replay 匹配契约**：按序号回放 + 结构断言（工具调用序列/关键字段），请求全文 diff 作为测试产物输出供人工审查；逐字节一致性单独作 resume-ready 测试（否则 prompt 微调会红掉全部夹具、或盲回放验证不了任何东西）。夹具重录：`foundry record`（对本地端点，M0 交付）。
- 终止状态机：`completed` 需 (a) runtime 自动 git 核对、(b) 全部 `validation_claim` 与 `command_exec` 事件交叉验证（event_id 存在且 exit code 一致）、(c) HEAD 未移动；否则降级 `partial`。只读任务走 `completed(no_changes)`。
- `~/.foundry/audit.jsonl` 独立追加（工具不可写）。

## 10. 测试策略（第一天开始）

1. **单元**：policy 流水线 = (rules, mode, call)→decision 决策表；命令分段器攻击样例表；路径边界攻击样例表；patch 应用梯度表；编码回退表。
2. **回归**：golden transcripts + ReplayBackend 跑全 loop（无网络无凭证）；prompt/loop 任何改动必须过全套。
3. **E2E 冒烟**：本地 OpenAI 兼容端点（免费）→ 个人 API key（少量）→ 公司 Gateway（可用时）。
4. **失败遥测闭环**（aider 实践）：离线报告脚本从 session JSONL 统计 patch 首次成功率、失败分类、每任务轮数/token——"加 repo map / 开模糊匹配 / 某 backend 降级 whole-file"这类决定以数字驱动。

## 11. 依赖决策表

| 领域 | 选择 | 落选与理由 |
|---|---|---|
| HTTP/SSE | stdlib `http.client`+`ssl`（自研 ~300 行） | httpx(7 wheels)/requests(5)：certifi 不信 Windows 证书库，公司 MITM 代理必炸；stdlib 默认信任系统库=零配置 |
| 凭证 | DPAPI via ctypes | keyring：6 wheels + 后端 2560 字节上限 |
| 进程树 | Job Object via ctypes | psutil：C 扩展 wheel + PID 快照法丢孤儿 |
| 渲染 | rich（4 wheels） | textual：~9 wheels + app 框架过重 |
| 审批输入 | prompt_toolkit（可选，2 wheels） | — |
| 校验 | dataclasses + 手写 | pydantic：平台二进制 wheel |
| 构建 | hatchling（仅构建机） | setuptools 无增益 |

## 12. 已知接受风险（威胁模型附录）

TOCTOU（检查后替换为 junction）；hardlink 不可路径检测；Popen→Job assign 毫秒窗口；DPAPI 在本地账户强制改密后失效（按未登录处理）；混合编码流解码 best-effort（原始 bytes 兜底）；NTLM/Kerberos 代理不支持（407 显式报错）；managed DENY 不防本机管理员篡改安装（边界在 Gateway 服务端）。
