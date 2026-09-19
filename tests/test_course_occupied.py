from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from xdty_booking.config import AppConfig, SchedulerConfig, NotifyConfig, FeishuConfig
from xdty_booking.core.models import IntervalResponse
from xdty_booking.core.booking_engine import BookingEngine
from xdty_booking.core.scheduler import BookingScheduler
from xdty_booking.notify.notifier import Notifier


def intervals(status='locked', select_type=0, selected=0):
    return IntervalResponse.from_dict({'status': 1, 'info': 'ok', 'data': {
        'venue_id': '14', 'date_list': [], 'time_slot_list': [{
            'date': '2026-09-20', 'time_range': '16:30-18:00',
            'start_time': '16:30', 'end_time': '18:00', 'week': '7', 'week_name': '周日',
            'slots': [{'column_id': '67', 'date': '2026-09-20', 'area_name': '健身房',
                       'interval_id': '123', 'price': 0, 'selected': selected, 'max_count': 95,
                       'status': status, 'select_type': select_type, 'is_lock': 0, 'lock_reason': ''}],
        }],
    }})


def test_numeric_zero_select_type_remains_unbookable():
    slot = intervals('available').time_slot_list[0].slots[0]
    assert slot.select_type == 0
    assert slot.is_locked
    assert not slot.is_available


@pytest.mark.parametrize('by_id,fallback', [(False, False), (False, True), (True, True)])
def test_occupied_slot_skips_without_verification_captcha_order_or_fallback(by_id, fallback):
    api = Mock()
    api.get_intervals.return_value = intervals()
    engine = BookingEngine(api, Mock(), AppConfig())
    with patch.object(api.get_intervals.return_value, 'find_nearest_available_slots') as nearest:
        result = engine.execute_booking(target_date='2026-09-20', preferred_time='16:30-18:00',
                                        interval_id='123' if by_id else None, fallback_nearest=fallback)
    assert result['skipped'] is True
    assert result['reason'] == 'course_occupied'
    api.choose_verify.assert_not_called()
    api.get_captcha.assert_not_called()
    api.add_order.assert_not_called()
    nearest.assert_not_called()


@pytest.mark.parametrize('stage', ['verify', 'order'])
def test_late_course_occupation_response_stops_retrying(stage):
    api = Mock()
    api.get_intervals.return_value = intervals('available', 1)
    api.choose_verify.return_value = {'status': 0 if stage == 'verify' else 1, 'info': '该时段课程占用'}
    api.get_captcha.return_value = b'image'
    api.add_order.return_value = {'status': 0, 'info': '该时段教学排课占用'}
    solver = Mock()
    solver.solve.return_value = 'abcd'
    result = BookingEngine(api, solver, AppConfig()).execute_booking(
        target_date='2026-09-20', preferred_time='16:30-18:00', fallback_nearest=True)
    assert result['reason'] == 'course_occupied'
    assert api.add_order.call_count == (0 if stage == 'verify' else 1)
    assert api.get_captcha.call_count == (0 if stage == 'verify' else 1)


@pytest.mark.parametrize('response,raises', [({'code': 0}, False), ({'code': 19024}, False), ({}, True)])
def test_weekly_skip_notifies_once_and_continues_even_if_notification_fails(response, raises):
    cfg = AppConfig(scheduler=SchedulerConfig(weekly_enabled=True, weekly_plan={'7': '16:30-18:00', '1': '15:00-16:30'}),
                    notify=NotifyConfig(enabled=True, channel='feishu', feishu=FeishuConfig(webhook_url='https://example.invalid/test')))
    api = Mock()
    api.get_intervals.return_value = intervals()
    scheduler = BookingScheduler(cfg, Mock(), Mock(), api, Mock(), notifier=Notifier(cfg.notify))
    seen = []
    original = scheduler._run_once

    def attempt(*args):
        seen.append(scheduler.next_booking)
        if len(seen) == 2:
            scheduler.stop()
            return {'success': False, 'info': 'stopped'}
        return original(*args)

    with patch('xdty_booking.core.scheduler.datetime', wraps=datetime) as clock, \
         patch.object(scheduler, 'ensure_valid_session', return_value=True), \
         patch.object(scheduler, '_run_once', side_effect=attempt), \
         patch('xdty_booking.core.scheduler.TimeSync.get_server_time_offset', return_value=0), \
         patch('xdty_booking.core.scheduler.TimeSync.wait_until'), \
         patch('xdty_booking.notify.notifier.requests.post') as post:
        clock.now.return_value = datetime(2026, 9, 19, 6, 59, 40)
        post.return_value.json.return_value = response
        if raises:
            post.side_effect = TimeoutError('offline')
        scheduler.run()
    assert seen == [(datetime(2026, 9, 19, 7), '2026-09-20', '16:30-18:00'),
                    (datetime(2026, 9, 20, 7), '2026-09-21', '15:00-16:30')]
    assert post.call_count == 1
    text = post.call_args.kwargs['json']['content']['text']
    for value in ['课程占用', '2026-09-20', '16:30-18:00', '翔安校区健身房', '跳过', '下一']:
        assert value in text
    api.add_order.assert_not_called()


def test_full_slot_is_not_misreported_as_course_occupation():
    api = Mock()
    api.get_intervals.return_value = intervals('available', 1, 95)
    result = BookingEngine(api, Mock(), AppConfig()).execute_booking(
        target_date='2026-09-20', preferred_time='16:30-18:00', fallback_nearest=False)
    assert not result.get('skipped')
    assert result.get('reason') != 'course_occupied'


def test_unknown_unavailable_status_is_not_misreported_as_course_occupation():
    api = Mock()
    api.get_intervals.return_value = intervals('unavailable', 1)
    result = BookingEngine(api, Mock(), AppConfig()).execute_booking(
        target_date='2026-09-20', preferred_time='16:30-18:00', fallback_nearest=False)
    assert result.get('reason') != 'course_occupied'
    api.add_order.assert_not_called()
