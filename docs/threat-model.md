# Foundry 威胁模型（V1）

> 这份文档说明 Foundry **保护什么**、**不保护什么**，以及每条保证的执行点在哪里。
> 原则：宁可写清楚做不到，也不做无法验收的承诺。

## 1. 一句话定位

**V1 是 trusted-host，没有沙箱。** 审批（policy）减少的是*失误*，不是*恶意*。
每个被批准的命令以你的完整用户权限运行：可读写你的所有文件（包括凭证）、访问网络、读取环境变量。

首次运行与每次会话开始都会打印这段披露（[cli/render.py](../src/foundry/cli/render.py) 的 `DISCLOSURE`）。

## 2. 信任边界

| 来源 | 信任级别 | 说明 |
|---|---|---|
| 用户在终端输入的任务 | 可信 | 唯一的指令来源 |
| `~/.foundry/` 下的用户配置 | 可信 | 机器本地，用户自己写 |
| managed policy（ProgramData，ACL 保护） | 可信且优先 | 只能收紧 |
| **仓库文件内容** | **不可信** | 可能含指向模型的注入指令 |
| **工具输出**（命令 stdout、git diff、read_file 结果） | **不可信** | 同上 |
| **仓库内 `.foundry/config.toml`、`FOUNDRY.md`** | **不可信** | 需一次性信任确认；只能收紧 policy |
| **模型输出** | **不可信** | 是待审的请求，不是授权 |

"可信仓库"的写实定义：**你愿意让其中任意文件内容既被执行、也被当作指令读取的仓库。**

## 3. 防护的威胁与执行点

