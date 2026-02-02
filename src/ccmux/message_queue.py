"""Message queue management for per-user sequential message delivery.

Core responsibilities:
  - Per-user message queues with worker tasks
  - Message merging to reduce API calls (consecutive content messages)
  - Queue processing with rate limiting and status handling
  - Tool message tracking for edit-in-place updates

Key classes: MessageTask
Key functions: get_or_create_queue(), enqueue_content_message(), enqueue_status_update()
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Literal

from telegram import Bot
from telegram.constants import ChatAction

from .markdown_v2 import convert_markdown
from .message_sender import NO_LINK_PREVIEW, rate_limit_send
from .terminal_parser import parse_status_line
from .tmux_manager import tmux_manager

logger = logging.getLogger(__name__)


@dataclass
class MessageTask:
    """Message task for queue processing."""

    task_type: Literal["content", "status_update", "status_clear"]
    text: str | None = None
    window_name: str | None = None
    # content type fields
    parts: list[str] = field(default_factory=list)
    tool_use_id: str | None = None
    content_type: str = "text"


# Per-user message queues and worker tasks
_message_queues: dict[int, asyncio.Queue[MessageTask]] = {}
_queue_workers: dict[int, asyncio.Task[None]] = {}
_queue_locks: dict[int, asyncio.Lock] = {}  # Protect drain/refill operations

# Map (tool_use_id, user_id) -> telegram message_id for editing tool_use messages with results
_tool_msg_ids: dict[tuple[str, int], int] = {}

# Status message tracking: user_id -> (message_id, window_name, last_text)
# Note: last_text may be missing in old entries during rolling update
_status_msg_info: dict[int, tuple[int, str] | tuple[int, str, str]] = {}

# Merge configuration
MERGE_MAX_LENGTH = 3800  # Leave room for markdown conversion overhead


def get_or_create_queue(bot: Bot, user_id: int) -> asyncio.Queue[MessageTask]:
    """Get or create message queue and worker for a user."""
    if user_id not in _message_queues:
        _message_queues[user_id] = asyncio.Queue()
        _queue_locks[user_id] = asyncio.Lock()
        # Start worker task for this user
        _queue_workers[user_id] = asyncio.create_task(
            _message_queue_worker(bot, user_id)
        )
    return _message_queues[user_id]


def _inspect_queue(queue: asyncio.Queue[MessageTask]) -> list[MessageTask]:
    """Non-destructively inspect all items in queue.

    Drains the queue and returns all items. Caller must refill.
    """
    items: list[MessageTask] = []
    while not queue.empty():
        try:
            item = queue.get_nowait()
            items.append(item)
        except asyncio.QueueEmpty:
            break
    return items


def _can_merge_tasks(base: MessageTask, candidate: MessageTask) -> bool:
    """Check if two content tasks can be merged."""
    if base.window_name != candidate.window_name:
        return False
    if candidate.task_type != "content":
        return False
    # tool_use/tool_result break merge chain
    # - tool_use: will be edited later by tool_result
    # - tool_result: edits previous message, merging would cause order issues
    if base.content_type in ("tool_use", "tool_result"):
        return False
    if candidate.content_type in ("tool_use", "tool_result"):
        return False
    return True


async def _merge_content_tasks(
    queue: asyncio.Queue[MessageTask],
    first: MessageTask,
    lock: asyncio.Lock,
) -> tuple[MessageTask, int]:
    """Merge consecutive content tasks from queue.

    Returns: (merged_task, merge_count) where merge_count is the number of
    additional tasks merged (0 if no merging occurred).
    """
    merged_parts = list(first.parts)
    current_length = sum(len(p) for p in merged_parts)
    merge_count = 0

    async with lock:
        items = _inspect_queue(queue)
        remaining: list[MessageTask] = []

        for i, task in enumerate(items):
            if not _can_merge_tasks(first, task):
                # Can't merge, keep this and all remaining items
                remaining = items[i:]
                break

            # Check length before merging
            task_length = sum(len(p) for p in task.parts)
            if current_length + task_length > MERGE_MAX_LENGTH:
                # Too long, stop merging
                remaining = items[i:]
                break

            merged_parts.extend(task.parts)
            current_length += task_length
            merge_count += 1

        # Put remaining items back into the queue
        for item in remaining:
            queue.put_nowait(item)
            # Compensate: this item was already counted when first enqueued,
            # put_nowait adds a duplicate count that must be removed
            queue.task_done()

    if merge_count == 0:
        return first, 0

    return MessageTask(
        task_type="content",
        window_name=first.window_name,
        parts=merged_parts,
        tool_use_id=first.tool_use_id,
        content_type=first.content_type,
    ), merge_count


async def _message_queue_worker(bot: Bot, user_id: int) -> None:
    """Process message tasks for a user sequentially."""
    queue = _message_queues[user_id]
    lock = _queue_locks[user_id]
    logger.info(f"Message queue worker started for user {user_id}")

    while True:
        try:
            task = await queue.get()
            try:
                if task.task_type == "content":
                    # Try to merge consecutive content tasks
                    merged_task, merge_count = await _merge_content_tasks(
                        queue, task, lock
                    )
                    if merge_count > 0:
                        logger.debug(
                            f"Merged {merge_count} tasks for user {user_id}"
                        )
                        # Mark merged tasks as done
                        for _ in range(merge_count):
                            queue.task_done()
                    await _process_content_task(bot, user_id, merged_task)
                elif task.task_type == "status_update":
                    await _process_status_update_task(bot, user_id, task)
                elif task.task_type == "status_clear":
                    await _do_clear_status_message(bot, user_id)
            except Exception as e:
                logger.error(f"Error processing message task for user {user_id}: {e}")
            finally:
                queue.task_done()
        except asyncio.CancelledError:
            logger.info(f"Message queue worker cancelled for user {user_id}")
            break
        except Exception as e:
            logger.error(f"Unexpected error in queue worker for user {user_id}: {e}")


async def _process_content_task(bot: Bot, user_id: int, task: MessageTask) -> None:
    """Process a content message task."""
    wname = task.window_name or ""

    # 1. Handle tool_result editing (merged parts are edited together)
    if task.content_type == "tool_result" and task.tool_use_id:
        _tkey = (task.tool_use_id, user_id)
        edit_msg_id = _tool_msg_ids.pop(_tkey, None)
        if edit_msg_id is not None:
            # Clear status message first
            await _do_clear_status_message(bot, user_id)
            # Join all parts for editing (merged content goes together)
            full_text = "\n\n".join(task.parts)
            try:
                await bot.edit_message_text(
                    chat_id=user_id,
                    message_id=edit_msg_id,
                    text=full_text,
                    parse_mode="MarkdownV2",
                    link_preview_options=NO_LINK_PREVIEW,
                )
                await _check_and_send_status(bot, user_id, wname)
                return
            except Exception:
                try:
                    # Fallback: strip markdown
                    plain_text = task.text or full_text
                    await bot.edit_message_text(
                        chat_id=user_id,
                        message_id=edit_msg_id,
                        text=plain_text,
                        link_preview_options=NO_LINK_PREVIEW,
                    )
                    await _check_and_send_status(bot, user_id, wname)
                    return
                except Exception:
                    logger.debug(f"Failed to edit tool msg {edit_msg_id}, sending new")
                    # Fall through to send as new message

    # 2. Send content messages, converting status message to first content part
    first_part = True
    last_msg_id: int | None = None
    for part in task.parts:
        sent = None

        # For first part, try to convert status message to content (edit instead of delete)
        if first_part:
            first_part = False
            converted_msg_id = await _convert_status_to_content(bot, user_id, wname, part)
            if converted_msg_id is not None:
                last_msg_id = converted_msg_id
                continue

        await rate_limit_send(user_id)
        try:
            sent = await bot.send_message(
                chat_id=user_id, text=part, parse_mode="MarkdownV2",
                link_preview_options=NO_LINK_PREVIEW,
            )
        except Exception:
            try:
                sent = await bot.send_message(
                    chat_id=user_id, text=part,
                    link_preview_options=NO_LINK_PREVIEW,
                )
            except Exception as e:
                logger.error(f"Failed to send message to {user_id}: {e}")

        if sent:
            last_msg_id = sent.message_id

    # 3. Record tool_use message ID for later editing
    if last_msg_id and task.tool_use_id and task.content_type == "tool_use":
        _tool_msg_ids[(task.tool_use_id, user_id)] = last_msg_id

    # 4. After content, check and send status
    await _check_and_send_status(bot, user_id, wname)


async def _convert_status_to_content(
    bot: Bot, user_id: int, window_name: str, content_text: str
) -> int | None:
    """Convert status message to content message by editing it.

    Returns the message_id if converted successfully, None otherwise.
    """
    info = _status_msg_info.pop(user_id, None)
    if not info:
        return None

    # Handle both old (2-tuple) and new (3-tuple) format
    msg_id = info[0]
    stored_wname = info[1]
    if stored_wname != window_name:
        # Different window, just delete the old status
        try:
            await bot.delete_message(chat_id=user_id, message_id=msg_id)
        except Exception:
            pass
        return None

    # Edit status message to show content
    try:
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=msg_id,
            text=content_text,
            parse_mode="MarkdownV2",
            link_preview_options=NO_LINK_PREVIEW,
        )
        return msg_id
    except Exception:
        try:
            # Fallback to plain text
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=msg_id,
                text=content_text,
                link_preview_options=NO_LINK_PREVIEW,
            )
            return msg_id
        except Exception as e:
            logger.debug(f"Failed to convert status to content: {e}")
            # Message might be deleted or too old, caller will send new message
            return None


async def _process_status_update_task(bot: Bot, user_id: int, task: MessageTask) -> None:
    """Process a status update task."""
    wname = task.window_name or ""
    status_text = task.text or ""

    if not status_text:
        # No status text means clear status
        await _do_clear_status_message(bot, user_id)
        return

    # Send typing indicator if Claude is interruptible (working)
    if "esc to interrupt" in status_text.lower():
        try:
            await bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)
        except Exception:
            pass

    current_info = _status_msg_info.get(user_id)

    if current_info:
        # Handle both old (2-tuple) and new (3-tuple) format for compatibility
        if len(current_info) == 2:
            msg_id, stored_wname = current_info
            last_text = ""
        else:
            msg_id, stored_wname, last_text = current_info

        if stored_wname != wname:
            # Window changed - delete old and send new
            await _do_clear_status_message(bot, user_id)
            await _do_send_status_message(bot, user_id, wname, status_text)
        elif status_text == last_text:
            # Same content, skip edit
            pass
        else:
            # Same window, text changed - edit in place
            try:
                await bot.edit_message_text(
                    chat_id=user_id,
                    message_id=msg_id,
                    text=convert_markdown(status_text),
                    parse_mode="MarkdownV2",
                    link_preview_options=NO_LINK_PREVIEW,
                )
                _status_msg_info[user_id] = (msg_id, wname, status_text)
            except Exception:
                try:
                    await bot.edit_message_text(
                        chat_id=user_id,
                        message_id=msg_id,
                        text=status_text,
                        link_preview_options=NO_LINK_PREVIEW,
                    )
                    _status_msg_info[user_id] = (msg_id, wname, status_text)
                except Exception as e:
                    logger.debug(f"Failed to edit status message: {e}")
                    _status_msg_info.pop(user_id, None)
                    await _do_send_status_message(bot, user_id, wname, status_text)
    else:
        # No existing status message, send new
        await _do_send_status_message(bot, user_id, wname, status_text)


async def _do_send_status_message(
    bot: Bot, user_id: int, window_name: str, text: str
) -> None:
    """Send a new status message and track it (internal, called from worker)."""
    await rate_limit_send(user_id)
    try:
        sent = await bot.send_message(
            chat_id=user_id,
            text=convert_markdown(text),
            parse_mode="MarkdownV2",
            link_preview_options=NO_LINK_PREVIEW,
        )
        _status_msg_info[user_id] = (sent.message_id, window_name, text)
    except Exception:
        try:
            sent = await bot.send_message(
                chat_id=user_id, text=text,
                link_preview_options=NO_LINK_PREVIEW,
            )
            _status_msg_info[user_id] = (sent.message_id, window_name, text)
        except Exception as e:
            logger.error(f"Failed to send status message to {user_id}: {e}")


async def _do_clear_status_message(bot: Bot, user_id: int) -> None:
    """Delete the status message for a user (internal, called from worker)."""
    info = _status_msg_info.pop(user_id, None)
    if info:
        msg_id = info[0]
        try:
            await bot.delete_message(chat_id=user_id, message_id=msg_id)
        except Exception as e:
            logger.debug(f"Failed to delete status message {msg_id}: {e}")


async def _check_and_send_status(bot: Bot, user_id: int, window_name: str) -> None:
    """Check terminal for status line and send status message if present."""
    # Skip if there are more messages pending in the queue
    queue = _message_queues.get(user_id)
    if queue and not queue.empty():
        return
    w = await tmux_manager.find_window_by_name(window_name)
    if not w:
        return

    pane_text = await tmux_manager.capture_pane(w.window_id)
    if not pane_text:
        return

    status_line = parse_status_line(pane_text)
    if status_line:
        await _do_send_status_message(bot, user_id, window_name, status_line)


async def enqueue_content_message(
    bot: Bot,
    user_id: int,
    window_name: str,
    parts: list[str],
    tool_use_id: str | None = None,
    content_type: str = "text",
) -> None:
    """Enqueue a content message for delivery."""
    queue = get_or_create_queue(bot, user_id)
    task = MessageTask(
        task_type="content",
        window_name=window_name,
        parts=parts,
        tool_use_id=tool_use_id,
        content_type=content_type,
    )
    await queue.put(task)


async def enqueue_status_update(
    bot: Bot, user_id: int, window_name: str, status_text: str | None
) -> None:
    """Enqueue a status update task."""
    queue = get_or_create_queue(bot, user_id)
    task = MessageTask(
        task_type="status_update",
        window_name=window_name,
        text=status_text,
    )
    await queue.put(task)


async def enqueue_status_clear(bot: Bot, user_id: int) -> None:
    """Enqueue a status clear task."""
    queue = get_or_create_queue(bot, user_id)
    task = MessageTask(task_type="status_clear")
    await queue.put(task)
