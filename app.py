"""应用装配层：数据、监控线程、主窗口、悬浮窗、提醒弹窗的粘合与生命周期管理。"""

from __future__ import annotations

import os
import queue
import shutil
import socket
import sys
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import messagebox

import monitor
import store as store_module
import theme as T

from ui_float import FloatWindow
from ui_main import MainWindow
from ui_reminder import ReminderPopup

if getattr(sys, "frozen", False):
    # 打包成 exe 后，资源在 exe 旁边
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

APP_NAME = "ToDoList"
APP_VERSION = "1.1.0"


def _writable(folder: str) -> bool:
    """真正试写一次：有些目录存在但不可写（受控文件夹、只读安装位置等）。"""
    try:
        os.makedirs(folder, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder) as handle:
            handle.write(b"ok")
        return True
    except OSError:
        return False


def _data_dir() -> str:
    """Mac 默认 Application Support；Windows 默认 APPDATA，支持便携目录。

    逐个候选目录试写，第一个能写的就用——避免装到只读位置时任务悄悄存不上。
    """
    override = os.environ.get("TODO_LIST_DATA")
    if override:
        return os.path.dirname(os.path.abspath(os.path.expanduser(override)))
    if sys.platform == "darwin":
        folder = os.path.expanduser("~/Library/Application Support/ToDoList")
        if not _writable(folder):
            raise OSError(f"数据目录不可写：{folder}")
        return folder
    portable_marker = os.path.join(BASE_DIR, "portable.txt")
    if os.path.exists(portable_marker):
        return os.path.join(BASE_DIR, "data")
    candidates = []
    roaming = os.environ.get("APPDATA")
    if roaming:
        candidates.append(os.path.join(roaming, APP_NAME))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, APP_NAME))
    candidates.append(os.path.join(BASE_DIR, "data"))
    temp = os.environ.get("TEMP")
    if temp:
        candidates.append(os.path.join(temp, APP_NAME))
    for folder in candidates:
        if _writable(folder):
            return folder
    return os.path.join(BASE_DIR, "data")


DATA_DIR = ""
DATA_FILE = ""
LOCK_PORT = 47531


def _migrate_legacy_data() -> None:
    """旧版把数据写在 exe 旁边，搬过来，免得用户以为任务丢了。"""
    legacy = os.path.join(BASE_DIR, "data", "todo.json")
    if os.path.abspath(legacy) == os.path.abspath(DATA_FILE):
        return
    if not os.path.exists(legacy) or os.path.exists(DATA_FILE):
        return
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        shutil.copy2(legacy, DATA_FILE)
    except OSError as exc:
        raise OSError(f"旧数据迁移失败：{legacy} → {DATA_FILE}") from exc


