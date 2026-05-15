from openakita.agents.task_queue import Priority, QueuedTask, TaskQueue


def test_parent_done_is_blocked_by_unfinished_child():
    queue = TaskQueue()
    parent = QueuedTask(priority=Priority.NORMAL.value, created_at=1.0, task_id="parent")
    child = QueuedTask(
        priority=Priority.NORMAL.value,
        created_at=2.0,
        task_id="child",
        parent_task_id="parent",
    )
    queue._task_index = {"parent": parent, "child": child}

    assert not queue.mark_done("parent")
    assert parent.blocked_by == ["child"]


def test_parent_done_allowed_after_child_terminal():
    queue = TaskQueue()
    parent = QueuedTask(priority=Priority.NORMAL.value, created_at=1.0, task_id="parent")
    child = QueuedTask(
        priority=Priority.NORMAL.value,
        created_at=2.0,
        task_id="child",
        parent_task_id="parent",
        status="done",
    )
    queue._task_index = {"parent": parent, "child": child}

    assert queue.mark_done("parent")
    assert parent.status == "done"
