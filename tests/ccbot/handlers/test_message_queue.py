"""Tests for message queue helpers."""

import pytest

from ccbot.handlers import message_queue
from ccbot.handlers.message_queue import MessageTask


@pytest.fixture(autouse=True)
def clean_message_queue_state() -> None:
    message_queue._message_queues.clear()
    message_queue._queue_locks.clear()
    message_queue._queue_workers.clear()
    message_queue._flood_until.clear()
    message_queue._status_msg_info.clear()
    message_queue._tool_msg_ids.clear()
    yield
    message_queue._message_queues.clear()
    message_queue._queue_locks.clear()
    message_queue._queue_workers.clear()
    message_queue._flood_until.clear()
    message_queue._status_msg_info.clear()
    message_queue._tool_msg_ids.clear()


def test_message_task_defaults() -> None:
    task = MessageTask(task_type="content")
    assert task.parts == []
    assert task.content_type == "text"
