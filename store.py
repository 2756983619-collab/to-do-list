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
    "float_pinned": False,     # 钉住悬浮窗：固定最前、不可拖动
    "tray_hint_shown": False,  # 是否已经提示过"图标在右下角"
    "main_geometry": None,     # 主窗口上次大小位置
    "last_callout": 0.0,       # 上次提醒时间戳（仅作展示）
    "tasks_collapsed": False,  # 主界面任务清单是否折叠
    "float_collapsed": False,  # 悬浮窗任务清单是否折叠
    "daily_collapsed": False,  # 主界面"每日任务"分组是否折叠
    "temp_collapsed": False,   # 主界面"临时任务"分组是否折叠
    "last_rollover": None,     # 上次跨天滚动执行的日期（YYYY-MM-DD）
}

TASK_KINDS = ("temp", "daily")
TASK_KIND_LABELS = {"temp": "临时任务", "daily": "每日任务"}


def today_str(now: float | None = None) -> str:
    """本地日期字符串，跨天判定一律用它。"""
    return time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))


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
    def events(self):
        return self.data["events"]

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
            kind = item.get("kind") if item.get("kind") in TASK_KINDS else "temp"
            done = bool(item.get("done"))
            done_on = item.get("done_on")
            if not (isinstance(done_on, str) and len(done_on) == 10):
                done_on = None
            tasks.append({
                "id": str(item.get("id") or _new_id()),
                "text": text,
                "done": done,
                "done_on": done_on,
                "kind": kind,
                "pinned": bool(item.get("pinned", False)),
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
        settings["float_pinned"] = bool(settings.get("float_pinned", False))
        settings["tasks_collapsed"] = bool(settings.get("tasks_collapsed", False))
        settings["float_collapsed"] = bool(settings.get("float_collapsed", False))
        settings["daily_collapsed"] = bool(settings.get("daily_collapsed", False))
        settings["temp_collapsed"] = bool(settings.get("temp_collapsed", False))
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

        events = []
        for item in raw.get("events") or []:
            if not isinstance(item, dict):
                continue
            date = str(item.get("date", "")).strip()
            text = str(item.get("text", "")).strip()
            if len(date) != 10 or not text:
                continue
            events.append({
                "id": str(item.get("id") or _new_id()),
                "date": date,
                "text": text,
                "kind": item.get("kind") if item.get("kind") in TASK_KINDS else "temp",
                "pulled": bool(item.get("pulled")),
            })
        events.sort(key=lambda event: event["date"])

        with self.lock:
            self.data = {
                "version": 2,
                "tasks": tasks,
                "targets": targets,
                "events": events,
                "settings": settings,
            }

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
                "version": 2,
                "tasks": [dict(task) for task in self.data["tasks"]],
                "targets": [dict(target) for target in self.data["targets"]],
                "events": [dict(event) for event in self.data["events"]],
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
    def add_task(self, text: str, kind: str = "temp"):
        text = (text or "").strip()
        if not text:
            return None
        kind = kind if kind in TASK_KINDS else "temp"
        task = {
            "id": _new_id(),
            "text": text,
            "done": False,
            "done_on": None,
            "kind": kind,
            "pinned": False,
            "created": time.time(),
        }
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
            task["done_on"] = today_str() if task["done"] else None
        self._changed("tasks")

    def set_task_kind(self, task_id: str, kind: str) -> None:
        if kind not in TASK_KINDS:
            return
        with self.lock:
            task = self._find(self.data["tasks"], task_id)
            if task is None or task.get("kind") == kind:
                return
            task["kind"] = kind
        self._changed("tasks")

    def toggle_pin(self, task_id: str) -> None:
        """把任务钉在悬浮窗上：折叠悬浮窗时这些任务仍然显示。"""
        with self.lock:
            task = self._find(self.data["tasks"], task_id)
            if task is None:
                return
            task["pinned"] = not bool(task.get("pinned"))
        self._changed("tasks")

    def pinned_tasks(self) -> list:
        return [task for task in self.data["tasks"] if task.get("pinned")]

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

    # ------------------------------------------------------------ 跨天滚动
    def rollover(self, today: str | None = None) -> dict:
        """跨天滚动：每日任务重新出现，已完成的临时任务自动清除。

        返回 {"date": 今天, "reset": n, "removed": n}。没有变化也记录执行日期。
        """
        today = today or today_str()
        reset = 0
        removed = 0
        with self.lock:
            kept = []
            for task in self.data["tasks"]:
                if not task.get("done"):
                    kept.append(task)
                    continue
                done_on = task.get("done_on") or today
                if done_on >= today:
                    kept.append(task)
                    continue
                if task.get("kind") == "daily":
                    task["done"] = False
                    task["done_on"] = None
                    reset += 1
                    kept.append(task)
                else:
                    removed += 1
            if reset or removed:
                self.data["tasks"] = kept
            self.data["settings"]["last_rollover"] = today
        if reset or removed:
            self._changed("tasks")
        else:
            self.save()
        return {"date": today, "reset": reset, "removed": removed}

    # ------------------------------------------------------------ 日历事项
    def add_event(self, date: str, text: str, kind: str = "temp"):
        date = (date or "").strip()
        text = (text or "").strip()
        if len(date) != 10 or not text:
            return None
        kind = kind if kind in TASK_KINDS else "temp"
        event = {"id": _new_id(), "date": date, "text": text, "kind": kind, "pulled": False}
        with self.lock:
            self.data["events"].append(event)
            self.data["events"].sort(key=lambda item: item.get("date", ""))
        self._changed("events")
        return event["id"]

    def delete_event(self, event_id: str) -> None:
        with self.lock:
            before = len(self.data["events"])
            self.data["events"] = [e for e in self.data["events"] if e.get("id") != event_id]
            if len(self.data["events"]) == before:
                return
        self._changed("events")

    def events_on(self, date: str) -> list:
        with self.lock:
            return [dict(e) for e in self.data["events"] if e.get("date") == date]

    def events_between(self, start: str, end: str) -> list:
        with self.lock:
            return [dict(e) for e in self.data["events"] if start <= e.get("date", "") <= end]

    def pending_events(self, today: str | None = None) -> list:
        """到期但还没拉进任务清单的日历事项。"""
        today = today or today_str()
        with self.lock:
            return [dict(e) for e in self.data["events"]
                    if not e.get("pulled") and e.get("date", "") <= today]

    def pull_due_events(self, today: str | None = None) -> int:
        """把到期（含过期没处理）的日历事项变成任务，返回新增条数。"""
        today = today or today_str()
        added = 0
        with self.lock:
            for event in self.data["events"]:
                if event.get("pulled") or event.get("date", "") > today:
                    continue
                event["pulled"] = True
                self.data["tasks"].append({
                    "id": _new_id(),
                    "text": event["text"],
                    "done": False,
                    "done_on": None,
                    "kind": event.get("kind", "temp"),
                    "created": time.time(),
                })
                added += 1
        if added:
            self._changed("tasks")
        return added

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
