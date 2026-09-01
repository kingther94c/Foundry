# demo —— 用一个文件看懂 Foundry

`mini_foundry.py` 是 Foundry 的**骨架**：结构和真的完全一样，只是每一层都砍到最薄。
不需要网络、不需要 API key，直接跑。

```bash
python demo/mini_foundry.py --yes
```

两个入口，看你想怎么读：

| | 适合 |
|---|---|
| **`mini_foundry.py`** | 一口气跑完看全貌；从上往下读代码 |
| **`mini_foundry.ipynb`** | 逐格跑、看中间状态、改了再跑。notebook 从 `.py` **import**，不复制代码，所以两边永远一致 |

```bash
jupyter lab demo/mini_foundry.ipynb      # 或 VS Code 直接打开
```

## 用真模型（可选）

两个入口都支持接一个 **OpenAI 兼容的 `/v1/chat/completions`**：

```bash
python demo/mini_foundry.py --endpoint http://127.0.0.1:1234/v1 --model your-model --yes
```

notebook 里把第 5 节的 `USE_API` 改成 `True` 即可。

| 来源 | ENDPOINT | key |
|---|---|---|
| LM Studio | `http://127.0.0.1:1234/v1` | 随便填 |
| Ollama | `http://127.0.0.1:11434/v1` | 随便填 |
| 本机 OpenClaw 网关 | `http://127.0.0.1:18789/v1` | `~/.openclaw/openclaw.json` 的 `gateway.auth.token` |
| OpenAI | `https://api.openai.com/v1` | 你的 key |

**先知道两件事**：真 agent 一轮可能几十秒到几分钟；而且如果那个模型不产出
`tool_calls`（本机 OpenClaw 网关背后的 trade-advisor 系就不产出），loop 会在第一轮
就结束——你看得到真实的 HTTP 往返，看不到多轮工具调用。想看完整 loop，用剧本模式，
或者换一个支持 function calling 的模型。

## 先记住这一句

整个系统只有一句话：

```
while 模型还在要求调用工具:
    policy 判决 -> 执行 -> 把结果塞回对话 -> 再问一次模型
```

**其余全部代码，都是在给这句话里的某个词加保护。** 读 `src/foundry/` 时如果迷路了，
回到这句话，问「我现在读的这段，是在保护哪个词」。

## 四个剧本，四件事

模型被写死成剧本（真 backend 在那个位置发 HTTP 请求）。这样结果确定、不花钱，
而且**这正是 Foundry 那 1147 个测试能在没网没凭证的机器上跑的原因**。

| 命令 | 看什么 |
|---|---|
| `--yes` | 完整一轮：跑测试 → 读代码 → 改 → 重跑 → 带证据收工。退出码 0 |
| `--script destructive --yes` | 熔断表在第 0 步拒绝，`--yes` 也批不动 |
| `--script liar --yes` | 模型声称"全部测试通过"，闸门查出它引用的命令 exit code 是 1，降级 partial |
| `--mode plan --no` | 模式基线：plan 模式下所有改动被拒 |

`liar` 那个值得多看一眼：模型没有伪造引用，它引用的命令**真的跑过**——只是失败了。
拦住它的不是「检测撒谎」，是**要求它指出证据，然后我们自己去查那条证据**。

## 六个部分对应到哪

| demo 里的段落 | 真 Foundry | 真的那个多做了什么 |
|---|---|---|
| 1. IR | `core/conversation.py` | Usage 记账、能力协商。`arguments` 同样**故意不提前解析**——模型会吐坏 JSON，提前解析会让"报告这个调用坏了"变得不可能 |
| 2. 工具 | `core/tools/` | 9 个工具。`read_file` 记内容摘要以强制"改前先读"；`apply_patch` 锚定 search/replace 且**逐文件原子**；`run_command` 用 Job Object 保证子进程树整棵可杀、环境变量白名单过滤 |
| 3. Policy | `core/policy/` | 六步流水线 + 命令分段器。熔断表要对付同一条命令的各种写法 |
| 4. Session | `core/session.py` | 内容寻址 artifact（大输出不塞进对话）、凭证脱敏、写不进去也不能让整轮崩掉 |
| 5. Backend | `core/backends/` | Chat Completions 与 Responses 两个 adapter + stdlib 写的 HTTP/SSE |
| 6. Loop | `core/runtime.py` | 预算上限、取消、凭证过期重取、错误分类学、上下文窗口管理。**形状一模一样** |

## 三个设计选择，值得单独理解

### 为什么 validate 必须在 policy 之前

一个畸形调用如果先弹审批框，用户批准了才发现参数根本不对——白问一次。
所以顺序是：先验证、再判决、再执行。

### 为什么「显示的」和「执行的」必须是同一个对象

`Operation` 被 policy 判、被审批框显示、被执行器执行。三者拿到同一个对象。
如果显示的和执行的可能不同，那用户批准的就不是实际发生的事——审批就成了摆设。

### 为什么熔断表是第 0 步，而不是「一条优先级很高的规则」

规则表是可配置的。仓库里的 `.foundry/config.toml`、用户配置、hook，都能往里加东西。
熔断表不能是其中一条，因为**能被配置的东西就能被绕过**。它必须在流水线之外，
先于一切。

真 Foundry 为此有一条结构性不变量测试：540 个自动生成的组合，断言任何装饰、模式、
会话授权或 hook 改写，都不能让一条被禁命令变得可批准。这条测试的由来是：
**四轮对抗评审每一轮都攻破过命令分段器**，其中两次是把「不可批准的 DENY」
悄悄降级成了「可批准的 ASK」。

## demo 里故意留下的两个真实坑

读代码时会看到两处注释解释「为什么要多写这几行」，都是真事：

1. **`sys.stdout.reconfigure(encoding="utf-8")`** —— Windows 控制台默认不是 UTF-8，
   print 一个方框字符就 `UnicodeEncodeError`。平台细节不是杂活，是这类工具的正主。

2. **`PYTHONDONTWRITEBYTECODE=1`** —— 改完文件马上重跑测试时，如果新旧文件字节数
   相同、mtime 又落在同一个时钟刻度里，Python 会认定 `__pycache__` 里的 `.pyc`
   仍然有效，于是**跑的还是改之前的代码**。补丁明明打对了，测试却还是红的——
   agent 会陷入"改了又改"的死循环。这个坑是写这个 demo 时真撞上的。

## demo **没有**的东西

这些是 Foundry 真正花力气的地方，砍掉是为了让骨架看得清：

- 流式输出（demo 等模型说完；真的是逐字上屏，还要处理凭证跨两个分片的脱敏）
- 路径安全（demo 只做前缀比较；真的要处理 8.3 短名、reparse point、设备名、UNC、大小写）
- 命令分段（demo 用 `in` 做子串匹配，**这在真实环境下完全不够**）
- 上下文窗口管理、预算上限、取消、凭证过期重取
- 错误分类学（哪些重试、哪些停、`Retry-After` 怎么读）
- 补丁的锚定匹配与原子性（demo 直接整文件覆盖写）

最后一句：`--script destructive` 里那个 `git reset --hard` 之所以被拦住，靠的是
`"git reset --hard" in target` 这个子串匹配。真实世界里它可以写成 `git reset ,--hard`、
藏进 `<# ... #>` 注释、用 `&` 调用操作符包起来——每一种都绕开子串匹配。
**这就是那 443 行分段器存在的全部理由。**
