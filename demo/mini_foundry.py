#!/usr/bin/env python
"""迷你 Foundry —— 用一个文件讲清楚整套东西是怎么转的。

真 Foundry 有 7000 行。这里 600 行（其中一半是注释），**结构完全一样**，
只是每一层都砍到最薄。读完这个文件，你就知道 src/foundry/ 里每个模块在干什么。

    整个系统只有一句话：

        while 模型还在要求调用工具:
            policy 判决 -> 执行 -> 把结果塞回对话 -> 再问一次模型

    其余全部代码，都是在给这句话里的某个词加保护。

跑起来看（不需要网络、不需要 API key）：

    python demo/mini_foundry.py

看 policy 拦人：

    python demo/mini_foundry.py --script destructive

六个部分，从下往上读：
    1. IR       —— 对话的数据结构（对应 core/conversation.py）
    2. 工具     —— 模型能做的事      （对应 core/tools/）
    3. Policy   —— 哪些能做、哪些要问（对应 core/policy/）
    4. Session  —— 发生过什么的账本 （对应 core/session.py）
    5. Backend  —— 跟模型说话       （对应 core/backends/）
    6. Loop     —— 把上面五个缝起来 （对应 core/runtime.py）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# Windows 控制台默认不是 UTF-8，print 一个方框字符就 UnicodeEncodeError。
# 真 Foundry 到处都在处理这类事：子进程输出要先试 UTF-8 再试 OEM 代码页，
# 补丁要保住文件原本的 CRLF 和 BOM。平台细节不是杂活，是这类工具的正主。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


# ══════════════════════════════════════════════════════════════════════════
# 1. IR：对话长什么样
#
# 关键设计：这套结构**不属于任何一家模型厂商**。OpenAI 的 JSON、Anthropic 的 JSON
# 都在 backend 那一层翻译成它。这样换模型不影响 loop、policy、tools 一行代码。
#
# 真 Foundry: core/conversation.py。多了 Usage 记账、Capabilities 能力协商、
# 以及 ToolUseBlock.arguments 故意保持**原始字符串**——模型可能吐出坏 JSON，
# 提前解析会让"报告这个调用坏了"变成不可能。
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class ToolCall:
    """模型说：帮我调用这个工具。"""
    id: str
    name: str
    arguments: str          # 原始 JSON 字符串，不提前解析（理由见上）


@dataclass
class Message:
    """对话里的一条。role 是 user / assistant / tool。"""
    role: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""   # role == "tool" 时，这条结果回答的是哪个调用


@dataclass
class ModelTurn:
    """模型一次回复：说了点什么 + 想调哪些工具。"""
    text: str
    tool_calls: list[ToolCall]


# ══════════════════════════════════════════════════════════════════════════
# 2. 工具：模型能做的事
#
# 每个工具两件事：validate（这个调用合法吗）和 run（做）。
#
# **validate 必须在 policy 之前跑**。否则一个畸形调用会先弹一个审批框给用户，
# 用户批准了才发现参数根本不对。
#
# 真 Foundry: core/tools/。9 个工具。read_file 记住内容摘要以强制"改前先读"，
# apply_patch 用锚定 search/replace 且逐文件原子，run_command 用 Windows Job
# Object 保证子进程树能被整棵杀掉。
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class Operation:
    """一个**已经验证过**的调用。

    policy 判它、审批框显示它、执行器执行它——三者拿到的是同一个对象。
    这不是洁癖：如果显示的和执行的可能不同，用户批准的就不是实际发生的事。
    """
    tool: str
    args: dict
    display: str        # 给人看的一行字
    target: str         # policy 拿来匹配规则的键（路径，或命令原文）


class Tools:
    """四个工具，够演示了。"""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    # ---- 边界检查：所有文件工具的地基 ----
    def _resolve(self, relative: str) -> Path:
        path = (self.workspace / relative).resolve()
        # 真 Foundry 这里还要处理：8.3 短名、reparse point、大小写、设备名
        # （CON/NUL）、盘符相对路径（C:foo）、UNC。见 core/workspace.py。
        if not str(path).startswith(str(self.workspace.resolve())):
            raise ValueError(f"路径在 workspace 之外：{relative}")
        return path

    def validate(self, call: ToolCall) -> Operation:
        try:
            args = json.loads(call.arguments)
        except json.JSONDecodeError as exc:
            raise ValueError(f"参数不是合法 JSON：{exc}") from exc

        if call.name == "read_file":
            path = args["path"]
            self._resolve(path)                       # 越界在这里就拒
            return Operation("read_file", args, f"读 {path}", path)

        if call.name == "write_file":
            path = args["path"]
            self._resolve(path)
            return Operation("write_file", args, f"写 {path}", path)

        if call.name == "run_command":
            command = args["command"]
            return Operation("run_command", args, f"跑 {command}", command)

        if call.name == "finish":
            return Operation("finish", args, "收工", "")

        raise ValueError(f"没有这个工具：{call.name}")

    def run(self, op: Operation, session: "Session") -> str:
        if op.tool == "read_file":
            return self._resolve(op.args["path"]).read_text(encoding="utf-8")

        if op.tool == "write_file":
            path = self._resolve(op.args["path"])
            path.write_text(op.args["content"], encoding="utf-8")
            return f"已写入 {op.args['path']}（{len(op.args['content'])} 字符）"

        if op.tool == "run_command":
            # PYTHONDONTWRITEBYTECODE：改完文件马上重跑测试时，如果新旧文件字节数
            # 相同、mtime 又落在同一个时钟刻度里，Python 会认定 __pycache__ 里的
            # .pyc 仍然有效，于是**跑的还是改之前的代码**——补丁明明打对了，测试
            # 却还是红的。这类环境噪声会让 agent 陷入"改了又改"的死循环。
            #
            # 真 Foundry 走得更远：子进程环境是**白名单**，只留构建真正需要的变量。
            # 批准一次 pytest，不该顺带把你 shell 里所有 API key 交给仓库的测试代码。
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            done = subprocess.run(op.args["command"], shell=True, cwd=self.workspace,
                                  capture_output=True, text=True, timeout=60, env=env)
            output = (done.stdout + done.stderr).strip()
            # **证据链的起点**：命令执行被记进账本并拿到一个事件号。稍后 finish
            # 声称"测试通过了"时，我们回来查这个号——那一条到底是不是 exit 0。
            event_id = session.record("command_exec", {
                "command": op.args["command"],
                "exit_code": done.returncode,
            })
            return f"exit code {done.returncode}\n{output[:2000]}\n[event_id={event_id}]"

        if op.tool == "finish":
            return ""       # 由 loop 特殊处理，见第 6 节

        raise ValueError(op.tool)

    @staticmethod
    def schemas() -> list[dict]:
        """告诉模型有哪些工具可用。真 Foundry 的 schema 里还带例子和反例。"""
        return [
            {"name": "read_file", "description": "读一个文件",
             "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                            "required": ["path"]}},
            {"name": "write_file", "description": "覆盖写一个文件",
             "parameters": {"type": "object",
                            "properties": {"path": {"type": "string"},
                                           "content": {"type": "string"}},
                            "required": ["path", "content"]}},
            {"name": "run_command", "description": "在 workspace 里跑一条命令",
             "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                            "required": ["command"]}},
            {"name": "finish", "description": "任务做完了，报告结果",
             "parameters": {"type": "object",
                            "properties": {"summary": {"type": "string"},
                                           "claim_event_id": {"type": "integer"}},
                            "required": ["summary"]}},
        ]


# ══════════════════════════════════════════════════════════════════════════
# 3. Policy：哪些能做、哪些要问、哪些永远不行
#
# 真 Foundry 是**六步流水线**，这里保留了它的骨架和最重要的那个性质：
#
#     第 0 步  熔断表   —— 任何规则、模式、授权、hook 都无法覆盖
#     第 1 步  DENY 规则
#     第 2 步  ASK 规则
#     第 3 步  模式基线（只读 / 自动改 / 全问 / 全拒）
#     第 4 步  ALLOW 规则
#     第 5 步  问人
#
# **顺序就是全部含义**：deny 压过 allow，熔断压过一切。把熔断放在第 0 步而不是
# 做成一条"优先级很高的规则"，是因为规则表是可配置的，而这几条不能是。
#
# 真 Foundry: core/policy/。熔断表还要对付"同一条命令的两种读法"——
# `git reset --hard` 可以写成 `git reset ,--hard`、藏在 `<# #>` 注释里、
# 用 `&` 调用操作符包起来。五轮对抗评审每一轮都攻破过分段器，所以熔断表
# 现在**同时扫描一个故意很笨的读法**：它不理解引号和注释，因此骗不了它。
# ══════════════════════════════════════════════════════════════════════════

ALLOW, ASK, DENY = "allow", "ask", "deny"

# 第 0 步。这张表不接受配置，不接受授权，不接受 hook 改写。
FORBIDDEN = [
    ("git push", "永不发布"),
    ("git commit", "永不替你提交"),
    ("git reset --hard", "会毁掉未提交的工作"),
    ("rm -rf /", "毁灭性删除"),
]


@dataclass
class Decision:
    verdict: str
    reason: str
    step: int


class Policy:
    def __init__(self, mode: str = "default", allow_rules: list[str] | None = None):
        self.mode = mode                       # default | accept_edits | plan
        self.allow_rules = allow_rules or []
        self.session_grants: set[str] = set()  # 本次会话内批准过的

    def evaluate(self, op: Operation) -> Decision:
        # ---- 第 0 步：熔断表。先于一切。----
        for pattern, why in FORBIDDEN:
            if pattern in op.target:
                return Decision(DENY, f"『{pattern}』{why}，且不可批准", 0)

        # ---- 第 3 步：模式基线（只读工具直接放行）----
        if op.tool in ("read_file", "finish"):
            return Decision(ALLOW, "只读", 3)

        if self.mode == "plan":
            return Decision(DENY, "plan 模式下不做任何改动", 3)

        # ---- 第 4 步：ALLOW 规则 + 会话授权 ----
        if op.target in self.session_grants:
            return Decision(ALLOW, "本次会话中已批准过", 4)
        for rule in self.allow_rules:
            if op.target.startswith(rule):
                return Decision(ALLOW, f"匹配规则 {rule!r}", 4)

        if self.mode == "accept_edits" and op.tool == "write_file":
            return Decision(ALLOW, "accept_edits 模式", 3)

        # ---- 第 5 步：问人 ----
        return Decision(ASK, "没有规则匹配，需要确认", 5)


def ask_human(op: Operation, decision: Decision, auto: str | None) -> bool:
    """真 Foundry 这里是一个事件 + 一个回答，所以 headless 模式可以把所有 ASK
    自动判成 DENY（fail-closed），而不是把 input() 硬编码在工具里。"""
    print(f"\n  ⟨审批⟩ {op.display}")
    print(f"         理由：{decision.reason}")
    if auto is not None:
        print(f"         自动回答：{auto}")
        return auto == "y"
    return input("         允许？[y/N] ").strip().lower() == "y"


# ══════════════════════════════════════════════════════════════════════════
# 4. Session：发生过什么的账本
#
# 只追加，一行一个 JSON。两个用途：
#   (a) 出了事能查——谁批准了什么、跑了什么命令、结果如何；
#   (b) **证据链**——finish 声称"测试通过了"时，回来查那条命令的 exit code。
#
# 真 Foundry: core/session.py。多了内容寻址的 artifact 存储（大输出不塞进对话，
# 存成文件让模型按需分页读）、凭证脱敏、以及"写不进去也不能让整轮崩掉"的降级。
# ══════════════════════════════════════════════════════════════════════════

class Session:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("w", encoding="utf-8")
        self.ordinal = 0
        self.events: list[dict] = []

    def record(self, event_type: str, payload: dict) -> int:
        self.ordinal += 1
        entry = {"n": self.ordinal, "type": event_type, "payload": payload}
        self.events.append(entry)
        self.file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.file.flush()
        return self.ordinal

    def find_command(self, event_id: int) -> dict | None:
        for entry in self.events:
            if entry["n"] == event_id and entry["type"] == "command_exec":
                return entry["payload"]
        return None

    def close(self) -> None:
        self.file.close()


# ══════════════════════════════════════════════════════════════════════════
# 5. Backend：跟模型说话
#
# 它只做**翻译**：IR 进去，厂商 JSON 出来；厂商 JSON 进来，IR 出去。
# 它绝对不能自己跑循环——否则就有两个地方在决定"下一步做什么"了。
#
# 这里用一个写死的脚本代替真模型，好处是：不用 API key、结果确定、
# 每次跑都一样。真 Foundry 的测试套件（1436 个）全靠同样的东西才能在
# 没网没凭证的机器上跑。
#
# 真 Foundry: core/backends/。openai_compat（Chat Completions）和 responses
# 两个 adapter，加 stdlib 写的 HTTP/SSE 客户端。
# ══════════════════════════════════════════════════════════════════════════

def _call(cid: str, name: str, **args) -> ToolCall:
    return ToolCall(cid, name, json.dumps(args))


FIXED_CODE = "def add(a, b):\n    return a + b\n"

# 占位符：backend 发出前会换成对话里最后一个 [event_id=N]。见下面 SCRIPTS 的注释。
LAST_EVENT_ID = -1

SCRIPTS = {
    # 正常剧本：跑测试 → 看代码 → 改 → 再跑 → 带证据收工
    "fix": [
        ModelTurn("先跑一下测试，看看错在哪。",
                  [_call("c1", "run_command", command="python -m pytest -q")]),
        ModelTurn("测试挂了。看看源码。",
                  [_call("c2", "read_file", path="calc.py")]),
        ModelTurn("`add` 写成了减法。改掉。",
                  [_call("c3", "write_file", path="calc.py", content=FIXED_CODE)]),
        ModelTurn("再跑一次确认。",
                  [_call("c4", "run_command", command="python -m pytest -q")]),
        # claim_event_id 写的是 LAST_EVENT_ID，backend 会在发出前替换成对话里
        # 最后一个 [event_id=N]。真模型就是这么干的：它在自己的工具输出里读到
        # 那个编号，然后引用它。写死一个数字是不行的——编号取决于这一轮到底
        # 发生了多少事。
        ModelTurn("", [_call("c5", "finish",
                             summary="修好了 add() 的符号错误。",
                             claim_event_id=LAST_EVENT_ID)]),
    ],
    # 拦截剧本：模型（或者藏在文件里的注入）要求做一件永远不许做的事
    "destructive": [
        ModelTurn("我来清理一下工作区。",
                  [_call("c1", "run_command", command="git reset --hard HEAD")]),
        ModelTurn("那换个方式提交。",
                  [_call("c2", "run_command", command="git commit -am wip")]),
        ModelTurn("好吧，我只看看文件。",
                  [_call("c3", "read_file", path="calc.py")]),
        ModelTurn("", [_call("c4", "finish", summary="什么都没改。")]),
    ],
    # 撒谎剧本：什么都没修，却声称"全部测试通过"，还老老实实引用了那条命令。
    # 引用是真的，命令也真跑过——但它 exit code 是 1。闸门查得出来。
    "liar": [
        ModelTurn("跑个测试。",
                  [_call("c1", "run_command", command="python -m pytest -q")]),
        ModelTurn("", [_call("c2", "finish",
                             summary="全部测试通过，任务完成。",
                             claim_event_id=LAST_EVENT_ID)]),
    ],
}


class ScriptedBackend:
    """按顺序吐出写好的回复。真 backend 在这里发 HTTP 请求。"""

    def __init__(self, turns: list[ModelTurn]):
        self.turns = turns
        self.index = 0

    def sample(self, messages: list[Message], tools: list[dict]) -> ModelTurn:
        # 真 backend 在这里：把 messages 翻成厂商 JSON、带上 tools、发请求、
        # 解析 SSE 流、再翻回 ModelTurn。见 core/backends/openai_compat.py。
        if self.index >= len(self.turns):
            return ModelTurn("（脚本演完了）", [])
        turn = self.turns[self.index]
        self.index += 1
        return ModelTurn(turn.text, [self._resolve(c, messages) for c in turn.tool_calls])

    @staticmethod
    def _resolve(call: ToolCall, messages: list[Message]) -> ToolCall:
        """把 LAST_EVENT_ID 占位符换成对话里真实出现过的最后一个事件号。

        这不是脚本机制的花招——它就是真模型的行为：工具结果里带着
        `[event_id=N]`，模型读到它，然后在 finish 里引用它。
        """
        if f'"claim_event_id": {LAST_EVENT_ID}' not in call.arguments:
            return call
        seen = re.findall(r"\[event_id=(\d+)\]",
                          "\n".join(m.text for m in messages if m.role == "tool"))
        latest = int(seen[-1]) if seen else 0
        return ToolCall(call.id, call.name, call.arguments.replace(
            f'"claim_event_id": {LAST_EVENT_ID}', f'"claim_event_id": {latest}'))


SYSTEM_PROMPT = """你是一个在本地 Git 仓库里工作的编码 agent。

