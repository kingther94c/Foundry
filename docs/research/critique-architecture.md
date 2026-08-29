# Critique: architecture/delivery

### [high] §3.1 / 交付顺序 - 把『ChatGPT 认证优先完成』作为第一里程碑是排序错误:它是全项目不确定性最高的一项,却不被任何其他组件依赖。官方虽有 Sign-in-with-ChatGPT 流程(developers.openai.com/codex/auth.md:浏览器 OAuth 走 localhost:1455 回调;device-code 需 workspace admin 显式开启),但第三方 harness 使用 ChatGPT 订阅 OAuth 在 ToS 上是『容忍』而非承诺(github.com/openai/codex/discussions/8338),且 Windows 上 localhost 回调有已知失败案例(community.openai.com/t/codex-cli-login-fails-on-windows-.../1381736)。草稿 3.1 自己就写了『若无受支持方案标记 blocked』——即第一里程碑自带失败预案,却让全部骨架排在它后面等。
- **Why**: 若 OAuth spike 卡 2-4 周(回调被公司网络拦、ToS 结论不明、refresh 语义要逆向验证),loop/tools/policy/session 全部零进度;而这些恰是 Foundry 的真正资产。最坏情形:auth 判 blocked,项目在没有任何可运行物的状态下停摆。
- **Fix**: 重排里程碑:M0 = walking skeleton(CLI → AgentRuntime → 2 个 tool(read_file/list_files)→ PolicyEngine → JSONL),后端用普通 OpenAI API key——它和公司 Gateway 同为 Responses 协议,代码 90% 直接复用到 3.2;ChatGPT OAuth 降级为并行、带 timebox(如 1 周)和明确退出条件的 spike。文档增加显式里程碑序列(M0 骨架 → M1 全 tools+policy → M2 公司 Gateway → M3 ChatGPT auth → M4 加固)。
- **Ask user**: 是否同意把第一个可运行里程碑改为『API key(或公司 Gateway)驱动的 walking skeleton』,ChatGPT 登录降级为并行的、带时间盒的 spike?

### [high] §3.2 / §3.3 apply_patch - 文档从未询问公司 Gateway 究竟暴露哪些模型 ID,却隐含假设『Responses-compatible 就够』。apply_patch 的补丁语法(V4A)是训练进 GPT 系列的私有格式:官方文档列出的支持模型仅 GPT-5.1/5.2/5.4/5.5(developers.openai.com/api/docs/guides/tools-apply-patch);连 Azure 托管的 GPT-4.1 都因 Codex v0.80.0 不再注入格式说明而产出坏补丁(github.com/openai/codex/issues/14046)。若公司 Gateway 是 Responses facade 后面接 Claude/Gemini/内部模型,V4A 补丁产出会格式崩坏(Claude 系训练偏 str_replace 式编辑)。
- **Why**: apply_patch 是 V1 唯一的写文件工具——它对着公司唯一可用的模型失灵,整个公司路径就失灵,而这要到 M2 集成时才被发现,返工波及 tool schema、loop 提示词和测试集。
- **Fix**: (a) 把『获取 Gateway 模型清单 + 是否支持 built-in apply_patch tool type + parallel tool calls + streaming 事件差异』列为 Day-1 发现类交付物;(b) 编辑工具按 per-model-profile 设计:格式(V4A / str-replace / unified diff)是 ModelBackend 的 model profile 属性,ToolExecutor 只负责统一校验与落盘,格式不得烧死在 tool 定义里;(c) 无论哪种格式,必须显式在 system prompt 注入格式说明,不依赖『模型天生会』。
- **Ask user**: 公司 Gateway 具体暴露哪些模型 ID?是原生 OpenAI 模型,还是 Responses facade 后面接 Claude/Gemini 等非 GPT 模型?能否拿到 Gateway 的协议差异文档?

