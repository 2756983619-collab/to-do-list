"""JSON backed data store shared by the main window and the floating window."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

DEFAULT_SETTINGS = {
    "monitor": False,          # 监控总开关
    "repeat_alert": True,      # 运行期间持续提醒
    "interval": 90,            # 重复提醒间隔（秒）
    "float_visible": True,     # 显示悬浮窗
    "float_opacity": 0.9,      # 悬浮窗不透明度（半透明）
    "float_pos": None,         # 悬浮窗上次位置 [x, y]
    "main_geometry": None,     # 主窗口上次大小位置
    "last_callout": 0.0,       # 上次提醒时间戳（仅作展示）
}


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


class Store:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self._listeners = []
        self.last_error = None
        self.data = {"version": 1, "tasks": [], "targets": [], "settings": dict(DEFAULT_SETTINGS)}
        self.load()

    # ------------------------------------------------------------ properties
    @property
    def tasks(self):
        return self.data["tasks"]

    @property
    def targets(self):
        return self.data["targets"]

    @property
    def settings(self):
        return self.data["settings"]

    # ------------------------------------------------------------ persistence
    def load(self) -> None:
        raw = {}
        try:
            # utf-8-sig：兼容记事本等工具写入的 BOM，否则会把用户数据当成坏文件清空
            with open(self.path, "r", encoding="utf-8-sig") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            self._quarantine()
            raw = {}
        if not isinstance(raw, dict):
            raw = {}

        tasks = []
        for item in raw.get("tasks") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            tasks.append({
                "id": str(item.get("id") or _new_id()),
                "text": text,
                "done": bool(item.get("done")),
                "created": float(item.get("created") or time.time()),
            })

        targets = []
        seen = set()
        for item in raw.get("targets") or []:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind") if item.get("kind") in ("website", "software") else "software"
            match = str(item.get("match", "")).strip()
            if not match:
                continue
            key = (kind, match.lower())
            if key in seen:
                continue
            seen.add(key)
            targets.append({
                "id": str(item.get("id") or _new_id()),
                "kind": kind,
                "name": str(item.get("name") or match).strip() or match,
                "match": match,
                "enabled": bool(item.get("enabled", True)),
            })

        settings = dict(DEFAULT_SETTINGS)
        for key, value in (raw.get("settings") or {}).items():
            if key in DEFAULT_SETTINGS and value is not None:
                settings[key] = value
        settings["monitor"] = bool(settings.get("monitor"))
        settings["repeat_alert"] = bool(settings.get("repeat_alert"))
        settings["float_visible"] = bool(settings.get("float_visible", True))
        try:
            settings["interval"] = max(10, min(3600, int(settings.get("interval") or 90)))
        except (TypeError, ValueError):
            settings["interval"] = 90
        try:
            settings["float_opacity"] = max(0.35, min(1.0, float(settings.get("float_opacity") or 0.9)))
        except (TypeError, ValueError):
            settings["float_opacity"] = 0.9
        pos = settings.get("float_pos")
        if not (isinstance(pos, (list, tuple)) and len(pos) == 2):
            settings["float_pos"] = None

        with self.lock:
            self.data = {"version": 1, "tasks": tasks, "targets": targets, "settings": settings}

    def _quarantine(self) -> None:
        """读不出来的文件先留个 .bak 备份，避免直接覆盖导致用户数据彻底丢失。"""
        try:
            if os.path.getsize(self.path) <= 0:
                return
            os.replace(self.path, self.path + ".bak")
        except OSError:
            pass

    def save(self) -> None:
        with self.lock:
            snapshot = {
                "version": 1,
                "tasks": [dict(task) for task in self.data["tasks"]],
                "targets": [dict(target) for target in self.data["targets"]],
                "settings": dict(self.data["settings"]),
            }
        folder = os.path.dirname(os.path.abspath(self.path))
        try:
            os.makedirs(folder, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self.last_error = None
        except OSError as exc:
            # 保存失败要留痕，否则用户会以为"任务自己消失了"
            self.last_error = f"{exc}"
            print(f"[store] save failed for {self.path}: {exc!r}")

    # ------------------------------------------------------------ listeners
    def add_listener(self, callback) -> None:
        self._listeners.append(callback)

    def _emit(self, reason: str) -> None:
        for callback in list(self._listeners):
            try:
                callback(reason)
            except Exception as exc:  # never let a UI bug break persistence
                print(f"[store] listener error: {exc!r}")

    def _changed(self, reason: str) -> None:
        self.save()
        self._emit(reason)

    def _find(self, collection, item_id):
        for item in collection:
            if item.get("id") == item_id:
                return item
        return None

    # ------------------------------------------------------------ tasks
    def add_task(self, text: str):
        text = (text or "").strip()
        if not text:
            return None
        task = {"id": _new_id(), "text": text, "done": False, "created": time.time()}
        with self.lock:
            self.data["tasks"].append(task)
        self._changed("tasks")
        return task["id"]

    def toggle_task(self, task_id: str) -> None:
        with self.lock:
            task = self._find(self.data["tasks"], task_id)
            if task is None:
                return
            task["done"] = not task["done"]
        self._changed("tasks")

    def set_task_text(self, task_id: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self.lock:
            task = self._find(self.data["tasks"], task_id)
            if task is None or task["text"] == text:
                return
            task["text"] = text
        # silent for the main window (it is the editor), but redraw the float window
        self.save()
        self._emit("tasks_text")

    def delete_task(self, task_id: str) -> None:
        with self.lock:
            before = len(self.data["tasks"])
            self.data["tasks"] = [t for t in self.data["tasks"] if t.get("id") != task_id]
            if len(self.data["tasks"]) == before:
                return
        self._changed("tasks")

    def clear_done(self) -> int:
        with self.lock:
            remaining = [t for t in self.data["tasks"] if not t.get("done")]
            removed = len(self.data["tasks"]) - len(remaining)
            if not removed:
                return 0
            self.data["tasks"] = remaining
        self._changed("tasks")
        return removed

    # ------------------------------------------------------------ monitor targets
    def add_target(self, kind: str, name: str, match: str) -> bool:
        kind = kind if kind in ("website", "software") else "software"
        match = (match or "").strip()
        if not match:
            return False
        name = (name or "").strip() or match
        with self.lock:
            for target in self.data["targets"]:
                if target["kind"] == kind and target["match"].lower() == match.lower():
                    return False
            self.data["targets"].append({
                "id": _new_id(),
                "kind": kind,
                "name": name,
                "match": match,
                "enabled": True,
            })
        self._changed("targets")
        return True

    def toggle_target(self, target_id: str) -> None:
        with self.lock:
            target = self._find(self.data["targets"], target_id)
            if target is None:
                return
            target["enabled"] = not target["enabled"]
        self._changed("targets")

    def delete_target(self, target_id: str) -> None:
        with self.lock:
            before = len(self.data["targets"])
            self.data["targets"] = [t for t in self.data["targets"] if t.get("id") != target_id]
            if len(self.data["targets"]) == before:
                return
        self._changed("targets")

    # ------------------------------------------------------------ settings
    def set_setting(self, key: str, value, notify: bool = True) -> None:
        with self.lock:
            if key not in self.data["settings"]:
                return
            if key == "interval":
                try:
                    value = max(10, min(3600, int(value)))
                except (TypeError, ValueError):
                    return
            elif key == "float_opacity":
                try:
                    value = round(max(0.35, min(1.0, float(value))), 2)
                except (TypeError, ValueError):
                    return
            if self.data["settings"][key] == value:
                return
            self.data["settings"][key] = value
        if notify:
            self._changed("settings")
        else:
            self.save()

    # ------------------------------------------------------------ monitor view
    def snapshot(self, snooze_until: float = 0.0) -> dict:
        with self.lock:
            settings = dict(self.data["settings"])
            targets = [dict(target) for target in self.data["targets"]]
        return {
            "enabled": bool(settings.get("monitor")),
            "repeat": bool(settings.get("repeat_alert", True)),
            "interval": max(5, int(settings.get("interval") or 90)),
            "snooze_until": float(snooze_until or 0.0),
            "targets": targets,
        }

    def stats(self) -> dict:
        with self.lock:
            tasks = self.data["tasks"]
            return {
                "total": len(tasks),
                "done": sum(1 for task in tasks if task.get("done")),
                "targets": len(self.data["targets"]),
            }