class App:
    def __init__(self) -> None:
        global DATA_DIR, DATA_FILE
        DATA_DIR = _data_dir()
        DATA_FILE = os.path.abspath(os.path.expanduser(os.environ.get("TODO_LIST_DATA") or os.path.join(DATA_DIR, "todo.json")))
        if not _writable(os.path.dirname(DATA_FILE)):
            raise OSError(f"数据目录不可写：{os.path.dirname(DATA_FILE)}")
        _migrate_legacy_data()
        self.store = store_module.Store(DATA_FILE)
        self.snooze_until = 0.0
        self.alert_count = 0
        self.last_alert_at = 0.0
        self.status_text = "监控已暂停"
        self.status_color = T.MUTED
        self.last_status: dict = {}
        self._geometry_job = None
        self._permission_prompted = False
        self._permission_settings_error = False

        self.root = MainWindow(self)
        self.float = FloatWindow(self)
        self.reminder = ReminderPopup(self)

        self.queue: queue.Queue = queue.Queue()
        self.worker = monitor.MonitorThread(lambda: self.store.snapshot(self.snooze_until),
                                            self.queue)
        self.worker.start()

        self.store.add_listener(self._on_store_change)
        self.root.after(150, self._pump)
        self.root.after(600, self._apply_float_visibility)
        self._schedule_geometry_save()
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass
        self.refresh_status()
        if self.store.settings.get("monitor"):
            self.root.after(800, lambda: self.ensure_accessibility()
                            if self.store.settings.get("monitor") else None)

    # ------------------------------------------------------------ windows
    def show_main(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def hide_main(self) -> None:
        self.root.withdraw()

    def close_main(self) -> None:
        """关闭主窗口 ≠ 退出：收起到后台，悬浮窗与监控继续工作。"""
        if not self.store.settings.get("float_visible", True):
            self.store.set_setting("float_visible", True)
        self.hide_main()
        try:
            self.float.show()
            self.float.flash_hint("主界面已收起到后台 · 点 ▣ 可打开")
        except tk.TclError:
            pass

    def show_float(self) -> None:
        self.float.show()

    def hide_float(self) -> None:
        try:
            main_visible = bool(self.root.winfo_viewable())
        except tk.TclError:
            main_visible = True
        if not main_visible:
            # 主界面已经收起，再藏悬浮窗就一个窗口都不剩了——把主界面叫回来
            self.show_main()
            return
        self.store.set_setting("float_visible", False)

    def _apply_float_visibility(self) -> None:
        if self.store.settings.get("float_visible", True):
            self.float.show()
        else:
            self.float.hide()

    # ------------------------------------------------------------ monitor control
    def open_accessibility_settings(self) -> None:
        if sys.platform != "darwin":
            return
        try:
            subprocess.run(
                ["/usr/bin/open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
                check=True, timeout=3, capture_output=True)
            self._permission_settings_error = False
        except (OSError, subprocess.SubprocessError):
            self._permission_settings_error = True
        self.refresh_status()

    def _handle_accessibility(self, trusted: bool) -> bool:
        self.last_status["accessibility"] = trusted
        if trusted:
            self._permission_prompted = False
            self._permission_settings_error = False
        elif not self._permission_prompted:
            self._permission_prompted = True
            monitor.request_accessibility()
        self.refresh_status()
        return trusted

    def ensure_accessibility(self) -> bool:
        if sys.platform != "darwin":
            return True
        try:
            return self._handle_accessibility(monitor.accessibility_trusted())
        except RuntimeError as exc:
            messagebox.showerror("Mac 依赖未就绪", str(exc), parent=self.root)
            return False

    def set_monitor(self, enabled: bool) -> None:
        if enabled:
            self.ensure_accessibility()
        self.store.set_setting("monitor", bool(enabled))
        if enabled:
            self.snooze_until = 0.0
        self.refresh_status()

    def toggle_monitor(self) -> None:
        self.set_monitor(not bool(self.store.settings.get("monitor")))

    def snooze(self, seconds: int) -> None:
        self.snooze_until = time.time() + max(0, int(seconds))
        self.refresh_status()

    # ------------------------------------------------------------ status
    def refresh_status(self) -> None:
        settings = self.store.settings
        enabled = bool(settings.get("monitor"))
        if self._permission_settings_error:
            text, color = "设置打开失败 · 请从“监控权限”重试或手动打开辅助功能", T.AMBER
        elif self._permission_prompted:
            text, color = "请在辅助功能中授权 Python 或启动它的终端", T.AMBER
        elif not enabled:
            text, color = "监控已暂停", T.MUTED
        elif self.last_status.get("error"):
            text, color = "监控采样失败 · 请重新启动监控", T.RED
        elif self.last_status.get("accessibility") is False:
            text, color = "软件检测中 · 网站检测需要辅助功能权限", T.AMBER
        elif self.last_status.get("sampling_errors"):
            text, color = f"监控中 · 部分窗口读取失败（{self.last_status['sampling_errors']}）", T.AMBER
        elif time.time() < self.snooze_until:
            left = int(self.snooze_until - time.time())
            text, color = f"提醒暂停 {max(1, left // 60 + 1)} 分钟", T.AMBER
        else:
            windows = int(self.last_status.get("windows") or 0)
            text, color = f"监控中 · 正在检测 {windows} 个窗口", T.GREEN
        if self.store.last_error:
            text, color = "保存失败 · 请检查数据目录写入权限", T.RED
        signature = (text, color, enabled)
        if signature == getattr(self, "_status_signature", None):
            return
        self._status_signature = signature
        self.status_text, self.status_color = text, color
        root = getattr(self, "root", None)
        if root is not None:
            try:
                root.apply_status(text, color, enabled)
            except tk.TclError:
                pass
        float_window = getattr(self, "float", None)
        if float_window is not None:
            try:
                float_window.apply_status(text, color, enabled)
            except tk.TclError:
                pass

    # ------------------------------------------------------------ monitor events
    def _pump(self) -> None:
        try:
            while True:
                try:
                    kind, payload, stamp = self.queue.get_nowait()
                except queue.Empty:
                    break
                if kind == "status":
                    self.last_status = payload
                    if (sys.platform == "darwin" and payload.get("enabled")
                            and self.store.settings.get("monitor")
                            and isinstance(payload.get("accessibility"), bool)):
                        self._handle_accessibility(payload["accessibility"])
                elif kind == "alerts":
                    self._on_alerts(payload, stamp)
                elif kind == "show":
                    self.show_main()
                elif kind == "error":
                    self.last_status = {"error": payload.get("message")}
                    print(f"[monitor] {payload.get('message')}")
        finally:
            self.refresh_status()
            self.root.after(200, self._pump)

    def _on_alerts(self, alerts: list, stamp: float) -> None:
        if not alerts:
            return
        if not self.store.settings.get("monitor"):
            return
        if stamp < self.snooze_until:
            return
        self.alert_count += len(alerts)
        self.last_alert_at = stamp
        try:
            self.reminder.show(alerts)
        except tk.TclError:
            pass
        try:
            self.float.apply_status(self.status_text, self.status_color,
                                    bool(self.store.settings.get("monitor")))
        except tk.TclError:
            pass

    # ------------------------------------------------------------ store events
    def _on_store_change(self, reason: str) -> None:
        try:
            if reason == "tasks_text":
                self.float.refresh_rows()
                return
            if reason in ("tasks", "targets", "settings"):
                self.root.refresh_all()
                self.float.refresh_rows()
                self.float.refresh_monitor()
                self.float.refresh_pin()
                self.refresh_status()
            if reason == "settings":
                self._apply_float_visibility()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ persistence extras
    def _schedule_geometry_save(self) -> None:
        def _save():
            try:
                geometry = self.root.geometry()
                if self.root.state() == "normal":
                    self.store.set_setting("main_geometry", geometry, notify=False)
            except tk.TclError:
                return
            self._geometry_job = self.root.after(5000, _save)

        self._geometry_job = self.root.after(5000, _save)

    # ------------------------------------------------------------ teardown
    def quit(self) -> None:
        try:
            if self._geometry_job is not None:
                self.root.after_cancel(self._geometry_job)
        except tk.TclError:
            pass
        try:
            if self.root.state() == "normal":
                self.store.set_setting("main_geometry", self.root.geometry(), notify=False)
        except tk.TclError:
            pass
        try:
            self.worker.stop()
        except Exception:
            pass
        self.store.save()
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def _serve_show_requests(on_show) -> socket.socket | None:
    """Single-instance guard: a second launch brings the first window forward."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", LOCK_PORT))
        server.listen(4)
    except OSError:
        return None

    def _loop():
        while True:
            try:
                client, _addr = server.accept()
                with client:
                    client.settimeout(1.0)
                    try:
                        client.recv(16)
                    except OSError:
                        pass
            except OSError:
                return
            try:
                on_show()
            except Exception:
                pass

    threading.Thread(target=_loop, daemon=True, name="show-request").start()
    return server


def _notify_existing() -> bool:
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.settimeout(1.0)
    try:
        client.connect(("127.0.0.1", LOCK_PORT))
        client.sendall(b"show")
        return True
    except OSError:
        return False
    finally:
        try:
            client.close()
        except OSError:
            pass


def main() -> int:
    if _notify_existing():
        return 0
    try:
        app = App()
    except (OSError, RuntimeError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1
    _serve_show_requests(lambda: app.queue.put(("show", {}, time.time())))
    try:
        app.root.mainloop()
    except KeyboardInterrupt:
        app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
