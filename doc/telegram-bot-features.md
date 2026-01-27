# Telegram Bot 高级功能调研

## 一、Telegram Bot API 高级功能全景

### 1. 富文本与格式化

| 功能 | 说明 |
|------|------|
| **MarkdownV2 / HTML parse_mode** | 支持粗体、斜体、删除线、代码块、嵌套格式、链接。HTML 支持 `<pre language="python">` 语法高亮 |
| **MessageEntity** | 消息实体：mention、hashtag、URL、code、pre 等 |
| **Spoiler text** | 折叠/隐藏文本 `<tg-spoiler>`，点击才展开 |

### 2. 交互组件

| 功能 | 说明 |
|------|------|
| **InlineKeyboardMarkup** | 消息内嵌按钮 |
| **ReplyKeyboardMarkup** | 底部持久键盘 |
| **callback_query.answer(text, show_alert)** | 按钮点击后弹出 Toast 或模态 Alert |
| **switch_inline_query** | 按钮跳转 inline 查询 |
| **WebApp (Mini App)** | `WebAppInfo` 嵌入 H5 页面 |
| **LoginWidget** | Telegram 一键登录 |

### 3. 多媒体与文件

| 功能 | 说明 |
|------|------|
| **send_photo / send_document / send_animation** | 发送图片、文件、GIF |
| **send_media_group** | 相册模式批量发送 |
| **InputMediaPhoto/Document** | 编辑已发消息的媒体 |

### 4. 会话管理

| 功能 | 说明 |
|------|------|
| **ConversationHandler** | 多步骤状态机对话 |
| **send_chat_action("typing")** | "正在输入..." 状态指示 |
| **pin_chat_message / unpin** | 置顶消息 |
| **copy_message** | 转发但不显示来源 |
| **reply_to_message_id** | 精确回复引用 |

### 5. Inline Mode

| 功能 | 说明 |
|------|------|
| **InlineQueryHandler** | 任意聊天 @bot 触发搜索 |
| **ChosenInlineResultHandler** | 获取用户选择结果 |

### 6. 其他高级能力

| 功能 | 说明 |
|------|------|
| **edit_message_text / edit_message_reply_markup** | 原地编辑消息或按钮 |
| **delete_message** | 删除消息 |
| **BotCommand + set_my_commands** | `/` 命令菜单 |
| **MenuButton** | 自定义左下角菜单按钮 |
| **Message Reactions** | 消息表情反应 (API 7.x+) |
| **Link Preview Options** | 控制链接预览 |
| **Job Queue** | 定时/延迟任务 |

---

## 二、当前 bot.py 改进建议

### 优先级排序（按投入产出比）

1. **send_chat_action("typing")** — 加一行，体验提升明显
2. **HTML 格式化代码块** — Claude 输出大量代码，高亮后可读性飙升
3. **reply_to_message_id** — 消息关联清晰
4. **session 详情加 inline 操作按钮** — 交互效率提升
5. **callback answer 文字反馈** — 反馈感更好
6. **Claude Code 命令快捷键** — /clear, /compact, /cost 等
7. **spoiler 包裹 thinking** — 视觉整洁
8. **Job Queue 主动通知** — session 状态变化感知
9. **ConversationHandler 重构** — 代码质量提升
10. **WebApp / 置顶消息** — 适合后期

---

## 三、Claude Code 无参数命令（适合做 Telegram 快捷指令）

| 命令 | 功能 |
|------|------|
| `/clear` | 清空对话历史 |
| `/compact` | 压缩/总结对话上下文 |
| `/cost` | 显示 token 用量和费用 |
| `/help` | 显示帮助 |
| `/review` | 代码审查 |
| `/status` | 项目状态 |
| `/init` | 初始化项目 CLAUDE.md |
| `/memory` | 编辑 CLAUDE.md |
| `/doctor` | 诊断环境问题 |
| `/login` | 登录 |
| `/logout` | 登出 |

这些命令通过 Telegram bot 的 `/` 菜单暴露，点击后直接将对应文本发送到 tmux 窗口中的 Claude Code 进程。
