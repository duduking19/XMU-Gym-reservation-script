from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from xdty_booking.config import AppConfig, SchedulerConfig
from xdty_booking.core.scheduler import BookingScheduler

NOT_RELEASED = {'success': False, 'skipped': True, 'reason': 'course_occupied', 'info': '仍锁定'}
MISSING = {'success': False, 'reason': 'slot_missing', 'info': '未找到指定时段场次'}
BOOKED = {'success': True, 'info': '预约成功'}


def run_once(cfg, results):
    scheduler = BookingScheduler(cfg, Mock(), Mock(), Mock(), Mock(), notifier=Mock())
    with patch('xdty_booking.core.scheduler.datetime', wraps=datetime) as clock, \
         patch.object(scheduler, 'ensure_valid_session', return_value=True), \
         patch.object(scheduler._stop_event, 'wait', return_value=False), \
         patch('xdty_booking.core.scheduler.TimeSync.get_server_time_offset', return_value=0), \
         patch('xdty_booking.core.scheduler.TimeSync.wait_until'), \
         patch('xdty_booking.core.scheduler.BookingEngine.execute_booking', side_effect=results) as booking:
        clock.now.return_value = datetime(2026, 9, 22, 6, 59, 40)
        res = scheduler._run_once()
    return res, booking.call_count


def test_keeps_polling_until_slot_is_released():
    res, calls = run_once(AppConfig(), [MISSING, NOT_RELEASED, BOOKED])
    assert res['success'] and calls == 3


def test_still_locked_after_grace_period_is_course_occupied():
    cfg = AppConfig(scheduler=SchedulerConfig(release_grace_seconds=0))
    res, calls = run_once(cfg, [NOT_RELEASED, BOOKED])
    assert res['reason'] == 'course_occupied' and calls == 1

