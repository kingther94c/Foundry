# Critique: product/scope

### [high] 全文 / TL;DR - 文档从头到尾没有说明 Foundry 存在的真实驱动约束（motivation）。为什么不直接用 Codex CLI？Codex 本身就支持 ChatGPT 登录 + 通过 config.toml 的 model_providers 指向任意 OpenAI-compatible endpoint（即公司 Gateway），且是 Apache-2.0 开源。文档唯一的暗示是 §3.6 '不要求安装 Node.js 或 Rust toolchain'（Codex CLI 是 Rust 二进制）和离线 wheel 安装，这暗示真实约束可能是'公司环境禁止安装未批准的外部 agent 二进制/禁止外呼'或'审计合规'或'个人学习练手'。
- **Why**: 驱动约束不同，V1 优先级完全不同：若是公司合规驱动，则 Gateway 路径 + PolicyEngine + JSONL 审计是核心，ChatGPT 个人路径是 nice-to-have（甚至可砍）；若是学习驱动，范围纪律和'不 fork'条款的意义都变了；若是离线安装驱动，打包/依赖锁定就是第一公民。现在两条路径都写成'优先'，没有裁决依据，团队会平均用力，最难的部分（ChatGPT 逆向合规登录）可能吃掉全部预算。
- **Fix**: 在文档开头增加 'Why Foundry / 非目标' 一节：一句话写清真实约束（例如'公司机器不允许安装/运行 Codex 或任何外部 coding agent，且必须走内部 Gateway 并留审计日志'），并据此显式排序两条路径的优先级。
- **Ask user**: Foundry 真正的驱动约束是什么——公司禁止安装 Codex 等外部 agent？必须走内部 Gateway 留审计？离线无 Node/Rust 环境？还是个人学习项目？（这个答案决定个人路径和公司路径谁是 M1）

### [high] §2 V1 范围 / §3.4 审批 - 交互模型完全未定义：Foundry 是交互式会话（TUI/REPL，用户中途回答 ASK、追加指令），还是一次性 headless runner（`foundry run "task"` 跑完出报告），还是两者都要？§3.4 的 ASK 审批和 §3.3 的 cancel 隐含交互性，但 §2 '接收明确的工程任务…返回带证据的结果' 又是 one-shot 措辞。对照物两者都有明确产品形态：Codex 有交互 TUI 和 `codex exec` 非交互模式（供 CI/pipeline），见 learn.chatgpt.com/docs/codex/cli。
- **Why**: 这是最上游的产品决定，几乎影响每个组件：ASK 在无人值守时是阻塞等待、自动 DENY 还是转为 blocked 状态退出？streaming 输出往哪渲染？Ctrl+C 取消语义？结果如何返回（stdout 最终消息？JSON 报告？exit code 映射 completed/partial/blocked/failed/cancelled？）。不定下来，PolicyEngine 和 SessionStore 的接口都没法冻结。
- **Fix**: V1 明确二选一（建议：交互式 CLI 会话为主，ASK 通过 stdin 提示回答；headless 模式若要，则规定 ASK 一律按 DENY 处理并以 blocked 终止）。同时补一节'输出契约'：五种终止状态到进程 exit code 的映射、最终结果的结构（含证据：命令 + exit code + diff 摘要）。
- **Ask user**: V1 的形态是交互式 CLI 会话、一次性 headless runner，还是两者都要？如果有 headless 模式，遇到 ASK 时应该自动拒绝并以 blocked 退出，还是阻塞等人？

### [high] 全文（缺失章节） - CLI surface 和配置故事完全缺失：没有任何子命令（run? login? logout? resume? status?）、flags、配置文件格式与位置（TOML/JSON？Windows 下放 %APPDATA%\foundry 还是 ~/.foundry？）、配置优先级（CLI flag > env > 项目配置 > 用户配置？）、也没有个人/公司两种 backend 的切换 UX（自动探测？`--backend corporate`？named profile？）。对照：Codex 用 ~/.codex/config.toml + 项目级 .codex/config.toml（仅在信任该项目时加载），有 model、approval_policy（untrusted/on-request/never）、named profiles（learn.chatgpt.com/docs/config-file/config-basic）。
- **Why**: §3.2 列了公司 Gateway 的 8 项配置（endpoint、headers、TLS、proxy…）但没说这些配置写在哪、什么格式、如何在个人机和公司机之间不串。没有 profile/切换机制，'两边共用同一套 loop 只切换连接层' 就只是口号——第一个 demo 就会卡在'怎么告诉 Foundry 用哪个模型'。
- **Fix**: 新增 §3.7 'CLI 与配置'：定义子命令集（至少 login/logout/run/resume/status）、配置文件格式和位置、优先级链、以及 profile 机制（personal/corporate 两个内置 profile，profile 内含 backend + model + policy 预设）。
- **Ask user**: 配置文件用什么格式、放哪里（建议 TOML，%APPDATA%\foundry\config.toml + 仓库级覆盖）？个人/公司模式怎么切换——机器级默认 profile、还是每次命令行指定？公司机上是否要禁止 personal profile 出现？

