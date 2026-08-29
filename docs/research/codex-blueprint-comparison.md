# Codex 蓝图对比记录

> 对象：`origin/codex/create-branch-codex-blueprint`（PR #1，2026-08-29，保留作存档不合入）。
> 结论：以本 blueprint 为主干，吸收下列要点（2026-08-29 用户确认）。

## 独立收敛（互为验证，无需动作）

单一 runtime 拥有 loop、adapter 不得越权｜trusted-host 诚实披露、审批≠沙箱｜versioned JSONL 事件流 + 五种终止状态｜脏工作区支持 + baseline + 乐观并发写保护｜capability 声明式 backend｜fake/replay backend 离线测试｜workspace 边界须防 Windows reparse/设备名/ADS｜禁自动 commit/push、禁破坏性 git。

## 吸收清单（已落入主干文档）

1. **Canary 凭证泄漏套件**：测试用金丝雀 token，断言不出现在控制台/prompt/journal/artifact/异常/诊断（→ requirements §8、roadmap M3）。
2. **崩溃恢复语义**：JSONL 截尾记录可容忍；无终止事件的 session = `interrupted`，绝不判 completed（→ §6.1、M0）。
3. **内容寻址 artifacts**：`sessions/<id>/artifacts/<sha256>`，事件记 digest/大小/截断态（→ §6.1、design §9）。
4. **失败指纹**：按"归一化操作 + 错误类"计数重复失败，文本变化不重置（→ §5.1）。
5. **审批绑定 + 过期**：审批绑定精确操作 + cwd + env 策略 + 有效期，任一变化即失效（→ §4.1）。
6. **policy 决定审计字段**：记录 rule ID + 策略版本/摘要 + 操作摘要（→ design §6）。
7. **配置**：新增 env 层（`FOUNDRY_*`）；仓库配置与普通 CLI 参数禁止携带 secrets（→ §4.3）。
8. **文件工具路径 = workspace 相对**：拒绝绝对/设备路径，越界不可表达（→ §5.4；取代 Claude Code 绝对路径惯例，单根 workspace 下更安全）。
9. **完成披露**：claims 可为空但须显式声明"未执行验证"——披露有效、编造无效（→ §6.3）。
10. **测试**：假 HTTP server 的 adapter 合同测试；"断言违禁事件从未发生"式负向断言；生产 session 默认不转测试夹具（→ design §10）。
11. **公司路径合同**：CredentialSource = acquire/expiry/refresh/logout，机制可插拔；SecretHandle 命名（凭证不以可打印串流转）（→ §3.2、design §8）。
12. **M3 入场门**：先取得 Gateway 的 tool-call 流式脱敏夹具再动手实现（"Responses-compatible 可能只覆盖对话不覆盖工具续传"是真实风险）（→ roadmap M3）。
13. 细节：启动横幅显示生效限制与 policy、分支名醒目显示但不承载安全语义、配置值记录 provenance（来自哪层）、retention 用户可控。

## 新情报（用户确认属实，源自其给 Codex 的回答）

- 公司 Gateway：OpenAI 模型走 **Responses API**；token 由**内网 auth 流程**获取（HTTP 交换/内部可执行/浏览器 SSO 待确认）；Claude 模型存在但线协议未验证 → OQ-6 部分关闭；`responses` adapter 升回 M3 必选。
- Windows：不能创建 symlink、无 Developer Mode；proxy/自定义 CA/mTLS 非 V1 需求（能力保留，成本为零）。
- 开源意图 → 用户裁决**暂不开源**（D-021），license 推迟。

## 分歧裁决

| 分歧 | Codex 版 | 裁决 |
|---|---|---|
| apply_patch 默认 | workspace 内默认放行 | 维持默认交互审批，accept_edits 可切（D-023） |
| ChatGPT 认证 | 待调研 spike（其环境无外网，文档自陈不做事实声明） | 我方 blocked 实锤 + API key 决定（D-009）直接取代其 Phase 5 / Q-006 / R-001 |
| 开源 + Apache-2.0 | 开源意图 | 暂不开源（D-021） |

## 我方独有、对方缺失（保持不变）

认证可行性实锤｜编辑格式实证选型（锚定 search/replace + 宽容梯度）｜policy 六步流水线机制细节（mode 基线、内置规则摆放、持久化去向）｜ContextManager/masking/token 记账｜finish 工具作为 claims 产生通道｜shell、依赖预算、golden fixture 等已拍板决策｜事件总线/审批异步化的 UI 解耦设计。
