# Foundry 决策日志

> 格式：每条决定一个编号。**状态**：`已确认`（用户拍板）/ `暂定`（Claude 建议、待用户确认）/ `已推翻`。
> 推翻一条决定时不删除，改状态并链接到新决定。

---

## D-001 全新独立 runtime，不 fork / 不调用现有 coding agent
- **日期**：2026-08-29　**状态**：已确认
- **内容**：Foundry 自己拥有 agent loop、tools、policy、provider、session 的全部代码和接口。可以研读 Codex、Claude Code、gemini-cli 等公开源码借鉴设计，但不 fork、不 vendored、不在运行时调用它们。
- **理由**：公司环境不允许安装官方 agent 产品（只能走公司 Gateway 的 API）；同时项目本身有学习目的（吃透 agent runtime 设计）。

## D-002 Python 3.12 + wheel 离线安装
- **日期**：2026-08-29　**状态**：已确认
- **内容**：固定 Python 3.12；构建标准 wheel；目标安装方式 `python -m pip install --no-index --find-links <internal-wheel-dir> foundry`；依赖锁版本、带 hashes；不要求 Node.js / Rust toolchain。
- **理由**：公司电脑软件源受限（JFrog Artifactory，Node/Rust 能否用视具体情况）；Python + 内部 wheel 目录是最确定可行的分发通道。

## D-003 V1 是 trusted-host，不宣称 sandbox
- **日期**：2026-08-29　**状态**：已确认
- **内容**：V1 只用于可信仓库。审批（policy）是行为约束，不是安全边界；文档和 CLI 首次运行时都要明确披露这一点。
- **理由**：Windows 上做真 sandbox（如 AppContainer/Job Object 限制）成本高且容易给人虚假安全感；诚实披露优于半吊子沙箱。

## D-004 交互式终端会话优先，headless 后补
- **日期**：2026-08-29　**状态**：已确认（用户第一轮回答）
- **内容**：V1 核心形态是交互式终端会话：对话、流式输出、副作用动作当场审批（ASK）。headless 一次性模式（`foundry exec` 之类）后续版本再补。
- **架构约束**：UI 与 AgentRuntime 从第一天解耦——runtime 对外只暴露事件流 + 审批回调接口，终端 UI 只是第一个消费者。这样 headless 模式只是换一个"自动回答审批"的消费者，不动 loop。

## D-005 ChatGPT 登录保持最高优先级
- **日期**：2026-08-29　**状态**：已确认（用户第一轮回答，否决了 Claude 的"API key 先行"建议）
- **内容**：个人路径的 ChatGPT 浏览器登录仍是第一优先硬性需求。
- **理由（用户原话大意）**：开发环境没有 OpenAI API key，无法用 API 验证；ChatGPT 订阅是唯一可用的真实模型访问，登录打通了才能做真实 E2E 验证。
- **风险与缓解**：这仍是全项目最不确定的一项（第三方使用 ChatGPT 订阅可能无受支持途径，见 [OQ-5](open-questions.md)）。缓解措施：
  1. runtime 主体开发不依赖真实模型——必须先有 **mock / record-replay ModelBackend**，让 loop、tools、policy、session 全部可离线测试；
  2. 评估用**本地 OpenAI 兼容端点**（LM Studio / Ollama 等）作为 dev-only backend 做冒烟验证（见 [OQ-8](open-questions.md)）；
  3. 可行性研究给出证据后再定 fallback 阶梯，不偷偷降级。

## D-006 公司 Gateway 是多模型的（含 Claude 等非 OpenAI 模型）
- **日期**：2026-08-29　**状态**：已确认（用户第一轮回答）
- **内容**：公司 Gateway 上挂多家模型。草稿"优先支持 OpenAI Responses-compatible"的假设**不成立为唯一假设**。
- **架构约束**：
  1. 内部消息 / 工具调用 / 流式事件必须是 **provider-agnostic 的自有规范**，各协议（Chat Completions / Responses / Anthropic Messages / Gateway 自有协议）各写窄 adapter；
  2. 工具面（尤其编辑工具的格式）不能绑死 OpenAI 特化格式（如 V4A apply_patch），需要按模型族可配置或选择中性格式（见 [OQ-7](open-questions.md)）；
  3. Gateway 具体协议、认证、模型清单待用户确认（见 [OQ-6](open-questions.md)）。

## D-007 设计优先级排序：可审计 / 少依赖 / 清晰实现 > 功能堆砌
- **日期**：2026-08-29　**状态**：已确认（由动机推导）
- **内容**：动机 = 公司合规约束 + 学习目的。用户用 Claude Agent SDK 攒过 mini-codex，体验一般——Foundry 要赢在"完全掌控 + 设计质量"，而不是功能数量。
- **推论**：宁可工具面窄而可靠；每个依赖都要过"值不值得进离线 wheel 目录"的审查；决策和取舍必须留痕（本文档）。

## D-008 Claude consumer OAuth 不属于 V1
- **日期**：2026-08-29　**状态**：已确认（草稿继承）
- **内容**：个人路径 V1 只做 ChatGPT/OpenAI；Claude 订阅登录不做。公司路径里的 Claude 模型走 Gateway，与 consumer OAuth 无关。