### [high] §3.3 Agent loop 与 tools（缺失） - Context 管理完全缺席：没有任何需求描述文件内容如何进入模型上下文（read_file 有无行数/字节上限、分页？）、run_command 输出如何截断后进入上下文、上下文窗口逼近上限时怎么办（compaction/摘要？直接失败？）、长任务多轮累积的 token 增长如何控制。§3.3 限制了'输出大小'但那是工具输出上限，不是上下文预算。对照物都把这当核心：Codex 有 /compact 与自动压缩（learn.chatgpt.com/docs/codex/cli），Claude Code 同样有 compaction。
- **Why**: 这是 V1 第一个会撞上的墙：中等规模仓库里读 3 个大文件 + 一次测试全量输出就能撑爆上下文，模型开始截断/失忆，'带证据的结果' 无从谈起。不写进需求，实现者会各自即兴发挥，且事后很难补（影响 tool 接口：截断标记、artifact 溢出都要在接口层预留）。
- **Fix**: 补硬性需求：(a) 每个 tool 输出有字节上限 + 显式截断标记；(b) 超限的完整输出落盘为 artifact，模型可用 read_artifact 分页读取（这也顺便给了 read_artifact 存在的理由）；(c) 追踪每轮 token 用量；(d) 明确上下文超限行为——V1 可以不做自动 compaction，但必须干净地以 partial/failed 终止并在 JSONL 里记录 termination reason=context_exhausted，而不是让请求 400。
- **Ask user**: V1 需要自动上下文压缩（compaction/摘要）吗？还是接受'超限即干净失败并报 partial'，把 compaction 推到 V2？

### [high] §3.3 V1 tools - read_artifact 出现在硬性工具清单里但全文没有定义：什么是 artifact？谁产生它（run_command 的溢出输出？测试报告文件？构建产物？session 日志片段）？存在哪、生命周期、寻址方式（id? path?）？另外工具集有一个未言明的假设：没有 write_file/create_file 工具，意味着新建文件只能靠 apply_patch——文档没说 apply_patch 是否支持创建/删除/重命名文件，也没定义 patch 格式。
- **Why**: 一个未定义的工具没法实现、没法写 policy 规则（read_artifact 是 ALLOW 还是要看 artifact 来源？）、也没法测试。apply_patch 若不能建新文件，'修改仓库' 的基本任务（加一个测试文件）直接做不了；若能，patch 格式（unified diff? Codex 的 apply_patch 自定义格式?）是模型能否稳定输出的关键产品决定。
- **Fix**: 为 read_artifact 写一段定义（建议：artifact = 本 session 内工具产生的、超出上下文预算而落盘的输出，按 artifact_id 寻址，只读，随 session 目录存储）；并为 apply_patch 补一句能力边界：支持 create/modify/delete 文件，格式采用 X（并注明借鉴来源，如 openai/codex 的 apply_patch 工具设计）。
- **Ask user**: read_artifact 的'artifact'指什么——工具溢出输出的落盘文件、任务产物（测试报告/构建输出）、还是两者？

### [high] 全文（缺失章节） - 没有任何 V1 成功标准、验收测试或 eval 故事。'怎么知道它能用了'完全没有答案：没有代表性任务集（golden tasks）、没有端到端验收场景（如'在仓库 X 修复一个失败测试并给出通过证据'）、没有两条 backend 路径 + 离线安装的冒烟测试矩阵、没有'借鉴失败经验'落成的负面测试（malformed tool call、循环重试、traversal 攻击样例）。
- **Why**: §3 全是行为约束（不得…必须…），零条'做到什么算完成'。没有验收定义，V1 的 done 是感觉，会无限滑动；而且没有任务集就无法在改 prompt/loop 时回归，每次改动都是盲改。
- **Fix**: 增加 §4 'V1 验收'：(a) 5–10 个固定验收场景（按任务类型：修 bug、加测试、小重构、只读答疑），每个规定预期终止状态与证据；(b) 安装验收：干净 Windows + Python 3.12，pip --no-index 装好并跑通一个任务；(c) 负面用例清单（越权 tool call 被拒、DENY 不可覆盖、超时清理子进程）。
- **Ask user**: V1 的代表性验收任务集应覆盖哪几类（修 bug / 加测试 / 重构 / 只读代码问答）？在哪个真实仓库上验收——公司仓库还是公开样例仓库？