| 威胁 | 防护 | 执行点 |
|---|---|---|
| 模型误改 workspace 外文件 | 路径必须是 workspace 相对；realpath+normcase+commonpath 双侧比对；逐组件拒绝 reparse point；拒绝 ADS/设备名/UNC/盘符相对 | [workspace.py](../src/foundry/core/workspace.py) |
| 前缀白名单被链式命令绕过 | 命令按 `;` `\|` `&&` `\|\|` CR/LF 分段；**ALLOW 必须覆盖每一段才成立**，DENY/ASK 命中任一段或整条即生效；别名归一（rm/del→Remove-Item）；含结构性字符（`#` `<` `>` `$` 反引号 `^` 或孤立 `&`）或命令头不像普通可执行名者，一律不可自动放行 | [policy/segmenter.py](../src/foundry/core/policy/segmenter.py)、[policy/engine.py](../src/foundry/core/policy/engine.py) `Rule.matches` |
| **词法陷阱使危险命令对熔断表隐形** | 熔断表额外扫描一个**故意错误的读法**（`paranoid_segments`）：无视引号/注释/分组按分隔符切开，剥掉边缘的引号、括号、`&`、`.`。只增拒绝不增放行，因而不会被任何未预料的写法骗过（注释吞换行、`<#` 中缀、分组、script block 皆已覆盖） | segmenter.py `paranoid_segments` |
| 熔断表被参数形态绕过 | 命令头归一化（`git.exe`→`git`、别名展开）；`effective_argv` **扫描到第一个真正的 git 子命令**而非跳过枚举出的全局选项（`--attr-source HEAD` 曾把子命令挤出检查位）；`git checkout` 整体拒绝并引导 `git switch`，`git switch --discard-changes` 同样拒绝 | policy/engine.py `check_breaker`、segmenter.py `effective_argv` |
| 仓库 .git/config 让 git 变成程序启动器 | 显式关闭 `diff.external`、`--no-ext-diff --no-textconv`、`core.pager/editor/sshCommand`、`protocol.ext`；`safe.directory` 只限当前 workspace（不用 `*`）；git 子进程也走 `child_environment()` 过滤 | [tools/git.py](../src/foundry/core/tools/git.py) |
| 只读工具穿过 junction 读到外部文件 | `os.walk` 会跟随 junction（`islink` 对其返回 False），故 `list_files`/`search_text` 下降时逐目录检查 reparse point | [tools/files.py](../src/foundry/core/tools/files.py) `_prune` |
| 仓库自我提权 | 仓库配置只接受 deny/ask；**`[runtime]` 也只能收紧**（mode 只允许 plan/dont_ask，预算只能调低）；连接类配置（endpoint/凭证/headers/proxy）只读机器本地层；写 `<workspace>/.foundry/` 被熔断表拒绝 | [config.py](../src/foundry/core/config.py)、[policy/engine.py](../src/foundry/core/policy/engine.py) |
| 补丁经 `Move to:` 覆盖未申报的文件 | 移动目标计入 `paths`，因而对熔断表、脏文件 ASK 规则和审批展示可见；目标已存在即拒绝；目标在**规划阶段**解析，非法路径不会在源文件已改写后才失败 | [tools/patch.py](../src/foundry/core/tools/patch.py) |
| 补丁静默改错位置 | 宽容梯度的行级映射要求匹配落在行边界上，否则判失败并给提示——绝不把行内命中扩成整行替换 | tools/patch.py `_map_span` |
| 补丁解析器把 SEARCH 内容当成分隔符 | 一个 hunk 内出现多个 `=======` 时判**歧义并拒绝**（并提示改用整文件重写）——修改含冲突标记的文件是最可能触发它的任务，而按第一个分隔符切分会静默写出错误内容并报告成功 | tools/patch.py `parse_patch` |
| 同一文件在一个补丁里被写两次 | 文本归一化（大小写/`./`/`..`/分隔符）在 validate 阶段拦一道，执行阶段再按 `realpath` 归一化拦第二道（8.3 短名只有后者能识别） | tools/patch.py |
| 仓库规则文本注入 system prompt | 规则的 tool/pattern/reason 渲染前压成单行并截断——否则 deny 规则的 reason 可以往权限段里写出一段以假乱真的"以下命令无需批准" | [prompts.py](../src/foundry/core/prompts.py) |
| 单轮返回海量 tool call 耗尽预算 | 预算在**每次调用前**检查，而非每轮开头一次（后者曾让一轮执行 5000 次调用） | runtime.py |
| 模型经包装器移动 HEAD 使 diff 看起来干净 | 收口时重新采集 git 证据，HEAD 与 baseline 不一致即降级 `partial` 并记事件；报告区分本次会话改动与既有改动 | [runtime.py](../src/foundry/core/runtime.py) `_finalize` |
| 销毁用户未提交的工作 | 熔断表拒绝 `git checkout -- / restore / reset --hard / clean / stash drop / stash clear`；baseline 记录脏文件，改脏文件强制 ASK | policy/engine.py |
| 模型伪造"已验证" | `finish` 的每条 claim 必须引用真实 command 事件且 exit code 相符，否则降级 partial；HEAD 移动也降级 | [runtime.py](../src/foundry/core/runtime.py)、[tools/finish.py](../src/foundry/core/tools/finish.py) |
| 凭证进入日志/上下文/事件流 | `SecretHandle` 不可打印；凭证只在 HTTP header 注入点解析；单一 choke point 做字节级 exact-match（UTF-8 与 UTF-16LE，先于 base64） | [auth.py](../src/foundry/core/auth.py)、[redaction.py](../src/foundry/core/redaction.py) |
| 测试代码窃取环境里的密钥 | 子进程 env 只保留最小集，剔除含 KEY/TOKEN/SECRET/PASSWORD/AWS_ 等片段的变量 | [winapi.py](../src/foundry/core/winapi.py) `child_environment` |
| 超时后孤儿进程占住文件锁 | Job Object（KILL_ON_JOB_CLOSE）托管进程树，一次杀光并释放继承的管道句柄 | winapi.py `ProcessJob` |
| 终端转义序列注入（OSC 52 剪贴板外传等） | 渲染前剥离 ANSI/OSC/控制字符，rich markup 转义 | [cli/render.py](../src/foundry/cli/render.py) |
| 非 ASCII 文件名绕过脏文件保护 | git 默认把非 ASCII 路径转义为 `\303\251…`，于是带重音或中文名的文件永远匹配不上脏集合；硬化参数加 `core.quotepath=false` | tools/git.py `_HARDENING` |
| 崩溃的会话被误认为成功 | 无 termination 事件的 journal 一律判 `interrupted`；截尾末行可容忍 | [session.py](../src/foundry/core/session.py) |
| 模型换措辞无限重试 | 失败指纹按「归一化操作 + 错误类」计数，文本变化不重置 | runtime.py `FailureTracker` |

