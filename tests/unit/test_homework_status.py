from src.domain.homework.status import is_open_homework_status


def test_submitted_and_review_statuses_are_not_open():
    assert not is_open_homework_status("submitted", "待批阅")
    assert not is_open_homework_status("reviewed", "已批阅")
    assert not is_open_homework_status("已互评", "已互评")
    assert not is_open_homework_status("completed", "已完成")


def test_pending_statuses_are_open():
    assert is_open_homework_status("pending", "未交")
    assert is_open_homework_status("in_progress", "进行中")
    assert is_open_homework_status("expired", "已过期")
