"""Opt-in desktop integration check: TODO_LIST_GUI_TESTS=1 python -m unittest tests.test_gui -v."""
import os
import socket
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import app
import store


@unittest.skipUnless(os.environ.get('TODO_LIST_GUI_TESTS') == '1', 'requires a desktop session')
class DesktopTests(unittest.TestCase):
    def test_windows_tasks_overlay_and_single_instance(self):
        errors = []
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'TODO_LIST_DATA': os.path.join(folder, 'todo.json')}), patch.object(tk.Tk, 'report_callback_exception', lambda self, kind, value, trace: errors.append(str(value))):
            instance = app.App()
            server = None
            try:
                instance.root.update()
                instance.root.task_entry.set_value('Mac 集成测试')
                instance.root._add_task()
                task = instance.store.tasks[0]['id']
                instance.store.set_task_text(task, '修改后')
                instance.store.toggle_task(task)
                restored = store.Store(app.DATA_FILE)
                self.assertEqual(restored.tasks[0]['text'], '修改后')
                self.assertTrue(restored.tasks[0]['done'])
                instance.float.show()
                instance.float.apply_alpha(0.7)
                instance.root.update()
                self.assertAlmostEqual(float(instance.float.attributes('-alpha')), 0.7, places=1)
                if os.sys.platform == 'win32':
                    # Windows 用色键挖圆角，底色必须是那个哨兵色
                    self.assertEqual(instance.float.cget('bg'), '#ff01fe')
                else:
                    self.assertNotEqual(instance.float.cget('bg'), '#ff01fe')
                instance.float._start_drag(SimpleNamespace(x_root=100, y_root=100))
                instance.float._on_drag(SimpleNamespace(x_root=120, y_root=120))
                instance.float._end_drag()
                self.assertIsNotNone(instance.store.settings['float_pos'])
                instance.reminder.show([{'kind': 'software', 'name': '测试', 'detail': '集成测试'}])
                instance.root.update()
                self.assertTrue(instance.reminder.winfo_ismapped())
                instance.reminder._snooze()
                self.assertGreater(instance.snooze_until, time.time() + 590)
                self.assertEqual(instance.reminder.state(), 'withdrawn')
                self.assertTrue(instance.float.bind('<Control-Button-1>') if os.sys.platform == 'darwin' else True)

                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', 0))
                    port = probe.getsockname()[1]
                with patch.object(app, 'LOCK_PORT', port):
                    server = app._serve_show_requests(lambda: instance.queue.put(('show', {}, time.time())))
                    self.assertIsNotNone(server)
                    instance.hide_main()
                    self.assertTrue(app._notify_existing())
                    deadline = time.monotonic() + 2
                    while instance.root.state() == 'withdrawn' and time.monotonic() < deadline:
                        instance.root.update()
                        time.sleep(0.01)
                    self.assertEqual(instance.root.state(), 'normal')
                instance.store.delete_task(task)
                instance.root.update()
                self.assertEqual(instance.store.tasks, [])
                self.assertEqual(errors, [])
            finally:
                if server:
                    server.close()
                instance.quit()
                instance.worker.join(timeout=2)

    def test_close_to_background_and_pin(self):
        errors = []
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'TODO_LIST_DATA': os.path.join(folder, 'todo.json')}), patch.object(tk.Tk, 'report_callback_exception', lambda self, kind, value, trace: errors.append(str(value))):
            instance = app.App()
            try:
                instance.root.update()
                # 关闭主窗口 = 收起到后台，悬浮窗留下来
                instance.close_main()
                instance.root.update()
                self.assertEqual(instance.root.state(), 'withdrawn')
                self.assertTrue(instance.store.settings['float_visible'])
                # 主界面收起时再点隐藏悬浮窗，不会把自己藏没
                instance.hide_float()
                instance.root.update()
                self.assertEqual(instance.root.state(), 'normal')
                # 钉住：记住设置、禁止拖动
                instance.show_float()
                instance.float.set_pinned(True)
                instance.root.update()
                self.assertTrue(instance.store.settings['float_pinned'])
                self.assertEqual(float(instance.float.attributes('-topmost')), 1.0)
                instance.float._start_drag(SimpleNamespace(x_root=100, y_root=100))
                self.assertIsNone(instance.float._drag_offset)
                instance.float.set_pinned(False)
                instance.root.update()
                self.assertFalse(instance.store.settings['float_pinned'])
                instance.float._start_drag(SimpleNamespace(x_root=100, y_root=100))
                self.assertIsNotNone(instance.float._drag_offset)
                instance.float._end_drag()
                self.assertTrue(float(instance.float.attributes('-alpha')) > 0.3)
                self.assertEqual(errors, [])
            finally:
                instance.quit()
                instance.worker.join(timeout=2)

    def test_close_main_keeps_hidden_float_hidden(self):
        """先关悬浮窗、再关主界面：不应该把悬浮窗重新弹出来。"""
        errors = []
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'TODO_LIST_DATA': os.path.join(folder, 'todo.json')}), patch.object(tk.Tk, 'report_callback_exception', lambda self, kind, value, trace: errors.append(str(value))):
            instance = app.App()
            try:
                instance.root.update()
                instance.hide_float()
                instance.root.update()
                self.assertFalse(instance.store.settings['float_visible'])

                instance.close_main()
                instance.root.update()
                self.assertEqual(instance.root.state(), 'withdrawn')
                self.assertFalse(instance.store.settings['float_visible'])
                self.assertEqual(instance.float.state(), 'withdrawn')
                self.assertEqual(errors, [])
            finally:
                instance.quit()
                instance.worker.join(timeout=2)

    def test_float_pin_survives_collapse(self):
        """悬浮窗折叠后只剩钉住的任务。"""
        errors = []
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'TODO_LIST_DATA': os.path.join(folder, 'todo.json')}), patch.object(tk.Tk, 'report_callback_exception', lambda self, kind, value, trace: errors.append(str(value))):
            instance = app.App()
            try:
                instance.root.update()
                keep = instance.store.add_task("钉住的任务")
                instance.store.add_task("普通任务")
                instance.store.toggle_pin(keep)
                instance.root.update()

                instance.float.set_collapsed(True)
                instance.root.update()

                def collect(widget):
                    texts = []
                    for child in widget.winfo_children():
                        if child.winfo_class() == 'Label':
                            texts.append(child.cget('text'))
                        elif child.winfo_class() == 'Frame':
                            texts.extend(collect(child))
                    return texts

                labels = collect(instance.float.scroller.inner)
                self.assertEqual(labels, ['钉住的任务'])
                self.assertEqual(errors, [])
            finally:
                instance.quit()
                instance.worker.join(timeout=2)

    def test_tray_icon_and_menu_commands(self):
        """托盘图标：菜单命令要能在主线程里正确落到窗口/设置上。"""
        import tray
        errors = []
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'TODO_LIST_DATA': os.path.join(folder, 'todo.json')}), patch.object(tk.Tk, 'report_callback_exception', lambda self, kind, value, trace: errors.append(str(value))):
            instance = app.App()
            try:
                instance.root.update()
                if os.sys.platform != 'win32':
                    self.assertIsNone(instance.tray)
                    self.assertIsNone(tray.WNDPROC)
                    return
                self.assertIsNotNone(instance.tray)
                self.assertTrue(instance.tray.added)
                self.assertEqual(instance._tray_state(), {'monitor': False, 'float_visible': True})
                # 菜单 id -> 动作 的映射齐全
                self.assertEqual(set(tray.MENU_ACTIONS.values()),
                                 {'show_main', 'toggle_float', 'toggle_monitor', 'quit'})
                instance._on_tray('toggle_monitor')
                instance.root.update()
                self.assertTrue(instance.store.settings['monitor'])
                instance._on_tray('toggle_monitor')
                instance.root.update()
                self.assertFalse(instance.store.settings['monitor'])
                instance._on_tray('toggle_float')
                instance.root.update()
                self.assertFalse(instance.store.settings['float_visible'])
                self.assertEqual(instance.float.state(), 'withdrawn')
                instance._on_tray('toggle_float')
                instance.root.update()
                self.assertTrue(instance.store.settings['float_visible'])
                instance._on_tray('show_main')
                instance.root.update()
                self.assertEqual(instance.root.state(), 'normal')
                # 托盘在，主界面收起 + 悬浮窗也收起时，不能把主界面硬拉回来
                instance.close_main()
                instance.root.update()
                instance._on_tray('toggle_float')
                instance.root.update()
                self.assertFalse(instance.store.settings['float_visible'])
                self.assertEqual(instance.root.state(), 'withdrawn')
                self.assertEqual(errors, [])
            finally:
                instance.quit()
                instance.worker.join(timeout=2)

    def test_float_switch_label_follows_settings(self):
        """开关是绿的、文字却写着"监控已关闭"这种不一致，之前真的出现过。"""
        errors = []
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'TODO_LIST_DATA': os.path.join(folder, 'todo.json')}), patch.object(tk.Tk, 'report_callback_exception', lambda self, kind, value, trace: errors.append(str(value))):
            instance = app.App()
            try:
                instance.store.set_setting('monitor', True)
                instance.root.update()
                self.assertTrue(instance.float.switch._value)
                self.assertEqual(instance.float.switch_state.cget('text'), '监控已开启')
                self.assertEqual(instance.float.interval_label.cget('text'), '每 90 秒')
                instance.store.set_setting('monitor', False)
                instance.root.update()
                self.assertFalse(instance.float.switch._value)
                self.assertEqual(instance.float.switch_state.cget('text'), '监控已关闭')
                self.assertEqual(instance.float.interval_label.cget('text'), '')
                self.assertEqual(errors, [])
            finally:
                instance.quit()
                instance.worker.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