## 3.5 三条从实战中学到的原则


**四轮对抗评审，每一轮都攻破了命令分段器**——这个事实本身比任何单个漏洞都重要。

### (a) 拒绝时不要相信自己的词法分析

第一轮补了 `&'foo'`，第二轮来了 `(git reset --hard)`、`&{git ...}`、`cmd /c git ...`；第三轮写了注释剥离，第四轮就发现它**两头都错**——`'x'#` 后面的注释没识别（漏剥），`a<# ; git reset --hard #>` 中间的 `<#` 又被当成块注释（多剥，把 PowerShell 真会执行的语句直接抹掉）。四轮四破，每次都是上一轮没想到的写法。继续打补丁只会买到第五次。

**做法：熔断表同时扫描两个独立读法，任一命中即拒绝。**

`paranoid_segments()` 用一个**故意粗糙**的读法重扫命令——无视引号、注释、分组，只按分隔符与括号切开，并把引号/括号/`&`/`.`/逗号从 token 边缘剥掉。它只被熔断表使用：**只能增加拒绝，永远不能放行**。每条熔断规则都锚定 `argv[0]`，所以 `echo "git reset --hard"` 这种字符串提及不会误判。

**⚠️ 曾经的过度声称，以及第五轮的纠正**：这里一度写着"拒绝不再依赖正确解析 PowerShell"。**那是错的**——第五轮直接证伪：`paranoid_segments` 不是"没有词法分析"，它是**第二个词法分析器**，有自己的盲区。`git reset ,--hard` 就同时骗过了两者（PowerShell 的逗号数组操作符把 `--hard` 交给 git，而两个读法都只看到字面 token `,--hard`），实测放行并销毁未提交的工作。

诚实的表述是：**纵深防御，不是保证**。两个独立读法意味着攻击必须同时骗过两者，这比一个解析器强，但比"保证"弱。逗号已归一化，中缀括号已切分，但下一个 PowerShell 语法特性可能还会同时骗过它们。真正的边界仍然是 §1 那句话：没有沙箱。

自动放行这一侧则收紧到可验证的小语法：**含有能移动语句边界或隐藏文本的字符（`# < > $ ` ^` 或孤立 `&`）的命令，一律不可自动放行**。括号花括号故意不在其中——它们能分组但不能隐藏边界，而 `python -c "print(1)"` 太常见了；分组形式由命令头检查和 paranoid 读法负责。

`effective_argv` 同理：不再数"跳过几个全局选项"，而是**扫描到第一个真正的 git 子命令**——`--attr-source HEAD` 曾把子命令挤出所有熔断规则检查的位置（实测可破坏数据）。

**代价**：含 `#` 的命令（哪怕在引号里）现在需要一次批准。两轮都在注释语法上出事，这个代价我们认。命令照样能跑，只是要点个 yes。

### (b) 修复本身就是新漏洞的来源，且倾向于把硬保证换成软保证

