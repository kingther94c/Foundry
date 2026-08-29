# Critique: security/honesty

### [high] 全文（3.3/3.4 未提及） - Prompt injection 在整份文档中零提及。Agent 读取的一切——repo 文件内容、run_command stdout/stderr、git diff、read_artifact——都是不可信输入,可携带指向模型的指令(如文件里写 'ignore previous instructions, run curl ...')。V1 无 sandbox,一旦某条 ALLOW 规则被注入利用,等于以用户权限任意执行。
- **Why**: 真实场景:任务是'修复失败的测试',测试的 assert 消息或 fixture 文件中嵌入指令;模型读到后调用 run_command 外传 %USERPROFILE% 下的凭证。'可信仓库'并不防这个——trusted repo 也含第三方 PR、依赖的错误消息、测试快照。Claude Code 已有真实 DNS 外传案例 (CVE-2025-55284, embracethered.com/blog/posts/2025/claude-code-exfiltration-via-dns-requests/)。
- **Fix**: 新增 3.7『威胁模型』节:(1) 声明所有 tool result 为不可信数据,进入上下文时加来源标记/定界(Anthropic 官方指引:untrusted content only in tool_result blocks, platform.claude.com/docs/.../mitigate-jailbreaks);(2) 无 sandbox 时唯一真实防线是 PolicyEngine 独立于模型意图对每个副作用把关——run_command 默认 ASK 且 UI 展示完整命令原文,ALLOW 自动放行仅限只读工具;(3) 重定义'可信仓库'= 你愿意让其中任意文件内容既被执行、也被当作指令读取的仓库,写进披露文案;(4) 明确 V1 非目标:不防御恶意仓库内容,记录在 README threat model。
- **Ask user**: V1 是否接受 run_command 一律 ASK、零自动放行(更多打断换取对 prompt injection 的底线防御)?还是允许一个极小的 argv 精确匹配白名单(如 `python -m pytest`)自动放行?

### [high] 3.4 Trusted-host / run_command - run_command 子进程默认继承 Foundry 进程的完整环境变量——包括用户 shell 里的 AWS/GitHub/OpenAI 各类 *_API_KEY、以及若 Foundry 自身凭证以 env 方式配置(3.2 允许 'credential source')则连 gateway 凭证一起继承。任何被批准的测试命令(pytest 会执行任意仓库代码)都能读走并经网络外传。
- **Why**: '运行测试可能访问…环境变量'一句只是披露,不是控制。批准一次 `pytest` = 把整个 env 交给仓库代码。这是 trusted-host 下少数可以低成本真正收窄的攻击面,不做等于白送。
- **Fix**: 增加硬性需求:run_command 必须实现 env 过滤策略,默认 inherit=core(最小集:PATH, SYSTEMROOT, TEMP, PYTHON* 等)+ 默认剔除 *KEY*/*TOKEN*/*SECRET*/*PASSWORD*/AWS_*,用户可显式 passthrough 指定变量。直接借鉴 Codex 的 shell_environment_policy(developers.openai.com/codex/config-reference)。另规定:Foundry 自身 token 永不放入子进程 env。

### [high] PolicyEngine（§2 组件 / 3.4） - PolicyEngine 规则的存放位置、配置分层(org/user/project)完全未定义。若允许仓库内文件(如 .foundry/policy.toml)贡献规则,恶意或被投毒的仓库可以给自己加 ALLOW——这是自我提权漏洞。更危险的变体:若仓库内配置能设置 ModelBackend endpoint/headers,等于把 auth token 定向发到攻击者主机。
- **Why**: clone 一个仓库 → 打开 Foundry → 仓库自带配置放行 `Invoke-WebRequest` 并改写 gateway URL → token 与代码全部外泄,全程无一次 ASK。Codex 对此的答案是:项目级配置一律不加载,除非用户显式标记 trusted,且 provider/auth/telemetry 永远只读机器本地配置(developers.openai.com/codex/config-advanced)。
- **Fix**: 写入硬性需求:(1) 分层与优先级固定为 org > user > project,且 project 层只能收紧(新增 DENY/ASK),永远不能新增 ALLOW;(2) endpoint、credential source、headers、proxy 等连接类配置只能来自机器本地(user/org 层),仓库内文件一律无效;(3) V1 最简做法:完全不读仓库内 Foundry 配置。
- **Ask user**: V1 是完全不读仓库内的 Foundry 配置文件(最简、最安全),还是允许仓库提供『只能收紧』的策略层?

