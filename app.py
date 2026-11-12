"""应用装配层：数据、监控线程、主窗口、悬浮窗、提醒弹窗的粘合与生命周期管理。"""

from __future__ import annotations

import os
import queue
import shutil
import socket
import sys
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox

import monitor
import store as store_module
import theme as T

try:  # 托盘只有 Windows 有；macOS 上即便导入失败也不该连累启动
    import tray as tray_module
except Exception:  # pragma: no cover - 只可能在非 Windows 上触发
    tray_module = None

from ui_float import FloatWindow
from ui_main import MainWindow
from ui_reminder import ReminderPopup

if getattr(sys, "frozen", False):
    # 打包成 exe 后，资源在 exe 旁边
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

APP_NAME = "ToDoList"
APP_VERSION = "1.4.1"


def _writable(folder: str) -> bool:
    """真正试写一次：有些目录存在但不可写（受控文件夹、只读安装位置等）。

    这里故意不用 tempfile：打包成 exe 后 tempfile 在个别机器上会陷在
    _mkstemp_inner 的重试循环里出不来（表现为启动即卡死、CPU 100%）。
    """
    probe = os.path.join(folder, f".write-probe-{os.getpid()}")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(probe, "wb") as handle:
            handle.write(b"ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _data_dir() -> str:
    """数据放在程序自己所在的目录下（`data\\`），跟着安装位置走。

    这样用户把程序装到 D 盘，任务数据也在 D 盘，不会跑到 C 盘再占一份空间。
    只有程序装到了不可写的位置（例如 Program Files）时，才退回用户目录，
    避免出现"任务存不上"的静默失败。
    """
    override = os.environ.get("TODO_LIST_DATA")
    if override:
        return os.path.dirname(os.path.abspath(os.path.expanduser(override)))
    if sys.platform == "darwin":
        folder = os.path.expanduser("~/Library/Application Support/ToDoList")
        if not _writable(folder):
            raise OSError(f"数据目录不可写：{folder}")
        return folder
    install_dir = os.path.join(BASE_DIR, "data")
    if _writable(install_dir):
        return install_dir
    candidates = []
    roaming = os.environ.get("APPDATA")
    if roaming:
        candidates.append(os.path.join(roaming, APP_NAME))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, APP_NAME))
    temp = os.environ.get("TEMP")
    if temp:
        candidates.append(os.path.join(temp, APP_NAME))
    for folder in candidates:
        if _writable(folder):
            return folder
    return install_dir


DATA_DIR = ""
DATA_FILE = ""
LOCK_PORT = 47531


