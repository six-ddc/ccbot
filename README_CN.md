# CCBot

通过 Telegram 远程控制运行在 tmux 里的 Claude Code 或 Codex 会话。

https://github.com/user-attachments/assets/15ffb38e-5eb9-4720-93b9-412e4961dc93

## 它是什么

CCBot 本质上是 tmux 之上的一个 Telegram 控制层。

- Claude Code 或 Codex 进程继续跑在 tmux 窗口里
- 你通过 Telegram 看输出、发消息、下命令
- 随时可以用 `tmux attach` 回到终端

每个 Telegram 话题对应一个 tmux 窗口，以及一个 Claude Code 或 Codex 会话。

## 功能

- 一个话题对应一个会话
- 实时回复和状态更新
- 可以从 Telegram 恢复旧会话
- 支持语音转文字
- 支持交互式按钮
- 支持消息历史和截图
- 同时支持 Claude Code 和 Codex

## 前置要求

- `tmux`
- `claude` 或 `codex`
- 一个通过 [@BotFather](https://t.me/BotFather) 创建的 Telegram Bot

## 安装

### 从 GitHub 安装

```bash
uv tool install git+https://github.com/six-ddc/ccmux.git
```

或者：

```bash
pipx install git+https://github.com/six-ddc/ccmux.git
```

### 从源码安装

```bash
git clone https://github.com/six-ddc/ccmux.git
cd ccmux
uv sync
```

## 基本配置

**1. 创建 Telegram Bot 并启用话题模式：**

1. 与 [@BotFather](https://t.me/BotFather) 对话创建新 Bot 并获取 Token
2. 打开 @BotFather 的个人页面，点击 **Open App** 启动小程序
3. 选择你的 Bot，进入 **Settings** > **Bot Settings**
4. 启用 **Threaded Mode**（话题模式）

**2. 配置环境变量：**

创建 `~/.ccbot/.env`：

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERS=your_telegram_user_id
```

如果你想手动一次配完整，也可以直接写成这样：

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERS=123456789

# 默认运行时：claude 或 codex
CCBOT_RUNTIME=claude

# 新 tmux 窗口里实际启动的命令
CLAUDE_COMMAND=claude
CODEX_COMMAND=codex --no-alt-screen

# 可选
TMUX_SESSION_NAME=ccbot
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
```

常用可选项：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CCBOT_RUNTIME` | `claude` | 新窗口默认启动 `claude` 还是 `codex` |
| `CCBOT_DIR` | `~/.ccbot` | 配置和状态目录 |
| `CLAUDE_COMMAND` | `claude` | Claude Code 命令 |
| `CODEX_COMMAND` | `codex --no-alt-screen` | Codex 命令 |
| `CODEX_HOME` | `~/.codex` | Codex 配置和会话目录 |
| `TMUX_SESSION_NAME` | `ccbot` | tmux 会话名 |
| `MONITOR_POLL_INTERVAL` | `2.0` | 会话监控轮询间隔（秒） |
| `CCBOT_SHOW_HIDDEN_DIRS` | `false` | 目录选择器里是否显示隐藏目录 |
| `OPENAI_API_KEY` | _(空)_ | 只在语音转文字时需要 |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | 可选的 OpenAI 兼容接口地址 |

说明：

- `ALLOWED_USERS` 支持填写一个或多个 Telegram 用户 ID，用逗号分隔。
- `ccbot --run claude|codex` 只影响这一次启动，不会改写 `.env`。
- 如果你是从源码运行，也可以临时放项目内 `.env` 做测试，但长期配置通常还是放在 `~/.ccbot/.env`。

如果你是在没有交互终端的 VPS 上运行，Claude Code 命令可以考虑设置得更少交互一些，例如：

```bash
CLAUDE_COMMAND=IS_SANDBOX=1 claude --dangerously-skip-permissions
```

## 安装 Hook

执行：

```bash
ccbot hook --install
```

如果你想显式把 Claude Code 和 Codex 两边的 hook 都装上：

```bash
ccbot hook --install --run all
```

Codex 在 `v0.114.0` 里仍然把 hooks 当实验特性。如果你是在 CCBot 之外手动启动 Codex，请自己加上：

```bash
codex --enable codex_hooks
```

如果你不想用 `ccbot hook --install`，也可以手动写配置：

Claude Code 的 `~/.claude/settings.json`：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "ccbot hook",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Codex 的 `~/.codex/hooks.json`：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "ccbot hook",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

这样 CCBot 才能持续更新 `session_map.json`，把 tmux 窗口和正确的
Claude Code / Codex 会话关联起来。

## 启动 Bot

```bash
ccbot
```

如果你是从源码运行：

```bash
uv run ccbot
```

如果你只想让这一次进程使用 Codex：

```bash
ccbot --run codex
```

## 日常使用

1. 在 Telegram 群里创建一个新话题
2. 在这个话题里发送任意消息
3. 选择项目目录
4. 选择恢复旧会话或新开会话
5. 之后继续在这个话题里聊天

文字和语音都会转发到绑定的 Claude Code 或 Codex 会话。关闭话题时，
对应的 tmux 窗口也会一起关闭。

## 常用命令

| 命令 | 说明 |
| --- | --- |
| `/start` | 显示欢迎消息 |
| `/history` | 查看当前话题的消息历史 |
| `/screenshot` | 截图当前终端 |
| `/esc` | 发送 Escape 中断当前会话 |
| `/unbind` | 解绑当前话题，但保留 tmux 窗口继续运行 |
| `/kill` | 结束当前话题对应的会话 |

大多数其他 `/...` 命令也会直接转发给当前运行时。

- Claude Code 菜单里会有 `/usage`、`/help`、`/memory`、`/model` 等命令。
- Codex 菜单里会有 `/status`、`/plan` 等命令。
- 菜单里没显示的命令仍然可以手动输入，例如 `/clear`、`/compact`、`/review`、`/model`。

## 手动使用 tmux

如果你想自己先开窗口：

```bash
tmux attach -t ccbot
tmux new-window -n myproject -c ~/Code/myproject
claude
```

如果你用的是 Codex：

```bash
tmux attach -t ccbot
tmux new-window -n myproject -c ~/Code/myproject
codex --no-alt-screen --enable codex_hooks --enable default_mode_request_user_input
```
