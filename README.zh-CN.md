<p align="center">
  <img src="assets/banner.svg" alt="STOA Agent — 六个角色，一座会议厅，在你本地" width="100%">
</p>

# STOA Agent ⁂

**六个角色，一座会议厅，在你本地运行。**
默认 Council 辩论模式 · BYO Key（自带 API Key）· 无订阅、无锁定。

> STOA 是一座苏格拉底式的会议厅：六个命名角色 —— 各自拥有不同的角色定位、系统提示词和推理风格 —— 针对每一个非平凡任务并行辩论，然后由第七个调度者综合出最终裁定。STOA 本身永远免费且开源；你绑定自己的 LLM API Key（我们推荐 DeepSeek 免费版 —— 2 分钟注册、无需信用卡），STOA 不持有、不代理、也不转发你的 Key。想要「一席一供应商」（例如 Sokrates → Anthropic，Veritas → Google，Drax → xAI）？在 `~/.stoa/cli-config.yaml` 里逐席绑定不同的 Key。

本项目基于 [NousResearch / hermes-agent](https://github.com/NousResearch/hermes-agent) v0.14.0 (MIT) Fork。Runtime、Gateway、沙箱、记忆存储、Skill 格式、Provider 插件层全部沿用上游 —— 完整渊源见 [ATTRIBUTION.md](ATTRIBUTION.md)。STOA 在其之上新增的是「会议厅」：角色编排、可选的链上证明 Preview、以及发布 Skill 时的 6 角色审计闸门。

---

## 安装

**macOS · Linux · WSL2 · Termux**
```sh
curl -fsSL https://stoax.xyz/install.sh | sh
```

**Windows · PowerShell**
```powershell
iex (irm https://stoax.xyz/install.ps1)
```

**PyPI**
```sh
pip install stoa-agent
stoa setup
```

**Homebrew**
```sh
brew tap stoagent/stoa
brew install stoa-agent
```

---

## STOA 新增了什么

### 1. Council 模式 —— 六个角色，一座会议厅

每个角色有独立的系统提示词、推理风格和工具偏好。它们针对同一任务并行运行，由第七个调度者综合裁定 —— 不是把答案压平为一个声音，而是同时呈现共识与异议。

```sh
stoa /council "审计这份合约: $(cat MyToken.sol)"
# → 6 个角色并行
# → Sokrates / Mira / Veritas / Drax / Lyra / Echo 各自回应
# → 综合裁定 + 各角色异议 + 响应哈希（本地）
```

| 角色 | 定位 |
|---|---|
| **Sokrates** | 提问者 —— 揭示隐藏假设 |
| **Mira** | 建造者 —— 产出具体可用的工件 |
| **Veritas** | 审计者 —— 寻找正确性问题 |
| **Drax** | 红队 —— 寻找失败模式 |
| **Lyra** | 设计者 —— 关注清晰与形式 |
| **Echo** | 操作者 —— 关注运维与生命周期风险 |
| **Hermes** | 调度者（第七位）—— 综合裁定 |

### 2. 模型路由 —— BYO Key（自带 Key）

STOA 从不打包、代理或转发任何人的 API Key。我们没有 STOA Cloud，也没有「我们给你的免费额度」。首次运行向导会引导你以最低成本完成可用配置：

1. 打开 https://platform.deepseek.com/api_keys（免费注册、无需信用卡）。
2. 创建一个 Key。DeepSeek 免费额度覆盖一般个人使用；一次重度会话只要几美分。
3. 粘贴 Key。STOA 把它写入 `~/.stoa/cli-config.yaml`（你本地），不会发送到其他任何地方。

结果：会议厅在你自己的 DeepSeek 免费额度上跑通，花费完全在你掌控之中。想要「一席一供应商」（例如 Sokrates → Anthropic，Veritas → Google，Drax → xAI）？逐席绑定不同的 Key：

```yaml
# ~/.stoa/cli-config.yaml —— 由 `stoa setup` 生成，可任意修改
personas:
  sokrates: { provider: deepseek, model: deepseek-reasoner }
  mira:     { provider: deepseek, model: deepseek-chat }
  # …或者逐席覆盖为不同的 Key + Provider：
  veritas:  { provider: anthropic, model: claude-opus-4-7, api_mode: anthropic }
```

> 角色名（Sokrates / Mira / Veritas / Drax / Lyra / Echo / Hermes）是角色标识，与具体模型供应商解耦 —— 用 `stoa /persona list` 查看你机器上当前的角色 ↔ Provider 绑定。

### 3. Council 审计的 Skill 发布

Agent Skill 生态最难的问题是供应链信任。STOA 的回答是：**任何 Skill 发布之前都必须经过 6 角色审计 + 5/6 法定人数 + 本地审计哈希**。安全、性能、Prompt Injection、许可证、结构、归属 —— 六个不同视角扫描每一个新 Skill。

```sh
stoa skill publish ./my-skill
# → 6 角色独立审计
# → 需要 5/6 法定人数
# → 写入本地审计哈希；链上印章在 --attest 标志后（Preview）
```

### 4. 链上证明 —— Preview 阶段

`stoa --attest` 目前是 **Preview 功能**，需要显式标志启用。

启用后，每次 Council 裁定可以将响应哈希写入 Monad 主网上的 **AuditAttestationV2** 合约。几个月后，任何人都可以验证某个 STOA Agent 确实执行了它所声明的动作。哈希计算 + 本地持久化已就绪；`eth_sendRawTransaction` 提交和验证器客户端正在为下一个版本做加固。在此之前 `--attest` 会计算哈希、入队、并打印 `attestation_preview: pending_submit`。

如果你不需要链上可验证性，可以完全忽略 `--attest` —— 会议厅、裁定、Skill 审计闸门全部在本地工作。

---

## 命令

| 命令 | 作用 |
|---|---|
| `stoa` | 启动面板 + 交互 REPL |
| `stoa chat` | 直接对话 |
| `stoa setup` | 首次运行向导（生成 `~/.stoa/cli-config.yaml`）|
| `stoa gateway` | 运行多平台守护进程（Telegram, Discord, Slack 等）|
| `stoa /council "<任务>"` | 6 角色并行 + 裁定 |
| `stoa /persona <名称>` | 切换单角色模式 |
| `stoa /persona list` | 显示当前角色 ↔ Provider 绑定 |
| `stoa /verdict` | 显示上一次 Council 裁定 |
| `stoa /attest` | **Preview** —— 把上一次裁定盖章上链 |
| `stoa skill publish` | 发布前运行 6 角色审计闸门 |
| `stoa migrate xai` | 配置改写 —— 把退役的 xAI 模型替换为当前可用版本 |

---

## 安全立场

STOA 沿用上游的同一套基础能力：Shell 执行、浏览器自动化、Plugin 市场、可选的钱包绑定。这些是强力工具，需要操作者具备一定素养 —— STOA 面向的操作者画像与 Cursor、Claude Code、Aider 相同。

- **默认关闭的安全闸门**（数据库加密、PII/IP 脱敏、Skill ed25519 签名、强制沙箱）正在通过下个版本的 `STOA_SECURITY_PRESET` 选择器默认开启。
- **漏洞悬赏 + 协同披露**：见 [SECURITY.md](SECURITY.md)。请**不要**在 Public Issue 里报告安全问题。
- **审计报告**不会发布到 Master 树 —— 协同披露优先。我们先 Ship 修复，再发布摘要。

---

## 许可证

STOA Agent 代码本身是 MIT 许可证，见 [LICENSE](LICENSE)。
上游 MIT 许可证原文保留；本 Fork 的归属记录见 [ATTRIBUTION.md](ATTRIBUTION.md)。

**捆绑资产携带各自许可证：**

- `web/public/fonts-terminal/JetBrainsMono-*.woff2` —— SIL Open Font License 1.1，见 [`web/public/fonts-terminal/OFL.txt`](web/public/fonts-terminal/OFL.txt)。
- `optional-skills/productivity/powerpoint/` —— **Anthropic 专有**。仅 Opt-in（设置 `STOA_ENABLE_OPTIONAL_SKILLS=1` 才会被发现）。受你与 Anthropic 的单独协议约束；完整条款见 `optional-skills/productivity/powerpoint/LICENSE.txt`。不在 MIT 覆盖范围内。
- `optional-skills/mlops/inference/obliteratus/` —— AGPL-3.0。Opt-in 方式是设置 `STOA_ENABLE_REDTEAM=1`。如果你以联网服务的形式分发包含此 Skill 的产品，AGPL §13 义务适用。

## 链接

- 文档 · https://stoax.xyz/cli
- 会议厅 · https://stoax.xyz
- PyPI · https://pypi.org/project/stoa-agent/
- 源码 · https://github.com/STOAGENT/stoa-agent
- 上游渊源 · [ATTRIBUTION.md](ATTRIBUTION.md)