def _migrate_legacy_data() -> None:
    """把旧版本留在别处（exe 旁边或 %APPDATA%）的任务搬过来，免得用户以为任务丢了。"""
    if os.environ.get("TODO_LIST_DATA"):
        # 显式指定数据文件（测试、脚本）时不去动用户的历史数据
        return
    if os.path.exists(DATA_FILE):
        return
    candidates = []
    for env_name in ("APPDATA", "LOCALAPPDATA"):
        root = os.environ.get(env_name)
        if root:
            candidates.append(os.path.join(root, APP_NAME, "todo.json"))
    candidates.append(os.path.join(BASE_DIR, "data", "todo.json"))

    target = os.path.abspath(DATA_FILE)
    newest = None
    for path in candidates:
        if os.path.abspath(path) == target or not os.path.exists(path):
            continue
        if newest is None or os.path.getmtime(path) > os.path.getmtime(newest):
            newest = path
    if newest is None:
        return
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        shutil.copy2(newest, DATA_FILE)
    except OSError as exc:
        raise OSError(f"旧数据迁移失败：{newest} → {DATA_FILE}") from exc


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
        self._day = time.strftime("%Y-%m-%d")

        self.root = MainWindow(self)
        self.float = FloatWindow(self)
        self.reminder = ReminderPopup(self)

        self.queue: queue.Queue = queue.Queue()
        self.worker = monitor.MonitorThread(lambda: self.store.snapshot(self.snooze_until),
                                            self.queue)
        self.worker.start()

        self.tray: tray_module.TrayIcon | None = None
        if sys.platform == "win32" and tray_module is not None:
            try:
                tray = tray_module.TrayIcon(
                    tray_module.tray_icon_path(BASE_DIR),
                    "To-Do List · 专注清单",
                    lambda action: self.queue.put(("tray", {"action": action}, time.time())),
                    state_provider=self._tray_state,
                    log_path=os.path.join(DATA_DIR, "tray.log"),
                )
                # 注意：start() 返回 False 只表示"这一秒还没挂上"，后台看门狗会继续重试，
                # 所以对象要留着——退出时才能把图标摘掉，菜单也还能用。
                tray.start()
                self.tray = tray
                self._announce_tray()
            except Exception as exc:  # 托盘失败不能连累主程序
                try:
                    tray_module._log(f"未启用托盘：{exc!r}")
                except Exception:
                    pass
                self.tray = None
        elif sys.platform == "darwin":
            # macOS 没有通知区域，用 Dock 图标：点 Dock 就把主界面叫回来
            try:
                self.root.createcommand("::tk::mac::ReopenApplication", self.show_main)
            except Exception:
                pass

        self.store.add_listener(self._on_store_change)
        self._pump_job = self.root.after(150, self._pump)
        self._visibility_job = self.root.after(600, self._apply_float_visibility)
        # 启动先滚动一次：昨天勾掉的临时任务清掉、每日任务复位、到期的日历事项入清单
        self.sync_calendar(quiet=True)
        self._day_job = self.root.after(60000, self._check_day)
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
        """关闭主窗口 ≠ 退出：收起到后台，悬浮窗、监控、托盘图标继续工作。

        如果用户已经把悬浮窗收起来了，这里**不再**把它叫出来：只留托盘图标，
        程序继续在后台跑，用户可以从托盘唤回主界面或退出。
        """
        float_visible = bool(self.store.settings.get("float_visible", True))
        if self.tray_ready():
            self.hide_main()
            hint = "主界面已收起到后台 · 点 ▣ 或托盘图标可打开"
        else:
            # 没有托盘图标就别把主界面藏得找不到——最小化到任务栏
            try:
                self.root.iconify()
            except tk.TclError:
                self.hide_main()
            hint = "主界面已最小化 · 点任务栏按钮可打开（托盘图标没挂上）"
        if float_visible:
            try:
                self.float.show()
                self.float.flash_hint(hint, 6000)
            except tk.TclError:
                pass
        elif self.tray_ready():
            try:
                self.tray.show_balloon("To-Do List 仍在后台运行",
                                       "右键托盘图标可以打开主界面或退出程序。")
            except Exception:
                pass

    def show_float(self) -> None:
        self.float.show()

    def hide_float(self) -> None:
        try:
            main_visible = bool(self.root.winfo_viewable())
        except tk.TclError:
            main_visible = True
        if not main_visible:
            # 主界面已经收起，再藏悬浮窗就一个窗口都不剩了——把主界面叫回来。
            # 托盘菜单里的"显示悬浮窗"不走这里，可以只留托盘图标在后台跑。
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
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.set_tooltip(f"To-Do List · {text}")

    # ------------------------------------------------------------ 托盘图标
    def _tray_state(self) -> dict:
        """给托盘菜单算勾选状态（在托盘线程里调用，只读 settings 字典）。"""
        settings = self.store.settings
        return {
            "monitor": bool(settings.get("monitor")),
            "float_visible": bool(settings.get("float_visible", True)),
        }

    def _on_tray(self, action: str | None) -> None:
        if action == "show_main":
            self.show_main()
        elif action == "toggle_float":
            self.store.set_setting("float_visible",
                                   not bool(self.store.settings.get("float_visible", True)))
        elif action == "toggle_monitor":
            self.toggle_monitor()
        elif action == "quit":
            self.quit()

    def _announce_tray(self) -> None:
        """第一次挂上托盘图标时提一句，免得用户在任务栏上找不到。"""
        tray = self.tray
        if tray is None or self.store.settings.get("tray_hint_shown"):
            return

        def hint() -> None:
            if self.store.settings.get("tray_hint_shown"):
                return
            if not getattr(tray, "added", False):
                # 还没挂上：告诉用户"没图标"不是错觉，并给出可操作的办法
                self._tray_failed_hint = True
                if self.store.settings.get("float_visible"):
                    self.float.flash_hint(
                        "托盘图标没能挂上（系统拒绝）· 关主界面会最小化到任务栏，"
                        "悬浮窗右键可重挂", 7000)
                self.store.set_setting("tray_hint_shown", True)
                return
            if getattr(tray, "promoted", False):
                # 已经钉在任务栏上了，不用教用户在哪儿找
                self.store.set_setting("tray_hint_shown", True)
                return
            tray.show_balloon(
                "To-Do List 已在后台运行",
                "图标在右下角通知区；Windows 11 可能把它收进了 ^ 溢出区，"
                "拖到任务栏上就会一直显示。右键图标可以打开窗口或退出。",
                tray_module.NIIF_INFO,
            )
            # 气泡通知在部分系统上会被静默丢掉，悬浮窗里再提示一次
            if self.float is not None and self.store.settings.get("float_visible"):
                self.float.flash_hint("托盘图标可能在 ^ 溢出区里 · 拖到任务栏就会常驻", 5200)
            self.store.set_setting("tray_hint_shown", True)

        # 等一会儿再判断：图标常驻任务栏的设置是异步写的
        self._tray_job = self.root.after(3000, hint)

    def tray_ready(self) -> bool:
        """托盘图标此刻是否真的挂在通知区里。"""
        return bool(getattr(self.tray, "added", False))

    def retry_tray(self) -> None:
        """手动重挂托盘图标（悬浮窗右键菜单用）。"""
        tray = self.tray
        if tray is None:
            self.toast_main("这个系统没有托盘图标（macOS 请用 Dock 图标）")
            return
        try:
            tray.retry()
        except Exception:
            pass
        self.toast_main("已尝试重新挂上托盘图标，详情见 data\\tray.log")

    def toast_main(self, message: str) -> None:
        try:
            self.root.toast(message)
        except Exception:
            pass

    # ------------------------------------------------------------ 日期 / 日历
    def sync_calendar(self, quiet: bool = False) -> None:
        """跨天滚动 + 把到期（含过期）的日历事项变成任务。"""
        try:
            result = self.store.rollover()
            added = self.store.pull_due_events()
        except Exception as exc:  # 日历出错不能连累主程序
            print(f"[calendar] sync failed: {exc!r}")
            return
        parts = []
        if result.get("reset"):
            parts.append(f"{result['reset']} 个每日任务已重置")
        if result.get("removed"):
            parts.append(f"{result['removed']} 个临时任务已清除")
        if added:
            parts.append(f"{added} 个日历事项已加入清单")
        if not parts:
            return
        message = " · ".join(parts)
        if not quiet:
            self.toast_main(message)
        if self.store.settings.get("float_visible"):
            try:
                self.float.flash_hint(message, 5000)
            except tk.TclError:
                pass

    def _check_day(self) -> None:
        """每分钟看一眼是否跨天；跨天就滚动任务、拉取日历事项。"""
        today = time.strftime("%Y-%m-%d")
        if today != getattr(self, "_day", today):
            self._day = today
            self.sync_calendar()
        try:
            self._day_job = self.root.after(60000, self._check_day)
        except tk.TclError:
            self._day_job = None

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
                elif kind == "tray":
                    self._on_tray(payload.get("action"))
                elif kind == "error":
                    self.last_status = {"error": payload.get("message")}
                    print(f"[monitor] {payload.get('message')}")
        finally:
            self.refresh_status()
            self._pump_job = self.root.after(200, self._pump)

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
            if reason in ("tasks", "targets", "settings", "events"):
                self.root.refresh_all()
                self.float.refresh_rows()
                self.float.refresh_monitor()
                self.float.refresh_pin()
                self.float.refresh_collapsed()
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
        for job in (getattr(self, "_geometry_job", None), getattr(self, "_pump_job", None),
                    getattr(self, "_visibility_job", None), getattr(self, "_tray_job", None),
                    getattr(self, "_day_job", None)):
            try:
                if job is not None:
                    self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._geometry_job = None
        self._pump_job = None
        self._visibility_job = None
        self._tray_job = None
        self._day_job = None
        try:
            if self.root.state() == "normal":
                self.store.set_setting("main_geometry", self.root.geometry(), notify=False)
        except tk.TclError:
            pass
        try:
            self.worker.stop()
        except Exception:
            pass
        try:
            if self.tray is not None:
                self.tray.stop()
                self.tray = None
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
