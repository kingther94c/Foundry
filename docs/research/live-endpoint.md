# 首次真实 endpoint 验证（2026-09-01）

在此之前，Foundry 的每一次「端到端」都跑在 `ScriptedBackend` 或测试内起的
`HTTPServer` 上。用户提供了一个本机 OpenClaw 网关，第一次让这套代码对着**真的
HTTP 服务**说话。

## 环境

| | 地址 | 协议 | 认证 |
|---|---|---|---|
| 网关 | `http://127.0.0.1:18789/v1` | Chat Completions | `gateway.auth.token`（真 token） |
| Responses facade | `http://127.0.0.1:18790/v1` | Responses | **任意** bearer 值 |

facade 把 Responses 请求翻译成网关的 `/v1/chat/completions`，再把回答重新包装成
Responses 对象——形状是模拟的，答案是真的。

## 关键结论：这个 endpoint 不能验证 tool call

网关**接受** `tools` / `tool_choice`，甚至会校验：`tool_choice="required"` 得不到
工具调用时返回 `HTTP 502 "tool_choice=required was not satisfied by the agent
response"`。但它背后的 7 个模型（都是 trade-advisor 系）**从不产出 tool_calls**：

```
openclaw/default             tool_choice=auto     -> finish=stop, tool_calls=none
openclaw/default             tool_choice=required -> HTTP 502
openclaw/trade-advisor-panel tool_choice=required -> HTTP 502
```

所以 **[OQ-6](../open-questions.md) 依然开着**。M3 的入场门要的是「真实 Gateway 的
tool-call 流式脱敏夹具」，这台机器给不出来——把 facade 改成转发 `tools` 也没用，
因为不产出工具调用的是模型，不是 facade。不要因为「跑通了」就把 M3 标成完成。

补充一点让人稍微安心的：tool-call 的 SSE 分片重组**本来就**在真 socket 上测过
（`test_backend_openai.py::test_streaming_reassembles_text_and_tool_calls` 与
`test_streaming_handles_parallel_tool_calls` 起的是真的 `HTTPServer`），只是服务端
是合成的。缺的那块是「没人见过真实公司 Gateway 吐 tool call 长什么样」。

## 验证到了什么

两个 adapter 都对着真服务跑通了完整一轮，含流式：

| | 协议 | 结果 |
|---|---|---|
| `openai_compat` → 18789 | Chat Completions | `OK`，usage 17095/20，真 token 未泄漏 |
| `responses` → 18790 | Responses | `OK`，usage 17090/23，真 token 未泄漏 |

`responses` adapter 此前**从未对任何真实服务器跑过**（D-022 把它升为 M3 必选时就
标注了「真实协议行为待验证」）。现在它对着一个真的 Responses 形状的 endpoint 跑通了
流式、usage 与终止事件。

真实线格式里两处值得记的形状，已作为字节级夹具收进
`tests/fixtures/live_gateway/`：

- Chat Completions 的**最后一个 chunk 带 usage 但 `choices` 是空数组**。先取
  `choices[0]` 再找 usage 的 adapter 会把每一轮流式的 token 数报成 0。
- facade 的 Responses SSE 每帧前面有 `event: <name>` 行，且仍以 `data: [DONE]` 收尾。

## 找到并修掉的四个缺陷

真服务立刻暴露了脚本化 backend 够不着的东西：

1. **`--json` 事件流有损**。序列化走的是一张手工维护的属性白名单，没人想起来加的
   字段就被静默丢掉：`token_count` 输出成 `{"kind": "token_count"}`，一个数字都没有
   ——而旁边的 journal 记着真实计数；`tool_begin`/`tool_end` 没有 `call_id`，消费者
   无法配对。改为序列化事件实际携带的内容。
2. **流里没有终止事件**。正常结束的 headless run 不会走到 `runtime._terminate`，
   于是 `--json` 就那么停了：CI 消费者在流里拿不到最终状态，只能从退出码反推。
3. **exit 10 没有解释**。`"headless run ended"` 说不清一个看起来完全正确的回答为什么
   是 PARTIAL。对一个**根本不会调工具**的模型来说，这是唯一可达的结局。
4. **loopback 会被送进公司代理**。urllib 的 bypass 列表基本不会写 `127.0.0.1`
   （`proxy_bypass('127.0.0.1')` 返回 `False`），所以在设了 `HTTP_PROXY` 的机器上
   ——也就是 Foundry 的目标环境，永远如此——本机网关的请求被发给代理，而代理路由不回
   调用方自己的 loopback。表现为连接超时，和「本地服务没起来」完全一样。

第 4 条是这次最有价值的：它只在「有本地 endpoint + 有公司代理」时才会发生，而这
恰好就是用户的真实拓扑。没有这台本机网关，它会一直躲着。

## 顺藤摸瓜：代理路径审计

第 4 条逼出一个问题——「代理这条路还有什么没人看过？」于是拿真实抓包做了一次五视角
审计（SSE 分帧 / chat adapter / responses adapter / 错误与重试 / loopback 与代理），
每条发现再交给一个「任务是驳倒它」的验证者。33 条被驳回 23 条，剩 10 条。

**其中一条是整个项目最严重的缺陷**：设了公司代理时，API key 以**明文**穿过 CONNECT
隧道。详见 [threat-model.md](../threat-model.md) §3(b3)——那里也记了为什么六轮对抗评审
都没碰到它。

其余九条（均已修复，见 `tests/test_transport_audit.py`）：

| | 缺陷 |
|---|---|
| high | 断流的注释承诺「会重试」，但没有任何一层真的重试；且它会终结整个 REPL 会话，用户丢掉全部上下文 |
| medium | `Retry-After` 只在 429 上读、只认秒数形式；503 说「30 秒后再来」被无视，改成 1/3/7 秒连打三次 |
| medium | `request_max_retries = 0`（关掉重试最自然的写法）走到 `raise None` → TypeError，CLI 直接吐 traceback |
| medium | 错误响应体被存进 `payload` 后从没人读，于是 400 只显示 "request rejected (HTTP 400)"，而 body 里明写着哪个字段不对 |
| medium | `SSL_CERT_FILE` 指向不存在的文件时，抛裸 `FileNotFoundError`（连文件名都没有），在错误分类学之外 |
| medium | `arguments: null` 的 tool call 让非流式路径崩溃（`json.loads(None)` 抛 TypeError，`parse_arguments` 不接） |
| medium | NotStreaming 降级时 `stream_options` 没跟着摘掉，严格网关回 400——救场的路径反而把这轮弄挂 |
| medium | 只有一行 `data: [DONE]` 的流被当成「成功的空回答」 |
| low | 非隧道分支丢掉代理凭证，407 的提示还叫用户去做他已经做过的事 |

## 复现

```bash
curl http://127.0.0.1:18790/healthz
python -m pytest tests/test_live_endpoint_findings.py -q   # 离线，用字节级夹具
```
