"""跨平台桌面监控、匹配规则与后台提醒调度。"""

from __future__ import annotations

import sys
import threading
import time

if sys.platform == "win32":
    import monitor_windows as backend
elif sys.platform == "darwin":
    import monitor_macos as backend
else:
    raise RuntimeError("ToDoList supports Windows and macOS")

BROWSER_EXES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe",
    "chromium.exe", "iexplore.exe", "360se.exe", "360chrome.exe", "qqbrowser.exe",
    "sogouexplorer.exe", "arc.exe", "zen.exe", "librewolf.exe", "waterfox.exe",
}
if sys.platform == "darwin":
    BROWSER_EXES |= backend.BROWSER_EXES
SYSTEM_EXES = backend.SYSTEM_EXES
process_exe = backend.process_exe
list_processes = backend.list_processes
list_windows = backend.list_windows
sample_system_state = backend.sample_system_state


def accessibility_trusted() -> bool:
    return getattr(backend, "accessibility_trusted", lambda: True)()


def request_accessibility() -> None:
    getattr(backend, "request_accessibility", lambda: None)()


# ---------------------------------------------------------------- matching rules
def normalize_match(kind: str, match: str) -> str:
    text = (match or "").strip().lower()
    if kind == "software":
        if text.endswith(".exe"):
            text = text[:-4]
        return text
    for prefix in ("https://", "http://", "ftp://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if text.startswith("www."):
        text = text[4:]
    if "/" in text and "." in text.split("/")[0]:
        text = text.split("/")[0]          # 粘贴完整网址时只保留域名
    for marker in ("?", "#"):
        if marker in text:
            text = text.split(marker)[0]
    return text.rstrip("/")


def detect_target(target: dict, state: dict) -> tuple[bool, str]:
    """Return (running, evidence) for one monitor target."""
    kind = target.get("kind", "software")
    key = normalize_match(kind, target.get("match", ""))
    if not key:
        return False, ""

    if kind == "software":
        for exe in state.get("processes") or ():
            stem = exe[:-4] if exe.endswith(".exe") else exe
            if exe == key or stem == key:
                return True, exe
        # 新版商店应用（进程名固定为 ApplicationFrameHost.exe 等）只能靠标题识别
        if len(key) >= 3:
            for window in state.get("windows") or ():
                if key in (window.get("title") or "").lower():
                    return True, window.get("title", "")
        return False, ""

    # website: 需要未最小化的浏览器窗口，当前标签页标题包含关键词
    for window in state.get("windows") or ():
        if window.get("exe") not in BROWSER_EXES:
            continue
        if window.get("minimized") or window.get("hidden"):
            continue
        title = (window.get("title") or "").lower()
        if key in title:
            return True, window.get("title", "")
    return False, ""


def evaluate(targets, state: dict, now: float, prev_state: dict, last_alert: dict,
             repeat: bool = True, interval: int = 90) -> list[dict]:
    """Pure function: decide which enabled targets should raise an alert now."""
    alerts: list[dict] = []
    active = set()
    for target in targets:
        if not target.get("enabled", True):
            continue
        target_id = target.get("id")
        active.add(target_id)
        running, evidence = detect_target(target, state)
        was_running = bool(prev_state.get(target_id, False))
        prev_state[target_id] = running
        if not running:
            continue
        last = float(last_alert.get(target_id, 0.0))
        if (not was_running) or (repeat and now - last >= interval):
            last_alert[target_id] = now
            alerts.append({
                "id": target_id,
                "kind": target.get("kind"),
                "name": target.get("name") or target.get("match"),
                "match": target.get("match"),
                "detail": evidence,
            })
    for target_id in [key for key in prev_state if key not in active]:
        prev_state.pop(target_id, None)
    for target_id in [key for key in last_alert if key not in active]:
        last_alert.pop(target_id, None)
    return alerts


# ---------------------------------------------------------------- background worker
class MonitorThread(threading.Thread):
    """Polls the desktop and pushes ('status'|'alerts'|'error', payload, ts) into a queue."""

    def __init__(self, provider, out_queue, sampler=None, poll: float = 1.2):
        super().__init__(daemon=True, name="todo-monitor")
        self.provider = provider
        self.queue = out_queue
        self.sampler = sampler or sample_system_state
        self.poll = poll
        self._stop_event = threading.Event()
        self._prev: dict = {}
        self._last_alert: dict = {}

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.wait(self.poll):
            try:
                self.tick()
            except Exception as exc:  # keep the thread alive whatever happens
                self.queue.put(("error", {"message": repr(exc)}, time.time()))

    def tick(self) -> None:
        snapshot = self.provider() or {}
        now = time.time()
        if not snapshot.get("enabled"):
            self._prev.clear()
            self._last_alert.clear()
            self.queue.put(("status", {"enabled": False, "windows": 0, "snoozed": False}, now))
            return

        state = self.sampler()
        snoozed = now < float(snapshot.get("snooze_until") or 0.0)
        alerts: list[dict] = []
        if snoozed:
            # 静默观察：保持状态新鲜，但不提醒
            for target in snapshot.get("targets") or []:
                if target.get("enabled", True):
                    self._prev[target.get("id")] = detect_target(target, state)[0]
        else:
            alerts = evaluate(
                snapshot.get("targets") or [], state, now, self._prev, self._last_alert,
                repeat=bool(snapshot.get("repeat", True)),
                interval=max(5, int(snapshot.get("interval") or 90)),
            )

        self.queue.put((
            "status",
            {"enabled": True, "windows": len(state.get("windows") or ()), "snoozed": snoozed,
             "accessibility": state.get("accessibility", True), "sampling_errors": state.get("sampling_errors", 0)},
            now,
        ))
        if alerts:
            self.queue.put(("alerts", alerts, now))


# ---------------------------------------------------------------- helper for the picker UI
def running_programs(limit: int = 400) -> list[dict]:
    """Snapshot of processes for the 'pick a program' dialog (windowed apps first)."""
    windows = list_windows()
    title_by_exe: dict[str, str] = {}
    for window in windows:
        exe = window.get("exe") or ""
        if not exe or exe in SYSTEM_EXES:
            continue
        current = title_by_exe.get(exe)
        title = window.get("title") or ""
        if current is None or len(title) > len(current):
            title_by_exe[exe] = title
    items = [{"exe": exe, "title": title, "has_window": True}
             for exe, title in title_by_exe.items()]
    listed = {item["exe"] for item in items}
    for exe in sorted(list_processes()):
        if exe in listed or exe in SYSTEM_EXES or (sys.platform == "win32" and not exe.endswith(".exe")):
            continue
        items.append({"exe": exe, "title": "", "has_window": False})
        if len(items) >= limit:
            break
    items.sort(key=lambda item: (not item["has_window"], item["title"].lower() or item["exe"]))
    return items