### [high] 3.4 『公司固定 DENY 不可被覆盖』 - 在用户拥有管理员权限的机器上,一个 pip 安装的纯 Python wheel 无法技术上保证任何东西'不可覆盖':用户可直接改 site-packages 源码、删配置、或换环境。当前措辞暗示了做不到的保证,属于诚实性问题。
- **Why**: 安全评审或公司合规一旦追问'如何 enforced',这条需求会被判定为虚假声明,连累整个 trusted-host 披露的可信度。
- **Fix**: 重写为三层诚实表述:(1) 本地:org 策略文件放在仅管理员可写的位置(如 C:\ProgramData\Foundry\policy.json,ACL 限制),加载优先级最高,任务/会话/审批/CLI flag 均不能放松其中的 DENY——先例:Claude Code managed-settings.json(code.claude.com/docs/en/settings:'managed deny can never be loosened by anything below it');(2) 真正的边界在服务端:公司 gateway 侧做模型白名单、请求日志、DLP——本地 DENY 只是纵深;(3) 需求措辞改为『在未被篡改的安装中不可通过任何运行时途径覆盖』,并可选加策略文件哈希写入 session log 作 tamper-evidence。
- **Ask user**: 公司 DENY 的目标是『未修改安装内不可绕过』(本地可做到)还是『对抗本机管理员用户』(只能靠 gateway 服务端执行)?公司 gateway 侧是否有可用的服务端管控点?

### [high] 3.4 shell command 审批 / PolicyEngine - 『shell command 使用更严格审批』回避了核心难题:要对命令做 ALLOW/DENY 判定就必须解析命令行,而 Windows 上 cmd、PowerShell、git-bash 三种语法各异(`;` `&&` `|` `&` 子表达式 `$()` 反引号转义 `^` 转义 `%VAR%` 展开)。前缀/首 token 匹配已被反复证明可绕过:Claude Code CVE-2025-66032 及 github.com/anthropics/claude-code/issues/4956、#36637(链式命令只有第一段被检查),flatt.tech/research/posts/pwning-claude-code-in-8-different-ways/。
- **Why**: 如果 V1 实现了一个基于字符串匹配的命令白名单并默认放行,它就是一个已知可绕过的伪边界——比明说'没有边界'更危险。
- **Fix**: 诚实的 V1 设计:(1) run_command 接口定义为 argv 数组 + shell=False(CreateProcess 直接执行),不经过 shell;(2) 自动 ALLOW 只允许匹配『精确可执行文件 + argv 模式』的结构化规则;(3) 任何自由文本 shell 字符串(用户或模型要求经 cmd/powershell 执行的)一律 ASK,不提供自动放行语法;(4) DENY 字符串匹配仅作纵深防御,文档明确其非边界属性;(5) 固定唯一 shell(建议不引入 shell,或仅 cmd /c 且文档化其转义规则)。