### [high] §2 组件清单 - 缺少离线测试/回放设施:ModelBackend 只列了两个真后端,组件清单里没有任何 record/replay 机制,也没有 loop 的测试策略。agent loop 是全项目最复杂的状态机,但按现设计只能用真实 token 或公司 Gateway 凭证做集成测试。
- **Why**: CI 无法离线运行(与 3.6 的离线交付哲学自相矛盾)、测试不可复现、烧订阅额度;更致命的是补救时机:等 loop 写完再补 replay,会发现请求构造(prompt 拼装、tool schema 序列化、截断)已散落各处无法拦截,只能重构。Codex 自己就以 JSONL rollout 实现会话录制与重放/恢复(github.com/openai/codex 的 codex-rs/core/src/rollout/),证明这必须是第一天的接口约束。
- **Fix**: 把 ModelBackend 明确为三实现:OpenAI / CorporateGateway / ReplayBackend(读取录制的 request-response JSONL 夹具);要求 SessionStore 事件(或旁路录制文件)完整到能逐字节重建每次模型请求。写入验收标准:全部 loop/policy/tool 测试必须在无网络、无凭证的机器上通过。

### [high] §2 / §3.4 ASK 流程 - 缺少 UI 事件流与审批通道组件。PolicyEngine 返回 ASK,但组件图里没有任何把审批请求送达用户并拿回决定的机制;streaming 输出如何到达 CLI 也未定义——AgentRuntime 和 CLI 之间没有接口。
- **Why**: 没有 typed 事件流(model_delta / tool_begin / tool_end / approval_request / token_usage / termination),CLI 只能直接耦合进 runtime 内部,未来 IDE/服务复用即成空话;非交互场景(脚本、CI)下 ASK 的语义完全未定义——是阻塞、超时还是放行?这是安全边界上的空洞。
- **Fix**: 定义 AgentEvent 联合类型与单一 event sink/observer 接口,CLI 只是订阅者;approval 建模为异步 request/response 事件对;硬性规定非交互模式下 ASK → DENY(fail-closed)并写入 session 事件。

### [high] §2 / §3.3 缺 ContextManager - 没有 context/prompt 管理组件。3.3 限制了轮数/次数/时间/输出大小,但没人负责 context window:长会话溢出时的行为未定义;system prompt 由谁按后端/模型拼装未定义;『输出大小』没有 per-tool 语义——read_file 是否分页、search_text 最大命中数、run_command 的 stdout/stderr 交织与截断保留策略(head+tail?)全部缺失。另一个 Windows 具体雷:中文环境 subprocess 输出是 GBK/cp936,不定义解码策略第一天就是乱码进模型上下文。
- **Why**: 『SessionStore 记录的事件』≠『发给模型的 context』,这两个概念不分离,replay、截断、未来压缩、以及 3.5 的审计都做不成;第一个跑 npm test 输出 2MB 的任务就会把 loop 打爆或把预算烧光。
- **Fix**: 增加 ContextManager 组件:拥有 transcript→model-context 投影、per-tool 截断规则(字节上限 + 显式 [truncated] 标记 + 完整输出落盘供 read_artifact 取回)、溢出策略(V1 允许只做『终止 + reason=context_exhausted』,但必须写明);规定子进程输出解码:UTF-8 优先、回退 cp936、errors=replace。

### [high] §3.2 / §3.3 错误处理 - 无错误分类学与 retry/backoff 策略。『错误转换』在 ModelBackend 一句带过,但没有共享 taxonomy:retryable(429+Retry-After、5xx、网络抖动)/ auth(mid-stream 401 → 触发 AuthProvider refresh 后重试一次)/ fatal(context_length_exceeded、内容拒绝)/ policy 拒绝,四类的归属和响应完全未定义。
- **Why**: 没有它,AgentRuntime 无法决定『重试还是终止』,3.3 的『重复失败必须有限终止』无从实现,两个 backend 会各自发明异常体系导致 loop 充满 isinstance 分支;此外双 Foundry 实例并发 refresh 同一 refresh token(rotation 场景)会互相打掉登录态,这必须在 AuthProvider 契约里写锁/单飞语义。
- **Fix**: 定义 FoundryError 层级(TransientError/AuthError/FatalError/PolicyDenied),backend 契约要求把 provider 原始错误映射到该层级并保留原始 payload 供日志;retry(指数退避 + 次数上限 + 尊重 Retry-After)放在 AgentRuntime,不放在各 backend;AuthProvider.refresh 要求进程内单飞 + 文件锁防多实例竞争。

