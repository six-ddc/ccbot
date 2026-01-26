"""Telegram bot handlers for Claude Code session monitoring."""

import logging

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import config
from .session import ClaudeSession, session_manager
from .session_monitor import NewMessage, SessionMonitor
from .telegram_sender import split_message
from .tmux_manager import tmux_manager

logger = logging.getLogger(__name__)

# Session monitor instance
session_monitor: SessionMonitor | None = None

# Callback data prefixes for inline keyboard
CB_SUBSCRIBE = "sub:"
CB_UNSUBSCRIBE = "unsub:"
CB_SELECT = "sel:"
CB_REFRESH = "refresh"
CB_INLINE_PAGE = "ipage:"

# Reply keyboard buttons
BTN_NEW = "➕ New"
BTN_PREV = "⬅️"
BTN_NEXT = "➡️"

# Sessions per page in bottom menu
MENU_SESSIONS_PER_PAGE = 3

# User state keys
STATE_KEY = "state"
STATE_WAITING_DIRECTORY = "waiting_directory"
PAGE_KEY = "menu_page"


def is_user_allowed(user_id: int | None) -> bool:
    """Check if user is allowed."""
    return user_id is not None and config.is_user_allowed(user_id)


def build_reply_keyboard(user_id: int, page: int = 0) -> ReplyKeyboardMarkup:
    """Build persistent bottom menu with session buttons.

    Layout:
    - Row 1-3: Session buttons (one per row)
    - Row 4: Navigation (if needed) + New button
    """
    sessions = session_manager.list_active_sessions()
    total_pages = max(1, (len(sessions) + MENU_SESSIONS_PER_PAGE - 1) // MENU_SESSIONS_PER_PAGE)

    # Ensure page is valid
    page = max(0, min(page, total_pages - 1))

    # Get sessions for current page
    start = page * MENU_SESSIONS_PER_PAGE
    end = start + MENU_SESSIONS_PER_PAGE
    page_sessions = sessions[start:end]

    # Get active session
    active = session_manager.get_active_session(user_id)
    active_id = active.session_id if active else None

    keyboard = []

    # Row 1-3: Session buttons
    for session in page_sessions:
        is_active = session.session_id == active_id
        is_subscribed = session_manager.is_subscribed(user_id, session.session_id)

        # Build label with icons
        icons = []
        if is_active:
            icons.append("📤")
        if is_subscribed:
            icons.append("🔔")

        icon_str = "".join(icons) + " " if icons else ""
        label = f"{icon_str}[{session.project_name}] {session.short_summary}"

        # Truncate if too long
        if len(label) > 40:
            label = label[:37] + "..."

        keyboard.append([KeyboardButton(label)])

    # Row 4: Navigation + New
    nav_row = []
    if total_pages > 1:
        if page > 0:
            nav_row.append(KeyboardButton(BTN_PREV))
        else:
            nav_row.append(KeyboardButton(" "))  # Placeholder
        nav_row.append(KeyboardButton(f"{page + 1}/{total_pages}"))
        if page < total_pages - 1:
            nav_row.append(KeyboardButton(BTN_NEXT))
        else:
            nav_row.append(KeyboardButton(" "))  # Placeholder

    nav_row.append(KeyboardButton(BTN_NEW))
    keyboard.append(nav_row)

    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


def build_session_detail_keyboard(session_id: str, user_id: int) -> InlineKeyboardMarkup:
    """Build inline keyboard for session details."""
    is_subscribed = session_manager.is_subscribed(user_id, session_id)

    buttons = []
    if is_subscribed:
        buttons.append(InlineKeyboardButton(
            "🔕 Unsubscribe",
            callback_data=f"{CB_UNSUBSCRIBE}{session_id}"
        ))
    else:
        buttons.append(InlineKeyboardButton(
            "🔔 Subscribe",
            callback_data=f"{CB_SUBSCRIBE}{session_id}"
        ))

    return InlineKeyboardMarkup([
        buttons,
        [InlineKeyboardButton("🔄 Refresh", callback_data=CB_REFRESH)],
    ])


def get_user_page(context: ContextTypes.DEFAULT_TYPE) -> int:
    """Get current menu page for user."""
    if context.user_data:
        return context.user_data.get(PAGE_KEY, 0)
    return 0


def set_user_page(context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    """Set current menu page for user."""
    if context.user_data is not None:
        context.user_data[PAGE_KEY] = page


async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Send or update the main menu."""
    page = get_user_page(context)
    sessions = session_manager.list_active_sessions()
    subscribed = session_manager.get_subscribed_sessions(user_id)
    active = session_manager.get_active_session(user_id)

    lines = [
        "🤖 *Claude Code Monitor*\n",
        f"📊 {len(sessions)} sessions in tmux",
        f"🔔 {len(subscribed)} subscribed",
    ]

    if active:
        lines.append(f"📤 Active: [{active.project_name}]")
    else:
        lines.append("📤 No active session")

    lines.extend([
        "",
        "Tap a session to select it.",
        "Send text to forward to active session.",
    ])

    if update.message:
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=build_reply_keyboard(user_id, page),
            parse_mode="Markdown",
        )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await update.message.reply_text("You are not authorized to use this bot.")
        return

    # Clear any pending state
    if context.user_data:
        context.user_data.pop(STATE_KEY, None)

    set_user_page(context, 0)
    await send_main_menu(update, context, user.id)


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /list command - show subscribed sessions."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await update.message.reply_text("You are not authorized to use this bot.")
        return

    subscribed = session_manager.get_subscribed_sessions(user.id)

    if not subscribed:
        text = "You are not subscribed to any sessions.\nTap a session and subscribe to receive notifications."
    else:
        lines = ["🔔 *Subscribed Sessions*\n"]
        for session in subscribed:
            has_terminal = session_manager.has_active_terminal(session)
            status = "🟢" if has_terminal else "⚪"
            lines.append(f"• {status} [{session.project_name}] {session.short_summary}")
        lines.append("\n🟢 = in tmux, ⚪ = no terminal")
        text = "\n".join(lines)

    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await update.message.reply_text("You are not authorized to use this bot.")
        return

    if not update.message or not update.message.text:
        return

    text = update.message.text
    page = get_user_page(context)

    # Handle waiting for directory input
    if context.user_data and context.user_data.get(STATE_KEY) == STATE_WAITING_DIRECTORY:
        context.user_data.pop(STATE_KEY, None)

        if text.startswith("/"):
            await update.message.reply_text(
                "Cancelled.",
                reply_markup=build_reply_keyboard(user.id, page),
            )
            return

        # Create new window
        success, message = tmux_manager.create_window(text)
        await update.message.reply_text(
            f"{'✅' if success else '❌'} {message}",
            reply_markup=build_reply_keyboard(user.id, page),
        )
        return

    # Handle navigation buttons
    if text == BTN_PREV:
        new_page = max(0, page - 1)
        set_user_page(context, new_page)
        await send_main_menu(update, context, user.id)
        return

    if text == BTN_NEXT:
        sessions = session_manager.list_active_sessions()
        total_pages = max(1, (len(sessions) + MENU_SESSIONS_PER_PAGE - 1) // MENU_SESSIONS_PER_PAGE)
        new_page = min(total_pages - 1, page + 1)
        set_user_page(context, new_page)
        await send_main_menu(update, context, user.id)
        return

    # Handle page indicator (do nothing)
    if "/" in text and text.replace("/", "").replace(" ", "").isdigit():
        return

    # Handle placeholder
    if text.strip() == "":
        return

    # Handle New button
    if text == BTN_NEW:
        if context.user_data is not None:
            context.user_data[STATE_KEY] = STATE_WAITING_DIRECTORY
        await update.message.reply_text(
            "Enter the directory path for the new Claude Code session:\n"
            "(e.g., ~/Code/my-project)\n\n"
            "Send /cancel to abort."
        )
        return

    # Check if text matches a session button
    sessions = session_manager.list_active_sessions()
    for session in sessions:
        # Match by project name in the button text
        if f"[{session.project_name}]" in text:
            # Select this session
            session_manager.set_active_session(user.id, session.session_id)
            is_subscribed = session_manager.is_subscribed(user.id, session.session_id)

            sub_status = "🔔 Subscribed" if is_subscribed else "🔕 Not subscribed"

            detail_text = (
                f"📤 *Selected: {session.project_name}*\n\n"
                f"📝 {session.summary}\n"
                f"💬 {session.message_count} messages\n\n"
                f"{sub_status}\n\n"
                f"Send text to forward to Claude."
            )

            await update.message.reply_text(
                detail_text,
                reply_markup=build_reply_keyboard(user.id, page),
                parse_mode="Markdown",
            )
            await update.message.reply_text(
                "Actions:",
                reply_markup=build_session_detail_keyboard(session.session_id, user.id),
            )
            return

    # Otherwise, try to send to active session
    success, message = session_manager.send_to_active_session(user.id, text)

    if success:
        active = session_manager.get_active_session(user.id)
        if active:
            await update.message.reply_text(f"📤 Sent to [{active.project_name}]")
    else:
        active = session_manager.get_active_session(user.id)
        if not active:
            await update.message.reply_text(
                "❌ No active session selected.\n"
                "Tap a session button to select it first."
            )
        else:
            await update.message.reply_text(
                f"❌ {message}\n"
                f"Session [{active.project_name}] may have no active terminal."
            )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard callbacks."""
    query = update.callback_query
    if not query or not query.data:
        return

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        await query.answer("Not authorized")
        return

    data = query.data

    if data.startswith(CB_SUBSCRIBE):
        session_id = data[len(CB_SUBSCRIBE):]
        session = session_manager.get_session(session_id)

        if session:
            session_manager.subscribe(user.id, session_id)
            await query.answer(f"Subscribed to {session.short_summary}")
            await query.edit_message_reply_markup(
                reply_markup=build_session_detail_keyboard(session_id, user.id)
            )
        else:
            await query.answer("Session not found")

    elif data.startswith(CB_UNSUBSCRIBE):
        session_id = data[len(CB_UNSUBSCRIBE):]
        session = session_manager.get_session(session_id)

        if session:
            session_manager.unsubscribe(user.id, session_id)
            await query.answer(f"Unsubscribed from {session.short_summary}")
            await query.edit_message_reply_markup(
                reply_markup=build_session_detail_keyboard(session_id, user.id)
            )
        else:
            await query.answer("Session not found")

    elif data == CB_REFRESH:
        await query.answer("Refreshed")
        await query.delete_message()


async def send_notification(bot: Bot, user_id: int, session: ClaudeSession, text: str) -> None:
    """Send a notification about Claude response to a user."""
    max_length = 2000
    if len(text) > max_length:
        text = text[:max_length] + "\n\n[... truncated]"

    header = f"🤖 [{session.project_name}] {session.short_summary}"
    full_message = f"{header}\n\n{text}"

    chunks = split_message(full_message)
    for chunk in chunks:
        try:
            await bot.send_message(chat_id=user_id, text=chunk)
        except Exception as e:
            logger.error(f"Failed to send notification to {user_id}: {e}")


async def handle_new_message(msg: NewMessage, bot: Bot) -> None:
    """Handle a new assistant message from the monitor."""
    subscribers = session_manager.get_subscribers(msg.session_id)

    if not subscribers:
        logger.debug(f"No subscribers for session {msg.session_id}")
        return

    session = session_manager.get_session(msg.session_id)
    if not session:
        logger.warning(f"Session not found: {msg.session_id}")
        return

    for user_id in subscribers:
        logger.info(f"Notifying user {user_id} for session {session.short_summary}")
        await send_notification(bot, user_id, session, msg.text)


async def post_init(application: Application) -> None:
    """Initialize bot and start session monitor."""
    global session_monitor

    await application.bot.delete_my_commands()

    from telegram import BotCommand
    await application.bot.set_my_commands([
        BotCommand("start", "Show session menu"),
        BotCommand("list", "Show subscribed sessions"),
        BotCommand("cancel", "Cancel current operation"),
    ])

    monitor = SessionMonitor()

    async def message_callback(msg: NewMessage) -> None:
        await handle_new_message(msg, application.bot)

    monitor.set_message_callback(message_callback)
    monitor.start()
    session_monitor = monitor

    logger.info("Session monitor started")


async def post_shutdown(application: Application) -> None:
    """Clean up resources on shutdown."""
    global session_monitor

    if session_monitor:
        session_monitor.stop()
        logger.info("Session monitor stopped")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cancel command."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return

    if context.user_data:
        context.user_data.pop(STATE_KEY, None)

    page = get_user_page(context)
    if update.message:
        await update.message.reply_text(
            "Cancelled.",
            reply_markup=build_reply_keyboard(user.id, page),
        )


def create_bot() -> Application:
    """Create and configure the Telegram bot application."""
    application = (
        Application.builder()
        .token(config.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    return application