第二轮的 5 个新缺陷全部由第一轮修复造成；第三轮抓到 2 个由第二轮造成；第四轮的**两个 critical 都在第三轮写的注释剥离器里**；第五轮证伪了第四轮的收敛声称。**同一类错误犯了两次**：为了标记"不可解析"而提前返回，丢掉了已解析的分段，于是 `git reset --hard; (foo)` 从熔断表的**不可批准 DENY** 掉成了**可批准 ASK**——而 system prompt 还在告诉模型这类操作"不可能被批准"。

所以现在有一条结构性不变量测试（[tests/test_breaker_invariant.py](../tests/test_breaker_invariant.py)）：**任何装饰、模式、会话授权或 hook 改写，都不能让一条本身被熔断表禁止的命令变得可批准**——28 条被禁命令 × 17 种装饰 = 476 个组合，连同各命令单独一次、各模式、会话授权与 hook 改写，共 **540 个自动生成用例**。它防的是整类错误，不是想到的那几个写法；第四轮那两个 critical 事后加进装饰表，一秒就能复现。

同理，`GIT_CONFIG_NOSYSTEM` 那个 critical 教训：**加固要问"它顺带关掉了什么合法行为"**，且验证必须跑在用户实际拥有的环境上（fixture 用普通 git 建仓，不用 Foundry 自己的硬化路径）。

### (b2) 第六轮：漏洞不在某一层里，而在两层的接缝上

前五轮都在打分段器。第六轮找到的东西形状完全不同：**每一层单看都对，合起来不对**。

- 补丁工具知道 `AVERYL~1.PY` 和长名是同一个文件；policy 层做的是字符串比较，于是 8.3 别名绕过了脏文件守卫——那条规则存在的唯一理由就是压过 `accept_edits`。
- `decode_output` 用"谁产生的替换字符少"来选编码，可回退编码是把 256 个字节全映射的单字节码页，**永远产生不了替换字符**。于是一个坏字节就把整段输出翻成乱码；而 `_drain` 在容量上限处按字节切，天然会切断多字节字符。
- 系统提示词告诉模型"merge 永远被拒"，`git pull` 干的就是那个 merge，却直接走过熔断表。
- `command_timeout_s` 有类型检查、有 provenance、有"仓库只能收紧"保护——**没有任何代码读它**。

最尖锐的一个是净室验证抓到的，而不是测试套件：事件按字段脱敏是对的，但模型的文本是**分块流式**到达的，凭证跨两个 delta 落下，两个片段都不匹配任何东西，渲染器再把它拼回屏幕上。事件流干净、终端泄漏。

教训写下来：**"每一处都正确"不蕴含"合起来正确"**。所以现在有 [tests/test_prompt_matches_breaker.py](../tests/test_prompt_matches_breaker.py)（承诺与表双向对齐）、`categorical_denials()`（提示词由熔断表的常量生成而非手写）、以及一次真实的净室端到端（重建 wheelhouse → `--no-index` 装进全新 venv → 跑真实任务 → 检查 canary 不在事件、渲染、stdout 与日志里）。**跨层的性质要跨层地验。**

### (c) 明说解决不了什么

`python -c "subprocess.run(['git','reset','--hard'])"` 一样有破坏性，任何解析都抓不到。所以解释器（python/node/…）**故意保持可放行**——拒绝 `python -m pytest` 代价极大而收益为零。"被批准的命令能做该程序能做的任何事"是 trusted-host 模型的属性（见 §4），分段器不假装解决它。

分段器只保证一件事：**文本里直接写出来的危险命令，不会因为换个写法就绕过审批。**

## 4. 明确不防护的（V1 非目标）