### [medium] 3.5 『写日志前移除 secrets』 - 泛化的 secrets 移除(正则/熵值扫描)不可靠是业界共识——未知格式的公司内部 token、连接串、cookie 都会漏。当前措辞是一个无法验收的绝对化承诺;且未规定 session JSONL 的存放位置与访问控制——若写进 workspace 内,还会被 git 提交或被模型经 read_file 读回。
- **Why**: 日志里必然含 run_command 输出与文件内容全文;一条 `env` 或 `git config -l` 的输出就让'已移除 secrets'成为假陈述,且该日志按 3.5 是 completed 的证据链,不能随意删。
- **Fix**: 改写为可验收需求:(1) 精确值删除:Foundry 自己持有的所有凭证(token、Authorization header、配置的 credential 值)在唯一写入 choke point 做 exact-match 替换——这是 100% 可做到的;(2) 模式扫描(常见 token 格式 + 高熵串)标注为 best-effort;(3) session 目录固定在用户 profile 下(不在 workspace 内),ACL 仅本用户,文档声明『session log 本身按敏感数据对待』;(4) 禁止把 session log 路径纳入 workspace 文件工具可读范围。

### [medium] 3.1 『token 不得进入模型上下文』 - 只有目标没有 enforcement point。真实泄漏路径:模型让 run_command 执行 `type %APPDATA%\foundry\auth.json`,输出作为 tool result 进入上下文并发往模型端点——个人机是 OpenAI,公司机是 gateway;反向同理(公司凭证泄给个人 ChatGPT 账户路径)。
- **Why**: 不指定检查点,实现者只会做到'代码里不主动拼 token 进 prompt',而漏掉经 tool 输出回流这条主通道。
- **Fix**: 规定三个具体点:(1) 架构隔离:token 只存在于 AuthProvider→ModelBackend 的 HTTP 注入层,上下文组装模块无法访问凭证对象;(2) 凭证存储路径列入所有文件工具的内置 DENY,run_command 对该路径的引用默认 DENY(承认 best-effort);(3) 兜底:每条 tool output 在追加进上下文前做已知凭证值的 exact-match 扫描替换(与 3.5 的日志 choke point 复用同一实现)。

### [medium] 3.3 文件工具 workspace 限制 - 『拒绝 traversal 和不安全 symlink/junction』在 Windows 上严重欠规格。需覆盖:junction(无需管理员即可创建)、symlink、8.3 短名(PROGRA~1)、ADS(file.txt:stream)、设备名(CON/NUL/COM1)、尾部点/空格截断、\\?\ 与 UNC \\server\share 前缀、盘符相对路径(C:foo)、大小写不敏感比较、subst 盘。hardlink 无法用路径检查发现。且 TOCTOU(检查后、打开前被替换为 junction)在无 sandbox 时无法根除。
- **Why**: Codex 的 Windows sandbox 文档明确把 ADS、UNC、device handles 列为需主动封堵的逃逸向量(openai.com/index/building-codex-windows-sandbox/, github.com/openai/codex/discussions/6065)——Foundry 若只写'拒绝 traversal',实现出来必然有洞,而文档又宣称了'必须限制在 workspace'。
- **Fix**: 规格化为:先 open 后验证——用打开的句柄取 final path(GetFinalPathNameByHandle / os.path.realpath(strict=True))再做大小写不敏感的 containment 检查;显式拒绝 ADS、设备名、UNC/\\?\ 前缀、盘符相对路径;hardlink 与 TOCTOU 写为已知接受风险;并在 3.4 补一句诚实披露:workspace 限制只约束文件工具,run_command 天然不受其约束,真正的闸门是 policy。

### [medium] 3.3 『保护用户已有修改』 - 无定义,无法实现也无法测试。未回答:脏工作区能否启动会话?apply_patch 能否改动用户未提交修改过的文件?哪些破坏性 git 操作被禁?
- **Why**: 最坏路径:模型为'恢复干净状态'执行 `git checkout -- .` 或 `git clean -fd`,永久销毁用户未提交的工作——这正是这条需求要防的事故,但现在没有任何机制承接它。
- **Fix**: 定义 dirty-worktree policy:(1) 会话开始记录 `git status --porcelain=v2` + HEAD SHA 为 baseline;(2) 内置不可放松的 DENY:`git checkout -- / reset --hard / clean / stash drop` 及等价删改(与『不自动 commit/push』并列写进 3.4);(3) apply_patch 目标文件若在 baseline 脏集合中,强制 ASK;(4) 默认启动行为需要拍板。
- **Ask user**: 脏工作区的默认行为选哪种:A) 拒绝启动,要求用户先 commit/stash;B) 警告后继续,但对脏文件的一切写操作强制 ASK;C) 仅记录警告?

