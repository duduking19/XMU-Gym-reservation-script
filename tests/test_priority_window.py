"""用虚拟时钟验证开抢与优先级降级边界，不发送真实预约请求。"""
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from xdty_booking.config import AppConfig, SchedulerConfig
from xdty_booking.core.scheduler import BookingScheduler

FIRST, SECOND, THIRD = '16:30-18:00', '15:00-16:30', '18:00-19:30'
MISSING = {'success': False, 'reason': 'slot_missing', 'info': '尚未放票'}
FULL = {'success': False, 'full': True, 'info': '已满'}
SUCCESS = {'success': True, 'info': '预约成功'}


def simulate(reply, *, start_delay=0, cancel_at=None, grace=600):
    target = datetime(2026, 9, 26, 7)
    epoch = target.timestamp()
    now = [epoch - 20]
    cfg = AppConfig(scheduler=SchedulerConfig(weekly_enabled=True, advance_ms=200,
                    weekly_plan={'7': [FIRST, SECOND, THIRD]}, release_grace_seconds=grace))
    scheduler = BookingScheduler(cfg, Mock(), Mock(), Mock(), Mock(), notifier=Mock())
    scheduler.next_booking = (target, '2026-09-27', [FIRST, SECOND, THIRD])
    attempts = []

    def wait_until(timestamp, offset=0, advance_ms=200):
        now[0] = timestamp - offset - advance_ms / 1000 + start_delay

    def wait(seconds):
        now[0] += seconds
        if cancel_at is not None and now[0] - epoch >= cancel_at:
            scheduler._stop_event.set()
            return True
        return False

    def book(**kwargs):
        elapsed = now[0] - epoch
        slot = kwargs['preferred_time']
        attempts.append((elapsed, slot))
        result, duration = reply(elapsed, slot)
        now[0] += duration
        return result

    with patch('xdty_booking.core.scheduler.datetime', wraps=datetime) as dt, \
         patch('xdty_booking.core.scheduler.time.time', side_effect=lambda: now[0]), \
         patch('xdty_booking.core.scheduler.TimeSync.get_server_time_offset', return_value=.5), \
         patch('xdty_booking.core.scheduler.TimeSync.wait_until', side_effect=wait_until), \
         patch.object(scheduler._stop_event, 'wait', side_effect=wait), \
         patch.object(scheduler, 'ensure_valid_session', return_value=True), \
         patch('xdty_booking.core.scheduler.BookingEngine.execute_booking', side_effect=book):
        dt.now.side_effect = lambda: datetime.fromtimestamp(now[0])
        result = scheduler._run_once()
    return result, attempts


def test_first_request_never_precedes_seven_even_with_legacy_advance_and_clock_offset():
    result, attempts = simulate(lambda t, slot: (SUCCESS, 0))
    assert result['success'] and attempts == [(0, FIRST)]


@pytest.mark.parametrize('reason', ['slot_missing', 'course_occupied', 'query_failed'])
def test_transient_failures_only_retry_first_priority_in_first_ten_seconds(reason):
    failure = dict(MISSING, reason=reason)
    result, attempts = simulate(lambda t, slot: (SUCCESS if slot == SECOND else failure, 0))
    assert result['success']
    assert attempts[0] == (0, FIRST)
    assert all(slot == FIRST for t, slot in attempts if t < 10)
    assert [t for t, slot in attempts if slot == SECOND] == [10]
    assert len([t for t, slot in attempts if slot == FIRST and t < 10]) > 1


def test_first_priority_released_inside_window_wins():
    result, attempts = simulate(lambda t, slot: (SUCCESS if t >= 6 else MISSING, 0))
    assert result['success'] and attempts[-1] == (6, FIRST)
    assert all(slot == FIRST for _, slot in attempts)


def test_full_first_priority_cannot_fall_back_before_ten_seconds():
    result, attempts = simulate(lambda t, slot: (FULL if slot == FIRST else SUCCESS, 0), grace=0)
    assert result['success'] and attempts == [(0, FIRST), (10, SECOND)]


def test_third_priority_follows_second_only_after_window():
    result, attempts = simulate(lambda t, slot: (SUCCESS if slot == THIRD else FULL, 0))
    assert result['success'] and attempts == [(0, FIRST), (10, SECOND), (10, THIRD)]


def test_delayed_start_uses_absolute_seven_oclock_window():
    result, attempts = simulate(lambda t, slot: (FULL if slot == FIRST else SUCCESS, 0), start_delay=8)
    assert result['success'] and attempts == [(8, FIRST), (10, SECOND)]


def test_slow_first_request_does_not_restart_protection_window():
    result, attempts = simulate(lambda t, slot: (FULL, 12) if slot == FIRST else (SUCCESS, 0))
    assert result['success'] and attempts == [(0, FIRST), (12, SECOND)]


def test_manual_stop_during_window_prevents_fallback():
    result, attempts = simulate(lambda t, slot: (MISSING, 0), cancel_at=4)
    assert not result['success'] and all(slot == FIRST for _, slot in attempts)


def test_uncertain_order_outcome_never_falls_back():
    result, attempts = simulate(lambda t, slot: ({'success': False, 'reason': 'outcome_unknown'}, 0))
    assert result['reason'] == 'outcome_unknown' and len(attempts) == 1