### [high] §2 并发模型 - asyncio 还是同步 loop 从未决定。streaming 响应、Ctrl+C 取消、子进程超时、模型的 parallel tool calls 全都压向并发模型选择,而它决定每一个接口签名(ModelBackend.stream() 返回 iterator 还是 async iterator;ASK 审批是阻塞回调还是 await;取消是 CancelledError 还是轮询标志)。
- **Why**: 这是无法后补的地基:接口全部写完后从 sync 改 async 等于重写所有组件;反之先定下来只是几行签名。Windows 上 Python 3.12 默认 ProactorEventLoop 已支持 asyncio 子进程,技术上无阻碍。
- **Fix**: 现在决定并写入接口文档。建议:asyncio 核心(streaming、取消、超时天然表达),同步实现的 tool 用 asyncio.to_thread 包装;若团队坚持全同步,则明确 streaming 用生成器、取消用协作式轮询、并发工具调用串行——两者皆可,但必须择一成文。

### [medium] §3.3 取消与子进程 - 『cancel/timeout 必须清理子进程』是目标不是设计。Windows 没有进程组 SIGKILL:run_command 起的 cmd/pwsh 会再派生子进程,Popen.kill() 只杀直接子进程,孤儿进程(如挂着的 node/pytest)会继续持有文件锁污染 workspace。取消传播链(CLI Ctrl+C → runtime → 中断在途 HTTP streaming → 杀子进程树 → session 记 cancelled)也无人拥有。
- **Why**: 第一个被取消的 npm test 就会留下孤儿进程和被锁文件,下一个任务的 apply_patch 直接失败,用户对『清理』承诺失去信任。
- **Fix**: 硬性要求:run_command 用 Win32 Job Object(JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)托管子进程树,taskkill /F /T /PID 作兜底;明确取消传播路径归 AgentRuntime 所有;加验收测试:取消一个正在运行多级子进程的命令,系统无孤儿进程残留。

### [medium] §3.3 parallel tool calls - 并行 tool calls 只字未提。Responses API 模型一轮可以返回多个 tool call,草稿的 loop 描述全按单数流程写;一批 call 中一个是 ASK 时怎么办(全部阻塞?先执行 ALLOW 的?)也未定义。
- **Why**: wire 协议层若不接受一轮 N 个 call,真实模型第一次并发调用就会掉进『malformed』分支被拒绝执行,loop 直接与主流模型行为不兼容。
- **Fix**: V1 明确规格:协议层接受 N 个 tool call;执行层按顺序串行执行,每个独立过 PolicyEngine;结果按 call_id 全部回填后才进入下一轮。写入 loop 规格与 replay 测试夹具。

### [medium] §3.5 SessionStore - 『versioned JSONL』只有名字没有机制:version 放哪(文件头记录 vs 每事件)、事件类型 discriminator、新版本二进制读旧 session 的兼容策略全部缺失。更大的隐藏范围问题:V1 是否支持 session resume?Codex 的 rollout JSONL 明确服务于 resume(codex-rs/core/src/rollout/),而 Foundry 若不 resume,SessionStore 只是审计日志,schema 可以宽松;若要 resume,schema 必须能重建完整模型上下文——两者工作量相差数倍,却从未被决定。
- **Why**: schema 无演进规则,第一次改字段就让所有历史 session 不可读;resume 与否直接决定 SessionStore 与 ContextManager 的深度耦合程度,是范围问题不是实现细节。
- **Fix**: 首行 header 记录 {schema_version, foundry_version, session_id, git_baseline};每事件带 type discriminator;演进规则:只增不改不删,reader 跳过未知事件类型;并把 resume 明确划入或划出 V1。
- **Ask user**: V1 是否需要 session resume(中断后继续同一会话)?这决定 SessionStore 是纯审计日志还是可重建模型上下文的事实源,工作量差别很大。