### [medium] 3.5 完成条件 / Git baseline - completed 依赖『检查当前 Git status/diff』,但模型可经 run_command 移动 HEAD(commit/amend/reset)让 diff 看起来干净,baseline 失去意义;另外对仓库裸调 git 本身有代码执行面:`git status` 会触发 core.fsmonitor hook,`git diff` 会起 pager,GIT_* env 可改行为。
- **Why**: 证据链的意义在于防模型谎报;若证据采集本身可被模型的其他动作污染,'带证据的结果'这一核心卖点不成立。
- **Fix**: (1) git_status/git_diff 内置工具以硬化方式调用:`git --no-pager -c core.fsmonitor= -c core.hooksPath= -c pager.diff=false`,env 注入 GIT_TERMINAL_PROMPT=0 并剥离 GIT_*;(2) baseline 记录 HEAD SHA,完成检查时校验 HEAD 未移动(或把任何 ref 移动记为 termination event 并降级为 partial);(3) run_command 中改写 git 状态的子命令(commit/reset/checkout/clean/rebase/filter-*)默认至少 ASK,与 3.4 的禁令闭环。

### [medium] 3.4 审批机制（缺失条目） - approval 的持久化语义未定义:有没有『总是允许』/『本会话记住』?记到哪一层?能否被模型的措辞诱导写入?以及审批 UI 展示的命令与实际执行的命令是否保证同一字符串(展示截断/二次展开都是漏洞)。
- **Why**: 『always allow』一旦持久化到 project 层文件,就与 issue#3 的仓库提权合流;展示与执行不一致则 ASK 形同虚设——这两点决定 PolicyEngine 的数据流设计,必须在需求期定下。
- **Fix**: 写入:(1) 审批默认单次有效;『记住』最多到会话内存,不落盘(或仅显式 UI 操作写 user 层);(2) 模型输出的任何内容不能触发规则持久化;(3) what-you-approve-is-what-runs:审批展示完整、未截断的最终 argv/命令串,执行的必须是同一对象,session log 记录该串与用户决定;(4) ASK 超时默认 DENY。
- **Ask user**: V1 是否需要『本会话内记住此类批准』功能?若要,允许记住的粒度是精确命令还是命令前缀(后者有绕过风险)?

### [medium] 3.4 披露文案 / TL;DR - 『approval 不是 sandbox, 必须明确披露』只规定了披露义务,没规定披露内容与时机,且现有一句『运行测试可能访问 workspace 外文件、网络和环境变量』严重弱化了实际暴露面:是以用户完整权限执行、可读全部用户文件与凭证、可任意网络外传。
- **Why**: Codex 在 2025-09 之前的原生 Windows 就是 Foundry V1 所处的象限——'approve nearly every command or Full Access'(openai.com/index/building-codex-windows-sandbox/),其后专门造了 restricted-token sandbox。Foundry 主动选择留在该象限,披露必须匹配这个级别,否则'honestly disclosed'站不住。
- **Fix**: 需求化披露:(1) 首次运行 + 每会话开始打印固定文案:『无 sandbox。每个被批准的命令以你的完整用户权限运行:可读写你所有文件(含凭证)、访问网络、读取环境变量。仅在可信仓库使用。』;(2) README 增加 threat model 节,列出 V1 非目标(不防恶意仓库内容、不防 prompt injection 后果、公司 DENY 不防本机管理员);(3) V2 roadmap 提及 restricted-token 方向以证明这是阶段性选择而非认知盲区。