### [high] §1 / §3.1 / §3.2（排序缺失） - 没有里程碑/路线图，且两个'优先'互相打架：§3.1 说'优先完成浏览器 ChatGPT 登录'，§3.2 说'优先支持 Responses-compatible Gateway'。更糟的是文档自己承认 ChatGPT 路径可能整个被 block（'若限定研究后仍无受支持方案，标记为 blocked'）——ChatGPT OAuth（auth.openai.com + localhost:1455 回调，credential 存 ~/.codex/auth.json）是为 Codex 设计的，第三方复用在 ToS 上高度存疑（见 learn.chatgpt.com/docs/auth 及社区讨论）。但文档没有写 blocked 之后的 fallback 计划（OpenAI API key 需要另行询问才准用）。
- **Why**: V1 的关键路径上放了一个自己都标注'可能不存在受支持方案'的组件，却没有排序和 Plan B。若 ChatGPT 路径 block，个人路径整条死掉，而团队可能已经烧了几周在浏览器登录上。风险最高的未知项应该最先探（spike），而不是和确定可行的 Gateway 路径并列'优先'。
- **Fix**: 加 §5 里程碑：M0 = 骨架 + Gateway 路径端到端跑通一个验收任务（技术上无未知）；M1 = ChatGPT 登录 time-boxed spike（比如 1 周），出'可行/blocked'结论；blocked 时的预案（如 OpenAI API key 作为个人路径替代）现在就写进文档待批，而不是到时候再问。
- **Ask user**: 如果限定研究后确认 ChatGPT 登录没有受支持的第三方方案，个人路径是接受降级为 OpenAI API key、直接砍掉个人路径只留公司 Gateway、还是 V1 整体等待？以及：公司 Gateway 和 ChatGPT 登录哪个是 M1？

### [medium] §3.4 / §2 PolicyEngine - '公司固定 DENY 不可被任务或临时批准覆盖'——但政策的来源、格式、分发和防篡改机制完全没定义。policy 规则写在哪（代码里硬编码？配置文件？）？公司规则和个人规则如何分层？在 trusted-host（用户是本机管理员）上，用户可以直接改配置文件甚至改源码，'不可覆盖'在技术上不可强制，只能是诚实 UI + 审计。对照：Codex 区分用户 config.toml 与管理侧 requirements.toml（后者可强制 allow_managed_hooks_only 等，忽略用户/项目配置），见 learn.chatgpt.com/docs/config-file 系列。
- **Why**: 这句话现在是无法实现的需求：没有'managed policy 文件'与'用户配置'的分离设计，DENY 不可覆盖就没有落点；而如果公司真要靠它做合规，必须明确说清这是 policy-by-honesty（防呆不防恶意），否则是在向公司做过度承诺——与文档'approval 不是 sandbox 必须明确披露'的诚实精神自相矛盾。
- **Fix**: 定义 policy 配置模型：分 managed 层（公司下发、独立文件、Foundry 启动时加载且用户配置不可 override）与 user 层；并在文档中如实声明威胁模型——managed DENY 防意外不防本机管理员恶意绕过。
- **Ask user**: 公司固定 DENY 规则由谁编写和下发（IT 推送的 managed 文件？随 wheel 打包？）？是否接受'只防意外、不防本机管理员故意绕过'的诚实定位？

### [medium] §3.5 SessionStore - Session 的产品面只有'记录'没有'使用'：没有 resume/continue 需求（中断、cancel、崩溃后能否接着做？）、没有 session 列表/查看命令、没有 session 文件的存放位置和保留策略。versioned JSONL 暗示可回放，但全文没有任何消费者。对照：Codex 有 codex resume（按仓库列出/搜索历史会话）。
- **Why**: 一个多轮审批、可能跑几十分钟的任务，进程一死全部作废，用户体验会立刻提出 resume 需求；而 resume 是否入 V1 直接决定 SessionStore 的写法（纯 append-only 审计日志 vs 可重建 in-flight 状态的事件溯源），事后从前者改到后者是重写。
- **Fix**: 现在就裁决：V1 明确'JSONL 仅为审计与证据，不支持 resume'（写进非目标），或者把 resume 列为需求并给 SessionStore 增加状态重建要求；无论哪种，补上 session 存储位置（如 %LOCALAPPDATA%\foundry\sessions\<id>\）与 foundry sessions 列表命令。
- **Ask user**: V1 需要 session resume（中断后继续任务）吗，还是 JSONL 只做审计证据、resume 明确推到 V2？

