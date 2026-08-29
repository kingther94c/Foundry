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
| 前缀白名单被链式命令绕过 | 命令按 `;` `\|` `&&` `\|\|` 分段；**ALLOW 规则必须覆盖每一段才成立**，DENY/ASK 命中任一段即生效；别名归一（rm/del→Remove-Item）；`&'foo'`（无空格调用操作符）判为不可解析；无法可信解析即 ASK | [policy/segmenter.py](../src/foundry/core/policy/segmenter.py)、[policy/engine.py](../src/foundry/core/policy/engine.py) `Rule.matches` |
| 熔断表被参数形态绕过 | 比较前先归一化命令头并剥掉 git 全局选项（`-C`/`-c`/`--git-dir` 等），`git.exe`/`git -C . reset --hard` 与裸形式等价；`git checkout` 整体拒绝（分支名与路径不可区分，引导用 `git switch`） | policy/engine.py `check_breaker`、segmenter.py `effective_argv` |
| 仓库 .git/config 让 git 变成程序启动器 | 显式关闭 `diff.external`、`--no-ext-diff --no-textconv`、`core.pager/editor/sshCommand`、`protocol.ext`；`safe.directory` 只限当前 workspace（不用 `*`）；git 子进程也走 `child_environment()` 过滤 | [tools/git.py](../src/foundry/core/tools/git.py) |
| 只读工具穿过 junction 读到外部文件 | `os.walk` 会跟随 junction（`islink` 对其返回 False），故 `list_files`/`search_text` 下降时逐目录检查 reparse point | [tools/files.py](../src/foundry/core/tools/files.py) `_prune` |
| 仓库自我提权 | 仓库配置只接受 deny/ask；**`[runtime]` 也只能收紧**（mode 只允许 plan/dont_ask，预算只能调低）；连接类配置（endpoint/凭证/headers/proxy）只读机器本地层；写 `<workspace>/.foundry/` 被熔断表拒绝 | [config.py](../src/foundry/core/config.py)、[policy/engine.py](../src/foundry/core/policy/engine.py) |
| 补丁经 `Move to:` 覆盖未申报的文件 | 移动目标计入 `paths`，因而对熔断表、脏文件 ASK 规则和审批展示可见；目标已存在即拒绝；目标在**规划阶段**解析，非法路径不会在源文件已改写后才失败 | [tools/patch.py](../src/foundry/core/tools/patch.py) |
| 补丁静默改错位置 | 宽容梯度的行级映射要求匹配落在行边界上，否则判失败并给提示——绝不把行内命中扩成整行替换 | tools/patch.py `_map_span` |
| 模型经包装器移动 HEAD 使 diff 看起来干净 | 收口时重新采集 git 证据，HEAD 与 baseline 不一致即降级 `partial` 并记事件；报告区分本次会话改动与既有改动 | [runtime.py](../src/foundry/core/runtime.py) `_finalize` |
| 销毁用户未提交的工作 | 熔断表拒绝 `git checkout -- / restore / reset --hard / clean / stash drop / stash clear`；baseline 记录脏文件，改脏文件强制 ASK | policy/engine.py |
| 模型伪造"已验证" | `finish` 的每条 claim 必须引用真实 command 事件且 exit code 相符，否则降级 partial；HEAD 移动也降级 | [runtime.py](../src/foundry/core/runtime.py)、[tools/finish.py](../src/foundry/core/tools/finish.py) |
| 凭证进入日志/上下文/事件流 | `SecretHandle` 不可打印；凭证只在 HTTP header 注入点解析；单一 choke point 做字节级 exact-match（UTF-8 与 UTF-16LE，先于 base64） | [auth.py](../src/foundry/core/auth.py)、[redaction.py](../src/foundry/core/redaction.py) |
| 测试代码窃取环境里的密钥 | 子进程 env 只保留最小集，剔除含 KEY/TOKEN/SECRET/PASSWORD/AWS_ 等片段的变量 | [winapi.py](../src/foundry/core/winapi.py) `child_environment` |
| 超时后孤儿进程占住文件锁 | Job Object（KILL_ON_JOB_CLOSE）托管进程树，一次杀光并释放继承的管道句柄 | winapi.py `ProcessJob` |
| 终端转义序列注入（OSC 52 剪贴板外传等） | 渲染前剥离 ANSI/OSC/控制字符，rich markup 转义 | [cli/render.py](../src/foundry/cli/render.py) |
| git 调用本身的代码执行面 | 内置 git 工具硬化：`--no-pager -c core.fsmonitor= -c core.hooksPath=`，剥离 `GIT_*`；裸 git 不入只读白名单 | [tools/git.py](../src/foundry/core/tools/git.py) |
| 崩溃的会话被误认为成功 | 无 termination 事件的 journal 一律判 `interrupted`；截尾末行可容忍 | [session.py](../src/foundry/core/session.py) |
| 模型换措辞无限重试 | 失败指纹按「归一化操作 + 错误类」计数，文本变化不重置 | runtime.py `FailureTracker` |

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

- **canary 泄漏套件**：以金丝雀凭证跑全流程，断言它不出现在 journal、artifact、audit、控制台（`test_cli_e2e.py::test_credentials_never_appear_in_the_journal`、`test_session.py`）。
- **路径逃逸表**：junction、ADS、`..`、设备名、UNC、盘符相对全部被拒（`test_workspace.py`）。
- **分段器攻击表**：链式命令、命令替换、重定向、调用操作符、别名（`test_segmenter.py`）。
- **policy 决策表**：deny-wins、熔断表不可覆盖、accept_edits 下脏文件仍 ASK、dont_ask fail-closed、hook 改写重入熔断（`test_policy.py`）。
- **进程树清理**：取消一个派生了孙进程的命令，无残留且不阻塞（`test_tools_command_git.py`）。
- **证据链**：伪造的 claim 使 completed 降级为 partial（`test_runtime.py`、`test_golden_tasks.py`）。
- **崩溃恢复**：截尾 journal 判 interrupted（`test_session.py`）。

## 6. V2 方向

restricted-token 沙箱（Codex 的 Windows sandbox 用了受限令牌 + 专用本地账户 + WFP 防火墙 + 提权辅助服务，是数个季度的工程量）、shadow-git checkpoint/undo、managed policy 的实际分发机制。
在那之前，诚实披露 + 强 ASK + 完整审计是 V1 的立场，不是疏漏。