先看清楚，再动手，最后验证。改文件之前先读它。做完之后调用 finish 报告——
如果你跑了命令来验证，把那条命令结果里的 [event_id=N] 填进 claim_event_id，
我们会去核对它的 exit code。没有验证过就别填，谎报会被查出来。
"""


class HttpBackend:
    """真的跟模型说话。stdlib only，非流式，够看懂就行。

    这是 core/backends/openai_compat.py 的三十行版本。真的那个还要处理
    SSE 流式、usage 记账、错误分类、重试与 Retry-After、以及一个只支持
    非流式的网关该怎么降级。

    它只做**翻译**：IR 进去、厂商 JSON 出来；厂商 JSON 进来、IR 出去。
    注意它不知道 loop、policy、tools 的存在——换模型不影响那三样。
    """

    def __init__(self, base_url: str, model: str, api_key: str = "any-value",
                 timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.last_response: dict | None = None      # 方便在 notebook 里翻看

    def sample(self, messages: list[Message], tools: list[dict]) -> ModelTurn:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}]
                        + [self._to_wire(m) for m in messages],
            "tools": [{"type": "function", "function": t} for t in tools],
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {self.api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 服务器答了，但不是 200。把它自己的话带出来——真 Foundry 在这里栽过：
            # 响应体被存下来却从没人读，用户只看到 "request rejected (HTTP 400)"，
            # 而 body 里明写着哪个字段不对。
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            # 根本没连上。这是指错端口最常见的下场，得说人话而不是吐 traceback。
            raise RuntimeError(
                f"连不上 {self.base_url}（{getattr(exc, 'reason', exc)}）。"
                "检查 endpoint 和端口，或者去掉 --endpoint 用剧本模式。"
            ) from exc

        self.last_response = payload
        message = (payload.get("choices") or [{}])[0].get("message", {}) or {}
        calls = [
            ToolCall(c.get("id") or f"call_{i}",
                     (c.get("function") or {}).get("name") or "",
                     # `or "{}"`：一个存在但为 null 的 arguments 会让
                     # json.loads(None) 抛 TypeError。真 Foundry 在这里栽过。
                     (c.get("function") or {}).get("arguments") or "{}")
            for i, c in enumerate(message.get("tool_calls") or [])
        ]
        return ModelTurn(message.get("content") or "", calls)

    @staticmethod
    def _to_wire(message: Message) -> dict:
        if message.role == "tool":
            return {"role": "tool", "tool_call_id": message.tool_call_id,
                    "content": message.text}
        wire: dict = {"role": message.role, "content": message.text}
        if message.tool_calls:
            wire["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": c.arguments}}
                for c in message.tool_calls
            ]
        return wire


# ══════════════════════════════════════════════════════════════════════════
# 6. Loop：把上面五个缝起来
#
# 这就是整个系统。三十来行。
#
# 真 Foundry: core/runtime.py。多的是预算上限（轮数/调用数/token）、
# 取消、凭证过期重取、错误分类学（哪些重试哪些停）、上下文窗口管理。
# 但**形状一模一样**。
# ══════════════════════════════════════════════════════════════════════════

MAX_ROUNDS = 12          # 防跑飞。真 Foundry 还按 token 和调用次数算。


def run(task: str, backend: ScriptedBackend, tools: Tools, policy: Policy,
        session: Session, auto: str | None) -> int:
    messages = [Message("user", task)]
    session.record("task", {"text": task})

    for round_no in range(1, MAX_ROUNDS + 1):
        turn = backend.sample(messages, tools.schemas())
        if turn.text:
            print(f"\n模型：{turn.text}")

        # 模型没有要调工具 —— 它只是在回答。这一轮结束。
        if not turn.tool_calls:
            print("\n（模型没有要求调用工具，结束。）")
            return 0

        messages.append(Message("assistant", turn.text, tool_calls=turn.tool_calls))

        for call in turn.tool_calls:
            # ---- 第一步：验证。在 policy 之前。----
            try:
                op = tools.validate(call)
            except ValueError as exc:
                print(f"  ✗ 调用不合法：{exc}")
                # 把错误**还给模型**，让它自己改。不要直接崩掉整轮——
                # 模型看到错误信息通常下一轮就修对了。
                messages.append(Message("tool", f"错误：{exc}", tool_call_id=call.id))
                continue

            # ---- 第二步：policy 判决 ----
            decision = policy.evaluate(op)
            session.record("policy_decision", {
                "target": op.target, "verdict": decision.verdict,
                "reason": decision.reason, "step": decision.step,
            })

            if decision.verdict == DENY:
                print(f"  ⛔ 拒绝（第 {decision.step} 步）：{decision.reason}")
                messages.append(Message("tool", f"被策略拒绝：{decision.reason}",
                                        tool_call_id=call.id))
                continue

            if decision.verdict == ASK:
                if not ask_human(op, decision, auto):
                    print("  ⛔ 用户拒绝")
                    messages.append(Message("tool", "用户拒绝了这个操作",
                                            tool_call_id=call.id))
                    continue
                policy.session_grants.add(op.target)

            # ---- 第三步：执行 ----
            print(f"  → {op.display}")
            if op.tool == "finish":
                return finalize(op, session)
            try:
                result = tools.run(op, session)
            except Exception as exc:                       # noqa: BLE001
                result = f"工具执行失败：{exc}"
            print(f"    {result.splitlines()[0] if result else '(空)'}")

            # ---- 第四步：把结果塞回对话，然后再问模型 ----
            messages.append(Message("tool", result, tool_call_id=call.id))

    print(f"\n⚠ 到了 {MAX_ROUNDS} 轮上限还没收工。")
    return 10


def finalize(op: Operation, session: Session) -> int:
    """收工闸门：**只有这里能报告"完成"**，而且声称要有证据。

    模型说"测试通过了"是不够的——它得指出**哪一条命令**证明了这件事，
    我们回账本里查那条命令的 exit code。对不上就降级成 partial。

    真 Foundry: core/tools/finish.py + runtime._finalize。还会检查
    HEAD 有没有被移动过、以及哪些文件其实是这次会话改的。
    """
    summary = op.args.get("summary", "")
    claim_id = op.args.get("claim_event_id")

    print(f"\n收工：{summary}")

    if claim_id is None:
        print("状态：completed（未声称任何验证）")
        session.record("termination", {"status": "completed", "summary": summary})
        return 0

    command = session.find_command(claim_id)
    if command is None:
        print(f"状态：partial —— 引用的事件 {claim_id} 不是一条命令记录")
        session.record("termination", {"status": "partial", "summary": summary})
        return 10
    if command["exit_code"] != 0:
        print(f"状态：partial —— 事件 {claim_id}（{command['command']}）"
              f"实际 exit code 是 {command['exit_code']}，不是 0")
        session.record("termination", {"status": "partial", "summary": summary})
        return 10

    print(f"状态：completed —— 证据核对通过"
          f"（事件 {claim_id}：{command['command']} → exit 0）")
    session.record("termination", {"status": "completed", "summary": summary})
    return 0


# ══════════════════════════════════════════════════════════════════════════
# 7. main：搭好线，跑起来
# ══════════════════════════════════════════════════════════════════════════

SAMPLE = {
    "calc.py": "def add(a, b):\n    return a - b\n",
    "test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
}


def make_sample_repo(root: Path) -> Path:
    """每次都从头建，这样重复跑的结果是确定的（上一次的修复不会留下来）。"""
    workspace = root / "sample_repo"
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)
    for name, content in SAMPLE.items():
        (workspace / name).write_text(content, encoding="utf-8")
    return workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="迷你 Foundry")
    parser.add_argument("--script", default="fix", choices=sorted(SCRIPTS),
                        help="用哪个剧本（fix=正常修 bug，destructive=被熔断表拦，"
                             "liar=假证据被识破）")
    parser.add_argument("--mode", default="default",
                        choices=["default", "accept_edits", "plan"])
    parser.add_argument("--yes", action="store_true", help="所有审批自动同意")
    parser.add_argument("--no", action="store_true", help="所有审批自动拒绝（等于 headless）")
    parser.add_argument("--workdir", default=str(Path(__file__).parent / ".run"))
    parser.add_argument("--endpoint", help="用真模型而不是剧本，例如 "
                                           "http://127.0.0.1:18790/v1")
    parser.add_argument("--model", default="openclaw/trade-advisor-panel")
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", "any-value"))
    parser.add_argument("--task", default="calc.py 里的 add 有 bug，修好它并确认测试通过。")
    args = parser.parse_args(argv)

    root = Path(args.workdir)
    workspace = make_sample_repo(root)
    session = Session(root / "session.jsonl")
    auto = "y" if args.yes else ("n" if args.no else None)

    if args.endpoint:
        backend = HttpBackend(args.endpoint, args.model, args.api_key)
        source = f"{args.model} @ {args.endpoint}"
    else:
        backend = ScriptedBackend(SCRIPTS[args.script])
        source = f"剧本 {args.script}"

    print("─" * 68)
    print(f"workspace : {workspace}")
    print(f"模型      : {source}      模式：{args.mode}")
    print(f"账本      : {session.path}")
    print("─" * 68)

    started = time.monotonic()
    try:
        code = run(
            task=args.task,
            backend=backend,
            tools=Tools(workspace),
            policy=Policy(mode=args.mode, allow_rules=["python -m pytest"]),
            session=session,
            auto=auto,
        )
    except RuntimeError as exc:
        print(f"\n后端出错：{exc}")
        code = 12
    finally:
        session.close()

    print("─" * 68)
    print(f"退出码 {code}（0=完成 10=部分完成）  用时 {time.monotonic() - started:.1f}s")
    print(f"账本里有 {session.ordinal} 条记录，`type {session.path}` 可以看全部。")
    return code


if __name__ == "__main__":
    sys.exit(main())