### [medium] §2 / §3.3 / §3.5 workspace 定义 - '在指定仓库中'如何指定没有说：workspace = 当前工作目录？--workspace flag？必须是 git 仓库吗（§3.5 要求 session 开始记录 Git baseline，隐含'非 git 目录不能跑'但没明说）？monorepo 场景 workspace 是 repo 根还是子目录（影响文件工具的边界判定和 git_status 的噪音）？跨两个仓库的任务是否明确不支持？
- **Why**: workspace 边界是文件工具安全检查（traversal/junction 拒绝）的判定基准，基准没定义则 §3.3 的安全需求无法测试；Git baseline 硬依赖也应升格为显式先决条件（非 git 目录直接 refuse 并报错），否则实现者会即兴处理成各种半吊子行为。
- **Fix**: 补一句定义：workspace = 显式传入或默认 cwd 的单一目录，必须位于 git 仓库内（否则启动即拒绝，给出明确错误）；V1 单 workspace，monorepo 下 workspace 可以是子目录但 git 命令作用于所在仓库；多仓库任务列为非目标。
- **Ask user**: V1 是否强制 workspace 必须是 git 仓库（非 git 目录直接拒绝）？monorepo 里允许把子目录设为 workspace 吗？

### [medium] §2 / §3.2 / §3.3（缺失） - 文档说'Foundry 必须先定义自己的接口，再独立实现'，但最核心的内部接口——统一的 message / tool-call / tool-result 表示（provider 无关的中间格式）——从未被列为交付物。同时模型侧产品决定缺失：目标/支持哪些模型？system prompt 归谁管、放哪、怎么迭代？tool schema 用 Responses API 的 function calling 还是自定格式再转换？streaming 是硬需求还是可选？
- **Why**: '两个 backend 共用一套 loop' 成立的前提就是这个中间表示；不先冻结它，ChatGPT 路径和 Gateway 路径会各自长出私有格式，最后 loop 事实上分叉——正是文档明令禁止的'第二套 loop'的温床。system prompt 是 agent 产品的一半，不在需求里就没人给它建版本管理和回归。
- **Fix**: 在 §2 交付物中显式列出：(a) Foundry 内部 conversation/tool-call 中间表示（文档 + 类型定义），两个 ModelBackend 只做该表示与线协议的互转；(b) system prompt 作为受版本管理的资产（随 wheel 打包），修改需过验收任务集回归；(c) 声明 V1 目标模型清单与 streaming 是否必需。

### [medium] §3.3 / §3.5（缺失） - Token/成本预算 UX 缺失：§3.3 限制轮数/次数/时间/输出大小，唯独不限 token；没有需求把每次模型调用的 usage（prompt/completion tokens）记入 JSONL、在结束时汇总展示；没有 429/rate-limit 的产品行为（个人 ChatGPT 路径有套餐限额，公司 Gateway 通常有配额/计费）——重试几次？退避多久？超限时以什么状态终止、怎么告诉用户？
- **Why**: 个人路径撞套餐限额、公司路径撞配额是必然日常，行为未定义会表现为'卡死不动'或静默失败；而没有 usage 记录，公司路径的成本归因和'为什么这么慢/贵'都无法回答——这对一个以审计为卖点的 runtime 是缺口。
- **Fix**: 补硬性需求：每次模型调用记录 usage 到 JSONL；结束报告含总 token/调用次数；限定 429/5xx 的重试次数与退避策略；持续限流以 blocked（含 reason=rate_limited）终止而非无限等待；可选的每任务 token 上限配置项。

### [medium] §2 / §3.5（缺失） - 没有仓库级说明文件机制（Codex 的 AGENTS.md、Claude Code 的 CLAUDE.md 的等价物），并由此产生一个具体空洞：§3.5 要求'所有验证声明都有真实命令和 exit code'，但没有说验证命令由谁决定——用户在任务里给？仓库配置声明（'本仓库测试命令是 pytest -q'）？还是模型自己猜？模型猜错命令（跑了 pytest 但项目用 unittest）时,'有 exit code 的验证'就成了合规但无意义的证据。
- **Why**: '返回带证据的结果'的证据质量完全取决于验证命令是否正确，这是产品的核心承诺；没有说明文件机制，每个任务都要用户口述构建/测试命令，UX 极差，且 policy（哪些命令预 ALLOW）也失去了仓库级配置的挂载点。
- **Fix**: V1 增加一个最小的仓库说明文件（如 FOUNDRY.md 或 foundry.toml 的 [project] 段）：声明构建/测试/lint 命令与注意事项，启动时注入上下文；验证声明优先匹配该文件声明的命令。
- **Ask user**: V1 是否要支持仓库级说明/配置文件（声明测试命令、构建命令，类似 AGENTS.md）？验证用哪个命令，以任务指定、仓库声明、还是模型自选为准？
