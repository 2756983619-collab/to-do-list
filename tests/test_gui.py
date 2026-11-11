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


if __name__ == '__main__':
    unittest.main()
