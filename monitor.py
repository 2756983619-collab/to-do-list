"""Win32 检测层：枚举窗口 / 进程，判断被监控目标是否“正在运行”。

设计要点
--------
* 软件目标 -> 进程是否在运行（同时兼容只有窗口标题可识别的新式应用）。
* 网站目标 -> 是否存在**未最小化**的浏览器窗口，其当前标签页标题包含关键词。
* 所有检测都是只读的，不注入、不联网，只使用 user32 / kernel32。
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    dwmapi = ctypes.WinDLL("dwmapi")
except OSError:  # pragma: no cover - only on ancient Windows
    dwmapi = None

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14

BROWSER_EXES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe",
    "chromium.exe", "iexplore.exe", "360se.exe", "360chrome.exe", "qqbrowser.exe",
    "sogouexplorer.exe", "arc.exe", "zen.exe", "librewolf.exe", "waterfox.exe",
}

# 这些系统进程不该出现在“选择监控程序”的列表里
SYSTEM_EXES = {
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe",
    "conhost.exe", "runtimebroker.exe", "shellexperiencehost.exe", "searchhost.exe",
    "taskhostw.exe", "ctfmon.exe", "audiodg.exe", "spoolsv.exe", "wudfhost.exe",
}

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _declare() -> None:
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetShellWindow.restype = wintypes.HWND
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL


_declare()


# ---------------------------------------------------------------- low level helpers
def process_exe(pid: int) -> str:
    """Return the lower-case executable name of *pid* ('' when unavailable)."""
    if not pid:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value).lower()
    finally:
        kernel32.CloseHandle(handle)
    return ""


def _is_cloaked(hwnd: int) -> bool:
    if dwmapi is None:
        return False
    value = ctypes.c_int(0)
    try:
        result = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        return False
    return result == 0 and value.value != 0


def list_processes() -> set[str]:
    """Lower-case executable names of every running process."""
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        return set()
    names: set[str] = set()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            names.add(entry.szExeFile.lower())
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def list_windows() -> list[dict]:
    """Visible, titled, non-tool windows with their owning process."""
    windows: list[dict] = []
    exe_cache: dict[int, str] = {}
    shell = user32.GetShellWindow()

    def _callback(hwnd, _lparam):
        try:
            if hwnd == shell:
                return True
            if not user32.IsWindowVisible(hwnd):
                return True
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if ex_style & WS_EX_TOOLWINDOW:
                return True
            if _is_cloaked(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            if not title:
                return True
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid_value = int(pid.value)
            if pid_value not in exe_cache:
                exe_cache[pid_value] = process_exe(pid_value)
            windows.append({
                "hwnd": int(hwnd),
                "pid": pid_value,
                "exe": exe_cache[pid_value],
                "title": title,
                "minimized": bool(user32.IsIconic(hwnd)),
            })
        except Exception:
            return True
        return True

    user32.EnumWindows(WNDENUMPROC(_callback), 0)
    return windows


def sample_system_state() -> dict:
    """One consistent read of what is currently running."""
    foreground = int(user32.GetForegroundWindow() or 0)
    return {
        "windows": list_windows(),
        "processes": list_processes(),
        "foreground": foreground,
    }


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
        if window.get("minimized"):
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
        self._stop = threading.Event()
        self._prev: dict = {}
        self._last_alert: dict = {}

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(self.poll):
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
            {"enabled": True, "windows": len(state.get("windows") or ()), "snoozed": snoozed},
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
        if exe in listed or exe in SYSTEM_EXES or not exe.endswith(".exe"):
            continue
        items.append({"exe": exe, "title": "", "has_window": False})
        if len(items) >= limit:
            break
    items.sort(key=lambda item: (not item["has_window"], item["title"].lower() or item["exe"]))
    return items