### [medium] §3.6 依赖预算 - 『优先标准库和少量依赖』不是清单。被迫的选型一个都没做:HTTP+SSE 栈(stdlib urllib 做不了流式 SSE,需 httpx 或等价物)、Windows 凭证存储(keyring → 拉入 pywin32 二进制 wheel)、CLI 框架、schema 校验(pydantic 会拉入 pydantic-core 平台二进制)。离线安装意味着 internal-wheel-dir 必须含所有传递依赖的 cp312/win_amd64 wheel。另外 3.2 列了 TLS/proxy 配置项,却没覆盖公司 TLS 拦截代理的现实:certifi 默认 CA bundle 会握手失败,必须支持系统证书库(truststore)或可配置 CA bundle。
- **Why**: 任何一个含平台二进制的传递依赖缺 wheel,离线安装当场失败;CA 问题则会让公司路径在第一次连 Gateway 时就 SSLError,而这两个都是最后一刻才暴露的交付杀手。
- **Fix**: 交付 requirements.lock(锁版本 + hashes),附带自动检查:锁文件中每个包在 internal-wheel-dir 都有 cp312-win_amd64(或纯 py3)wheel;把『支持 truststore 或可配置 CA bundle + 企业代理(含 NTLM/PAC 场景确认)』写成 3.2 硬性需求。

### [medium] §3.3 read_artifact - read_artifact 是 8 个 tool 中唯一无语义定义的:『artifact』是什么(run_command 截断后落盘的完整输出?构建产物?测试报告?)、存哪、谁登记、与 read_file 的边界(是否受 workspace 限制)全部空白。
- **Why**: 无定义的 tool 无法写 schema、无法过 policy 分类、无法测试;若它能读 workspace 外路径,还是 3.3 文件边界规则的一个未审计后门。
- **Fix**: 推荐定义为:读取 ToolExecutor 因超过输出上限而落盘到 session 目录的完整原始输出(按 artifact_id 索引),天然与 ContextManager 截断策略配对,且不越出 session 目录;若无此用例则从 V1 移除。
- **Ask user**: read_artifact 想读的到底是什么?是 run_command 被截断后落盘的完整输出,还是别的工件?若没有明确用例,是否同意移出 V1?

### [medium] §3.5 完成条件 - completed 门禁不可机器执行:『所有验证声明都有真实命令和 exit code』需要一个声明 schema——模型的 validation claim 必须引用 SessionStore 里的 command 事件 id,否则核验只能靠人眼比对;且纯只读任务(解释代码、评审、回答问题)既无 diff 也无验证命令,按现行文字永远不能返回 completed。
- **Why**: 门禁若不可自动执行,就退化为提示词层面的君子协定,恰是这份文档想避免的『模型口头声称已验证』;只读任务被迫返回 partial 会污染状态语义。
- **Fix**: 定义 ValidationClaim{claim_text, command_event_id, exit_code} 结构,由 runtime 交叉核验事件流后才允许 completed;为无副作用任务增加 completed(no_changes) 路径,git status 干净即视为满足。

### [low] §3.6 打包结构 - 单 wheel 还是 core+cli 分包未决定。未来 IDE 插件/服务化复用时,runtime 若与 console/CLI 依赖纠缠,拆分成本高。
- **Why**: V1 就分两个 wheel 会让离线安装与版本对齐复杂化,收益为零;但完全不设边界,日后 CLI 代码会渗入 runtime。
- **Fix**: V1 保持单 wheel,但强制 import 分层:foundry.core(runtime 全部组件)不得 import foundry.cli,用 import-linter 或简单 CI 检查固化;未来拆包即零成本。
