"""无需 GUI 的核心逻辑测试：数据存取 + 监控判定。运行：python -m unittest discover -s tests"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitor  # noqa: E402
import store as store_module  # noqa: E402


def _state(windows=(), processes=()):
    return {"windows": list(windows), "processes": set(processes), "foreground": 0}


def _window(exe, title, minimized=False):
    return {"hwnd": 1, "pid": 10, "exe": exe, "title": title, "minimized": minimized}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "todo.json")
        self.store = store_module.Store(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_task_crud_and_persistence(self):
        task_id = self.store.add_task("写周报")
        self.store.add_task("   ")
        self.assertEqual(len(self.store.tasks), 1)
        self.store.toggle_task(task_id)
        self.assertTrue(self.store.tasks[0]["done"])

        reloaded = store_module.Store(self.path)
        self.assertEqual(reloaded.tasks[0]["text"], "写周报")
        self.assertTrue(reloaded.tasks[0]["done"])

        reloaded.set_task_text(task_id, "写周报（周一前）")
        self.assertEqual(store_module.Store(self.path).tasks[0]["text"], "写周报（周一前）")

        removed = reloaded.clear_done()
        self.assertEqual(removed, 1)
        self.assertEqual(store_module.Store(self.path).tasks, [])

    def test_task_pin_toggles_and_persists(self):
        """钉住标记：默认不钉，可来回切，写入磁盘后还在。"""
        task_id = self.store.add_task("盯住这一条")
        self.assertFalse(self.store.tasks[0]["pinned"])
        self.assertEqual(self.store.pinned_tasks(), [])

        self.store.toggle_pin(task_id)
        self.assertTrue(self.store.tasks[0]["pinned"])
        self.assertEqual([task["id"] for task in self.store.pinned_tasks()], [task_id])
        self.assertTrue(store_module.Store(self.path).tasks[0]["pinned"])

        self.store.toggle_pin(task_id)
        self.assertFalse(store_module.Store(self.path).tasks[0]["pinned"])

    def test_task_kind_defaults_to_temp(self):
        self.store.add_task("默认临时")
        self.store.add_task("每日一条", kind="daily")
        kinds = [task["kind"] for task in self.store.tasks]
        self.assertEqual(kinds, ["temp", "daily"])

    def test_targets_deduplicate(self):
        self.assertTrue(self.store.add_target("website", "B站", "bilibili"))
        self.assertFalse(self.store.add_target("website", "B站 2", "BILIBILI"))
        self.assertTrue(self.store.add_target("software", "Steam", "steam"))
        self.assertEqual(len(self.store.targets), 2)
        self.assertEqual([t["kind"] for t in self.store.targets], ["website", "software"])

    def test_settings_are_clamped(self):
        self.store.set_setting("interval", 5)
        self.store.set_setting("float_opacity", 0.1)
        self.assertEqual(self.store.settings["interval"], 10)
        reloaded = store_module.Store(self.path)
        self.assertEqual(reloaded.settings["interval"], 10)
        self.assertAlmostEqual(reloaded.settings["float_opacity"], 0.35)

    def test_broken_file_falls_back_to_defaults(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"tasks": "nope", "settings": {"interval": "abc"}}, handle)
        store = store_module.Store(self.path)
        self.assertEqual(store.tasks, [])
        self.assertEqual(store.settings["interval"], 90)

    def test_corrupt_file_is_backed_up(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{ 这不是 JSON")
        store_module.Store(self.path)
        self.assertTrue(os.path.exists(self.path + ".bak"))

    def test_bom_file_still_loads(self):
        payload = {"tasks": [{"id": "t1", "text": "带 BOM 的任务", "done": False}],
                   "targets": [], "settings": {"interval": 30}}
        # 记事本默认会给 UTF-8 文件加 BOM，读的时候必须能吃下去
        with open(self.path, "w", encoding="utf-8-sig") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        store = store_module.Store(self.path)
        self.assertEqual(store.tasks[0]["text"], "带 BOM 的任务")
        self.assertEqual(store.settings["interval"], 30)


class DetectionTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(monitor.normalize_match("website", "https://www.Bilibili.com/"), "bilibili.com")
        self.assertEqual(monitor.normalize_match("software", "Steam.EXE"), "steam")

    def test_software_by_process(self):
        target = {"id": "1", "kind": "software", "match": "steam", "enabled": True}
        running, _ = monitor.detect_target(target, _state(processes={"steam.exe"}))
        self.assertTrue(running)
        running, _ = monitor.detect_target(target, _state(processes={"steamwebhelper.exe"}))
        self.assertFalse(running)

    def test_software_by_window_title(self):
        target = {"id": "1", "kind": "software", "match": "记事本", "enabled": True}
        running, evidence = monitor.detect_target(
            target, _state(windows=[_window("notepad.exe", "未命名 - 记事本")]))
        self.assertTrue(running)
        self.assertIn("记事本", evidence)

    def test_website_requires_visible_browser_tab(self):
        target = {"id": "1", "kind": "website", "match": "bilibili", "enabled": True}
        visible = _state(windows=[_window("chrome.exe", "哔哩哔哩 (゜-゜)つロ 干杯~-bilibili - Google Chrome")])
        self.assertTrue(monitor.detect_target(target, visible)[0])

        minimized = _state(windows=[_window("chrome.exe", "哔哩哔哩 - Google Chrome", minimized=True)])
        self.assertFalse(monitor.detect_target(target, minimized)[0])

        other = _state(windows=[_window("notepad.exe", "bilibili 笔记")])
        self.assertFalse(monitor.detect_target(target, other)[0])


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.target = {"id": "t1", "kind": "software", "match": "steam", "enabled": True}

    def test_first_detection_warns_once(self):
        prev, last = {}, {}
        alerts = monitor.evaluate([self.target], _state(processes={"steam.exe"}),
                                  100.0, prev, last, repeat=False)
        self.assertEqual(len(alerts), 1)
        alerts = monitor.evaluate([self.target], _state(processes={"steam.exe"}),
                                  200.0, prev, last, repeat=False)
        self.assertEqual(alerts, [])

    def test_repeat_after_interval(self):
        prev, last = {}, {}
        monitor.evaluate([self.target], _state(processes={"steam.exe"}), 100.0, prev, last,
                         repeat=True, interval=90)
        self.assertEqual(monitor.evaluate([self.target], _state(processes={"steam.exe"}),
                                          150.0, prev, last, repeat=True, interval=90), [])
        self.assertEqual(len(monitor.evaluate([self.target], _state(processes={"steam.exe"}),
                                              191.0, prev, last, repeat=True, interval=90)), 1)

    def test_reappear_warns_again(self):
        prev, last = {}, {}
        monitor.evaluate([self.target], _state(processes={"steam.exe"}), 100.0, prev, last)
        monitor.evaluate([self.target], _state(processes=set()), 110.0, prev, last)
        alerts = monitor.evaluate([self.target], _state(processes={"steam.exe"}), 120.0, prev, last)
        self.assertEqual(len(alerts), 1)

    def test_disabled_target_is_ignored(self):
        target = dict(self.target, enabled=False)
        prev, last = {}, {}
        self.assertEqual(monitor.evaluate([target], _state(processes={"steam.exe"}),
                                          100.0, prev, last), [])

    def test_stale_ids_are_pruned(self):
        prev, last = {"gone": True}, {"gone": 1.0}
        monitor.evaluate([self.target], _state(processes=set()), 100.0, prev, last)
        self.assertEqual(prev, {"t1": False})
        self.assertEqual(last, {})


if __name__ == "__main__":
    unittest.main()