1. **恶意仓库内容 / prompt injection 的后果**。文件和工具输出可以试图指挥模型。唯一真实防线是 PolicyEngine 独立于模型意图对每个副作用把关——但一旦你批准了某条命令，注入就已经赢了那一步。
2. **本机管理员绕过 managed policy**。用户能改 site-packages、删配置、换环境。managed DENY 的诚实定位是：*在未被篡改的安装内*，运行时任何途径都不能放松它。真正的边界在 Gateway 服务端（模型白名单、请求日志、DLP）。
3. **`run_command` 的 workspace 约束**。workspace 边界只约束文件工具。子进程天然不受约束——闸门是 policy，不是路径检查。
4. **TOCTOU 与 hardlink**。检查后、打开前被替换为 junction 的竞态无法在无沙箱下根除；hardlink 无法用路径检查发现。
5. **通用 secret 检测**。只保证删除 Foundry 自己持有的凭证的字面字节序列。未知格式的公司 token、连接串、cookie 会漏——模式扫描标注为 best-effort。
6. **NTLM/Kerberos 代理**。stdlib 不支持；检测到 407 Negotiate 时明确报错而非静默失败。
7. **Job assign 的毫秒窗口**。`Popen` 返回后才能 assign，理论上存在极短窗口让子进程先派生孙进程逃出 job。stdlib 无法关闭该窗口。

## 5. 验收方式

这些不是声明，是测试：

- **熔断表不变量**：28 条被禁命令 × 17 种装饰（链接、注释、CR 分隔、不可解析邻段、shell 包装、分组、script block、dot-source）= 476 个组合；加上单命令基线 28、各模式 24、会话授权 6、hook 改写 6，共 540 个自动生成用例，全部必须 DENY 在第 0 步（`test_breaker_invariant.py`）。
- **canary 泄漏套件**：以金丝雀凭证跑全流程，断言它不出现在 journal、artifact、audit、事件流与控制台；**并按 1/2/3/5/13/64 字节分块**发送，验证跨 delta 的凭证同样被删（`test_cli_e2e.py`、`test_session.py`、`test_round6_fixes.py`）。
- **提示词与熔断表双向对齐**：表拒绝的每一族都必须在提示词里被点名，提示词声称"永远拒绝"的每个 git 子命令都必须真的被拒（`test_prompt_matches_breaker.py`）。手写那段话时它承诺 merge 被拒，而 `git pull` 走了过去。
- **跨层拼写一致**：8.3 短名、CRLF、大小写等在工具层解析过的路径，policy 必须看到同一个拼写（`test_drift_fixes.py`）。
- **路径逃逸表**：junction、ADS、`..`、设备名、UNC、盘符相对全部被拒（`test_workspace.py`）。
- **分段器攻击表**：链式命令、命令替换、重定向、调用操作符、别名、CR 分隔、PowerShell 注释、包装形式（`test_segmenter.py`、`test_security_regressions.py`、`test_security_round3.py`）。
- **policy 决策表**：deny-wins、熔断表不可覆盖、accept_edits 下脏文件仍 ASK（含各种路径拼法）、dont_ask fail-closed、hook 改写重入熔断（`test_policy.py`）。
- **真实 git 环境**：fixture 用**普通 git** 建仓（非 Foundry 硬化路径），覆盖 CRLF、含空格路径、重命名（`test_crlf_repo.py`、`test_security_round3.py`）。
- **进程树清理**：取消一个派生了孙进程的命令，无残留且不阻塞；孙进程占管道时报告 incomplete 而非 exit 0（`test_tools_command_git.py`、`test_resource_bounds.py`）。
- **资源上限**：命令输出 400MB 实测峰值 27MB；超限文件拒读并给出可用替代（`test_resource_bounds.py`）。
- **证据链**：伪造的 claim 使 completed 降级为 partial；HEAD 移动同样降级（`test_runtime.py`、`test_golden_tasks.py`）。
- **崩溃恢复**：截尾 journal 判 interrupted（`test_session.py`）。

## 6. V2 方向

restricted-token 沙箱（Codex 的 Windows sandbox 用了受限令牌 + 专用本地账户 + WFP 防火墙 + 提权辅助服务，是数个季度的工程量）、shadow-git checkpoint/undo、managed policy 的实际分发机制。
在那之前，诚实披露 + 强 ASK + 完整审计是 V1 的立场，不是疏漏。
