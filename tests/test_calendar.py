"""日期整理的持久化、通知及应用重试行为；无需桌面会话。"""
import copy
import datetime as dt
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app
import store
from ui_calendar import CalendarWindow


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = os.path.join(self.folder.name, 'todo.json')
        self.store = store.Store(self.path)

    def seed(self, tasks=(), events=(), last_rollover=None):
        with open(self.path, 'w', encoding='utf-8') as handle:
            json.dump({'tasks': list(tasks), 'events': list(events),
                       'settings': {'last_rollover': last_rollover}}, handle)
        self.store = store.Store(self.path)

    @staticmethod
    def task(name, kind='temp', done_on=None, done=False, pinned=False):
        return {'id': name, 'text': name, 'kind': kind, 'done': done,
                'done_on': done_on, 'pinned': pinned, 'created': 1.0}

    @staticmethod
    def event(name, date, kind='temp', pulled=False):
        return {'id': name, 'text': name, 'date': date, 'kind': kind, 'pulled': pulled}

    def instance(self):
        instance = app.App.__new__(app.App)
        instance.store = self.store
        instance.root = Mock()
        instance.float = Mock()
        instance.tray = None
        instance.last_status = {}
        instance.snooze_until = 0
        instance._permission_prompted = False
        instance._permission_settings_error = False
        return instance

    def test_complete_transition_is_saved_before_one_notification(self):
        self.seed(tasks=[self.task('每日', 'daily', '2026-11-12', True, True),
                         self.task('完成临时', done_on='2026-11-14', done=True),
                         self.task('未完成'),
                         self.task('今天完成', done_on='2026-11-15', done=True)],
                  events=[self.event('过期', '2026-11-13'),
                          self.event('今天', '2026-11-15', 'daily'),
                          self.event('未来', '2026-11-16'),
                          self.event('已转入', '2026-11-14', pulled=True)])
        observations = []
        self.store.add_listener(lambda reason: observations.append(
            (reason, copy.deepcopy(self.store.tasks), store.Store(self.path).tasks)))
        with patch('store.os.replace', wraps=os.replace) as replace:
            result = self.store.sync_calendar('2026-11-15')
        self.assertEqual(result, {'date': '2026-11-15', 'reset': 1, 'removed': 1, 'added': 2})
        replace.assert_called_once()
        self.assertEqual(len(observations), 1)
        reason, visible, persisted = observations[0]
        self.assertEqual(reason, 'tasks')
        self.assertEqual(visible, persisted)
        self.assertEqual([t['text'] for t in visible], ['每日', '未完成', '今天完成', '过期', '今天'])
        self.assertFalse(visible[0]['done'])
        self.assertIsNone(visible[0]['done_on'])
        self.assertTrue(visible[0]['pinned'])
        self.assertTrue(visible[2]['done'])
        self.assertEqual([t['kind'] for t in visible[-2:]], ['temp', 'daily'])
        for task in visible[-2:]:
            self.assertFalse(task['pinned'])
            self.assertFalse(task['done'])
            self.assertIsNone(task['done_on'])
        restored = store.Store(self.path)
        self.assertEqual(restored.settings['last_rollover'], '2026-11-15')
        self.assertEqual([e['text'] for e in restored.pending_events('2026-11-16')], ['未来'])

    def test_same_day_and_restart_are_idempotent_without_extra_writes(self):
        self.seed(events=[self.event('事项', '2026-11-15')])
        self.store.sync_calendar('2026-11-15')
        tasks = copy.deepcopy(self.store.tasks)
        self.store = store.Store(self.path)
        listener = Mock()
        self.store.add_listener(listener)
        with patch('store.os.replace', wraps=os.replace) as replace:
            result = self.store.sync_calendar('2026-11-15')
        self.assertEqual(result['added'], 0)
        self.assertEqual(self.store.tasks, tasks)
        replace.assert_not_called()
        listener.assert_not_called()

    def test_new_due_event_is_processed_even_after_same_day_success(self):
        self.store.sync_calendar('2026-11-15')
        self.store.add_event('2026-11-15', '当天新增', 'daily')
        result = self.store.sync_calendar('2026-11-15')
        self.assertEqual(result['added'], 1)
        self.assertEqual(self.store.tasks[0]['text'], '当天新增')
        self.assertEqual(self.store.tasks[0]['kind'], 'daily')

    def test_calendar_add_immediately_persists_and_converts_today_event(self):
        instance = self.instance()
        entry = Mock()
        entry.get_value.return_value = '日历输入'
        window = SimpleNamespace(store=self.store, app=instance, entry=entry,
                                 _selected=dt.date(2026, 11, 15), _kind='daily')
        with patch('store.today_str', return_value='2026-11-15'):
            CalendarWindow._add_event(window)
        restored = store.Store(self.path)
        self.assertEqual([t['text'] for t in restored.tasks], ['日历输入'])
        self.assertEqual(restored.tasks[0]['kind'], 'daily')
        self.assertTrue(restored.events[0]['pulled'])
        entry.reset.assert_called_once()
        instance.root.toast.assert_called_once()

    def test_legacy_missing_completion_date_and_clock_rollback_preserve_tasks(self):
        self.seed(tasks=[self.task('旧每日', 'daily', done=True),
                         self.task('旧临时', done=True),
                         self.task('未来完成', done_on='2026-11-16', done=True)])
        before = copy.deepcopy(self.store.tasks)
        for date in ('2026-11-15', '2026-11-14'):
            result = self.store.sync_calendar(date)
            self.assertEqual(result['reset'], 0)
            self.assertEqual(result['removed'], 0)
            self.assertEqual(self.store.tasks, before)

    def test_uses_one_local_date_for_all_operations(self):
        self.seed(events=[self.event('明天', '2026-11-16')])
        with patch('store.today_str', side_effect=['2026-11-15', '2026-11-16']) as clock:
            result = self.store.sync_calendar()
        clock.assert_called_once_with()
        self.assertEqual(result['date'], '2026-11-15')
        self.assertEqual(result['added'], 0)

    def test_write_failures_preserve_memory_file_and_saved_event_then_retry(self):
        for failure in ('store.os.makedirs', 'builtins.open', 'store.os.replace'):
            with self.subTest(failure=failure):
                self.seed(tasks=[self.task('每日', 'daily', '2026-11-14', True),
                                 self.task('临时', done_on='2026-11-14', done=True)],
                          last_rollover='2026-11-14')
                event_id = self.store.add_event('2026-11-15', '已经保存的事项')
                before_tasks = copy.deepcopy(self.store.tasks)
                before_events = copy.deepcopy(self.store.events)
                before_settings = dict(self.store.settings)
                with open(self.path, 'rb') as handle:
                    before_file = handle.read()
                listener = Mock()
                self.store.add_listener(listener)
                with patch(failure, side_effect=OSError('磁盘不可写')):
                    self.assertIsNone(self.store.sync_calendar('2026-11-15'))
                self.assertEqual(self.store.tasks, before_tasks)
                self.assertEqual(self.store.events, before_events)
                self.assertEqual(self.store.settings, before_settings)
                self.assertEqual(self.store.events[0]['id'], event_id)
                self.assertFalse(self.store.events[0]['pulled'])
                with open(self.path, 'rb') as handle:
                    self.assertEqual(handle.read(), before_file)
                listener.assert_not_called()
                self.assertIsNotNone(self.store.calendar_error)
                result = self.store.sync_calendar('2026-11-15')
                self.assertEqual((result['reset'], result['removed'], result['added']), (1, 1, 1))
                self.assertIsNone(self.store.calendar_error)
                self.assertIsNone(self.store.last_error)
                self.assertEqual(len(store.Store(self.path).tasks), 2)
                listener.assert_called_once_with('tasks')

    def test_unrelated_save_does_not_clear_pending_calendar_failure(self):
        self.seed(events=[self.event('事项', '2026-11-15')])
        with patch('store.os.replace', side_effect=OSError('磁盘不可写')):
            self.store.sync_calendar('2026-11-15')
        self.store.set_setting('main_geometry', '800x600', notify=False)
        self.assertIsNone(self.store.last_error)
        self.assertIsNotNone(self.store.calendar_error)
        instance = self.instance()
        instance.refresh_status()
        self.assertIn('保存失败', instance.status_text)
        with patch('store.today_str', return_value='2026-11-15'):
            instance._check_day()
        self.assertNotIn('保存失败', instance.status_text)
        self.assertIsNone(self.store.calendar_error)

    def test_app_retries_startup_failure_on_same_day(self):
        self.seed(events=[self.event('事项', '2026-11-15')])
        instance = self.instance()
        with patch('store.today_str', return_value='2026-11-15'):
            with patch('store.os.replace', side_effect=OSError('磁盘不可写')):
                instance.sync_calendar(quiet=True)
            self.assertEqual(self.store.tasks, [])
            self.assertIn('保存失败', instance.status_text)
            instance.root.toast.assert_not_called()
            instance.float.flash_hint.assert_not_called()
            instance._check_day()
        self.assertEqual(len(self.store.tasks), 1)
        self.assertNotIn('保存失败', instance.status_text)
        instance.root.after.assert_called_once_with(60000, instance._check_day)
        instance.root.toast.assert_called_once()

    def test_app_retries_new_same_day_event_after_success(self):
        self.store.sync_calendar('2026-11-15')
        instance = self.instance()
        self.store.add_event('2026-11-15', '当天新增')
        with patch('store.today_str', return_value='2026-11-15'):
            with patch('store.os.replace', side_effect=OSError('磁盘不可写')):
                instance.sync_calendar()
            instance._check_day()
        self.assertEqual([t['text'] for t in self.store.tasks], ['当天新增'])
        self.assertTrue(store.Store(self.path).events[0]['pulled'])

    def test_app_retries_failed_day_transition(self):
        self.seed(tasks=[self.task('每日', 'daily', '2026-11-15', True)],
                  last_rollover='2026-11-15')
        instance = self.instance()
        with patch('store.today_str', return_value='2026-11-16'):
            with patch('store.os.replace', side_effect=OSError('磁盘不可写')):
                instance._check_day()
            self.assertTrue(self.store.tasks[0]['done'])
            instance._check_day()
        self.assertFalse(self.store.tasks[0]['done'])
        self.assertEqual(instance.root.after.call_count, 2)

    def test_no_changes_do_not_produce_success_toasts(self):
        self.store.sync_calendar('2026-11-15')
        instance = self.instance()
        with patch('store.today_str', return_value='2026-11-15'):
            instance._check_day()
        instance.root.toast.assert_not_called()
        instance.float.flash_hint.assert_not_called()


if __name__ == '__main__':
    unittest.main()
