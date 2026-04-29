# ClaudeCount

[English](./README.md) | **简体中文**

[![Version](https://img.shields.io/badge/version-1.1.0-blue.svg)](#更新日志)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

为 Claude Code（终端版）提供实时 token 用量与花费统计，直接显示在状态栏里。

```
MYPROJECT Sonnet 4.6 200k 🌡️ 22% 🎯 87% │ Turn: $0.03 (↑180k ↓450 179k «) │ Sess: $0.18 (↑180k ↓450 179k «, 5 turns, 12 min) │ Proj: $2.40 (↑18M ↓62k 17M «, 5 sess, 40 turns, 3hr)
```

| 字段 | 含义 |
|------|------|
| **PROJECT NAME** | 当前活动项目（取目录名，转大写） |
| **Model** | 当前模型名称与上下文窗口大小 |
| **🌡️ %** | 上下文窗口占用率 —— 绿 < 50%，蓝 50–74%，黄 75–89%，红 ≥ 90% |
| **🎯 %** | 本次会话缓存命中率（`cache_read` / 总输入）—— 橙 < 50%，黄 50–74%，蓝 75–89%，绿 ≥ 90%；会话尚无输入时不显示 |
| **Turn** | 上一轮已完成对话的花费 + token（所有 API 调用累加） |
| **Sess** | 本次会话累计的花费、token、轮数、活跃时长 |
| **Proj** | 本项目累计的花费、token、会话数、总轮数、活跃时长 |

### Token 标记说明

| 符号 | 含义 |
|------|------|
| `↑` | 输入 token 总量（非缓存 + cache_creation + cache_read） |
| `↓` | 生成的输出 token |
| `«` | `↑` 中由缓存提供的部分（按输入价的 0.1× 计费） |

## 安装

```bash
curl -fsSL https://raw.githubusercontent.com/ayooleo/ClaudeCount/main/install.sh | bash
```

安装后请重启 Claude Code。

**依赖**：Python 3、curl、支持状态栏的 Claude Code 版本。

## 卸载

```bash
curl -fsSL https://raw.githubusercontent.com/ayooleo/ClaudeCount/main/uninstall.sh | bash
```

`~/.claude/token_usage/` 下的历史数据会保留。需要彻底清除请手动删除该目录。

## 详细报告

```bash
# 当前项目（含 session 明细）
python3 ~/.claude/hooks/token_report.py

# 所有项目，按花费降序
python3 ~/.claude/hooks/token_report.py --all

# 所有项目 + session 级明细
python3 ~/.claude/hooks/token_report.py --all -v
```

## 接管旧项目

如果你在已经有 Claude Code 历史的项目上才安装 ClaudeCount，可以一键导入过去的 session —— Claude Code 会把每一份对话 transcript 落到 `~/.claude/projects/<encoded-cwd>/*.jsonl`，从这些文件里能完整重建花费、token、轮数、活跃时长：

```bash
# 在项目根目录执行
python3 ~/.claude/hooks/token_tracker.py --import

# 或指定任意项目路径
python3 ~/.claude/hooks/token_tracker.py --import /path/to/project
```

幂等 —— 已经记录过的 session 会跳过，可以安全重跑。导入进来的 session 在 `~/.claude/token_usage/projects/{pid}.json` 里会带 `"imported": true` 标记。

## 父项目与子项目

每个工作目录就是一个独立项目 —— 在子目录里打开 Claude Code 会有意创建一个独立的统计单元，**绝不会自动归并**。如果你希望某个子目录归并到父项目（比如 `myrepo/web` 和 `myrepo/server` 都汇总到 `myrepo` 的总账下），主动用 `--set-parent` 关联：

```bash
# 在子项目目录执行
python3 ~/.claude/hooks/token_tracker.py --set-parent /path/to/parent

# 或显式指定两端
python3 ~/.claude/hooks/token_tracker.py --set-parent /path/to/parent /path/to/child

# 解除关联（子项目恢复为独立项目，历史保留）
python3 ~/.claude/hooks/token_tracker.py --unset-parent

# 把子项目历史合并到父项目并删除子项目记录。破坏性操作 ——
# 默认仅打印预览，加 --yes 才真正执行。
python3 ~/.claude/hooks/token_tracker.py --merge-into-parent /path/to/child
python3 ~/.claude/hooks/token_tracker.py --merge-into-parent /path/to/child --yes
```

仅支持一层 —— 父项目本身不能再有父项目。关联建立后：

- **在父项目目录打开 Claude Code 时**，状态栏变为多行：第一行是父项目完整统计（🌡️ 上下文占用、🎯 缓存命中、Turn、Sess、Proj 家庭合计）；每个有花费的子项目在下方各占一行（`  › 名称  Sub: $cost (tokens, sessions, turns, time)`），按花费降序排列。花费为 $0.00 的子项目不显示。**在子项目目录打开时**，状态栏仍为单行，显示子项目自己的完整信息
- 子项目的状态栏 header 显示 `parent › CHILD`，第三段从 `Proj:` 改为 `Sub:`（含义：本子项目自身用量）
- 父项目的状态栏 `Proj:` 段升级为**家庭合计**——cost、tokens、会话数、轮数、活跃时长全部跨父 + 全部子项目累加。自动更新：子项目每次 Stop hook 触发后会顺手刷新父项目状态，父状态栏始终反映最新家庭合计，不必等父项目自己的 Stop
- `Sess:` 和 `Turn:` 永远不做家庭聚合 —— 同一时刻你只能在一个会话里
- 父项目的 `token_report` 块底部多一段 `Sub-projects:`（花费为 $0.00 的子项目自动隐藏，避免噪声），以及 `Project total:`（父 + 所有子项目合计）
- 子项目自己的 `token_report` 块顶部显示 `Parent:` 行，`Total cost` 仍为子项目自身花费
- 子项目仍然作为独立条目出现在全局列表里 —— 数据完全可见，只是额外有归并视图

## 自定义价格

在项目根目录创建 `.claude/claudecount.json` 即可覆盖默认价格：

```json
{
  "pricing": {
    "input": 3.00,
    "output": 15.00,
    "cache_write_5m": 3.75,
    "cache_write_1h": 6.00,
    "cache_read": 0.30
  }
}
```

如需对某个项目完全关闭统计：

```json
{ "enabled": false }
```

也可以放一份全局配置在 `~/.claude/token_usage/config.json`。

## 工作原理

三个 hook 协同保证显示准确：

**SessionStart hook**（同步）在每次 session 打开的瞬间触发：
- 在第一条 prompt 之前重置 Turn / Sess / 上下文窗口显示，避免状态栏残留上次会话的旧数据
- 通过比对 session ID 区分新会话与恢复会话 —— 恢复的会话不动数据

**UserPromptSubmit hook**（同步）在你发送消息时触发：
- 作为 SessionStart 没触发时（如 Claude Code 版本较旧）的兜底重置

**Stop hook**（异步）在 Claude 每次回复结束后触发：
- 读取 session transcript，并对 API 调用去重（一次响应可能跨多个 transcript 条目）
- 统计真实的人类轮数（tool_result 类型的消息会排除）
- 计算 Turn / Sess / Proj 的花费和 token 累计
- 写入 `~/.claude/token_usage/status/current.json`

**状态栏**通过 `token_status.sh` 在事件触发时刷新（每条 assistant 消息完成、permission 模式切换、vim 模式切换；可选 `refreshInterval` 周期刷新，去抖 300ms）：
- 从 stdin 接收 Claude Code 实时传入的 model ID 与 `context_window.current_usage`
- `/model` 切换模型时显示立即更新
- 上下文窗口**大小**取自 Claude Code 实时的 `context_window_size`（这样 1M 变体的 opt-in 也能正确识别）；内置模型表只是 fallback
- 上下文窗口**百分比**在本地从 `current_usage` 用 Claude Code 文档里的纯输入公式重新计算（`input + cache_creation + cache_read`，不含输出）

**活跃时长**统计的是人机协作的总时间：累加双方所有间隔 ≤ 3 分钟的消息间隔。超过 3 分钟的间隔（空闲、离开屏幕、未响应的权限弹窗等）会被排除。

## 价格参考（默认）

缓存写入分两档：**5 分钟**（输入价的 1.25×）和 **1 小时**（输入价的 2×）。

| 模型 | Input | Output | Cache write 5m | Cache write 1h | Cache read |
|------|-------|--------|----------------|----------------|------------|
| Opus 4.7 / 4.6 / 4.5 | $5.00 | $25.00 | $6.25 | $10.00 | $0.50 |
| Opus 4.1 / 4 | $15.00 | $75.00 | $18.75 | $30.00 | $1.50 |
| Sonnet 4.6 / 4.5 / 4 | $3.00 | $15.00 | $3.75 | $6.00 | $0.30 |
| Haiku 4.5 | $1.00 | $5.00 | $1.25 | $2.00 | $0.10 |
| Sonnet 3.7 / 3.5 | $3.00 | $15.00 | $3.75 | $6.00 | $0.30 |
| Haiku 3.5 | $0.80 | $4.00 | $1.00 | $1.60 | $0.08 |
| Opus 3 | $15.00 | $75.00 | $18.75 | $30.00 | $1.50 |
| Haiku 3 | $0.25 | $1.25 | $0.30 | $0.50 | $0.03 |

价格按每百万 token 计。**Opus 4.7 / 4.6** 和 **Sonnet 4.6** 支持 1M token 上下文窗口（标准价格），其他模型默认 200K。计费请以 [Claude Console 用量页面](https://platform.claude.com/usage) 为准。

## 数据存储位置

```
~/.claude/token_usage/
├── projects/   # 每个项目的历史数据（每个项目一个 JSON 文件）
└── status/     # 实时状态快照（current.json 由状态栏读取）
```

## 更新日志

本项目遵循 [Semantic Versioning 2.0](https://semver.org/) 与 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 格式。

### [1.1.0] — 2026-04-29

**新增**
- `🎯` 状态栏会话级缓存命中率指示器（`cache_read / 总输入`），配色按行业惯例（绿 ≥90% / 蓝 ≥75% / 黄 ≥50% / 橙 <50%）
- `🌡️` 上下文窗口压力指示器（替换之前的符号；图标与数字之间加空格）
- `--import` 子命令：通过解析磁盘上 Claude Code 的 transcript，把 ClaudeCount 安装之前的历史 session 一次性导入
- `--set-parent` / `--unset-parent`：父项目与子项目主动关联机制。子项目状态栏 header 渲染为 `parent › CHILD`，第三段标签改为 `Sub:`（自身用量）。父项目的 `Proj:` 段变为**家庭合计**（父 + 全部子项目的 cost/tokens/sess/turn/active 累加），任意子项目 Stop hook 触发后自动刷新。`token_report` 列出每个非零花费子项目以及 `Project total:` 合计
- `--merge-into-parent [child] [--yes]`：把子项目的所有 session 合并到父项目并删除子项目记录。破坏性操作——默认仅预览；合并的 session 带 `merged_from: <子项目名>` 字段用于审计。若子项目带有 `legacy` 块则拒绝执行（需手动合并 legacy 后再重试）
- `token_status.sh` 改为按项目路由：多个 Claude Code 实例并行不再因共享 `current.json` 而互相覆盖
- `--version` / `-V` 标记

**变更**
- 缓存命中率粒度选用 session 级（per-turn 抖动太大；per-project 收敛后失去信号）
- 状态栏 header 排版：图标与数值之间加空格（`🌡️ 35%` 而非 `🌡️35%`）
- 父子项目分隔符使用 U+203A 面包屑字符（`›`），不再用 `/`

**修复**
- `current.json` 跨项目污染问题：之前两个 Claude Code 实例并行会显示彼此的 session 数据
- 缓存创建定价正确处理 `ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` 双档分摊（已用 289 条真实 usage 记录核验）
- `token_report.py` 无参时现在只显示当前目录所属项目（与文档描述一致），用 `--all` 才显示全部。之前无参的代码路径会静默走到 `--all`，把当前项目的 `Sub-projects:` 信息埋在花费更高的兄弟项目下面看不到
- 父项目目录下的状态栏升级为多行显示（父行 + 每个有花费的子项目紧凑行）；`token_status.sh` pid 路由改为优先使用 `cwd`（session 启动目录）而非 `workspace.current_dir`，防止项目识别漂移

### [1.0.0] — 2026-04-26

首次发布。

## 许可证

MIT —— 详见 [LICENSE](LICENSE)。
