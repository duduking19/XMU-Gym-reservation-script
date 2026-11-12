from datetime import datetime

from xdty_booking.config import AppConfig, SchedulerConfig, load_config, save_target_and_scheduler_config
from xdty_booking.core.scheduler import next_scheduled_booking, planned_slots


def holiday_config():
    return AppConfig(scheduler=SchedulerConfig(
        weekly_enabled=True,
        weekly_plan={'1': '15:00-16:30', '2': '15:00-16:30', '3': '18:00-19:30',
                     '4': '15:00-16:30', '6': '16:30-18:00', '7': '16:30-18:00'},
        date_overrides={'2026-10-01': ['16:30-18:00'], '2026-10-07': ['16:30-18:00'],
                        **{f'2026-10-{day:02d}': [] for day in range(2, 7)}}))


def test_explicit_day_off_overrides_weekly_plan_and_round_trips(tmp_path):
    cfg = holiday_config()
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    save_target_and_scheduler_config(str(path), scheduler_updates={
        'weekly_enabled': True, 'weekly_plan': cfg.scheduler.weekly_plan,
        'date_overrides': cfg.scheduler.date_overrides})
    restored = load_config(str(path))
    for day in range(2, 7):
        date = f'2026-10-{day:02d}'
        assert restored.scheduler.date_overrides[date] == []
        assert planned_slots(restored, date) == []
    assert planned_slots(restored, '2026-10-01') == ['16:30-18:00']
    assert planned_slots(restored, '2026-10-07') == ['16:30-18:00']
    assert planned_slots(restored, '2026-10-08') == ['15:00-16:30']


def test_scheduler_skips_holiday_and_resumes_on_october_seventh():
    cfg = holiday_config()
    assert next_scheduled_booking(cfg, datetime(2026, 9, 29, 23)) == (
        datetime(2026, 9, 30, 7), '2026-10-01', ['16:30-18:00'])
    assert next_scheduled_booking(cfg, datetime(2026, 9, 30, 7)) == (
        datetime(2026, 10, 6, 7), '2026-10-07', ['16:30-18:00'])
    assert next_scheduled_booking(cfg, datetime(2026, 10, 6, 7)) == (
        datetime(2026, 10, 7, 7), '2026-10-08', ['15:00-16:30'])
