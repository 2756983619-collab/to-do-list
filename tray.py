"""Windows 通知区域（托盘）图标。

像微信 / Steam 那样常驻右下角：左键点开主界面，右键出菜单（打开主界面、显示或隐藏
悬浮窗、开始或暂停监控、退出程序）。

只用 ctypes 调 user32 / shell32，不引入第三方依赖；注意在 Tk 主线程之外的地方回调
命令——所以这里只往队列里塞指令，真正的动作由 App._pump 在主线程里执行。

Windows 11 会把新出现的图标塞进任务栏的溢出区（`^`），用户看不到就以为程序没启动。
所以挂上图标之后会顺手把「在任务栏显示」的偏好写进注册表
（`HKCU\\Control Panel\\NotifyIconSettings\\<hash>\\IsPromoted = 1`），并（可选）弹一个
气泡提示，告诉用户图标在哪儿。
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes

WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_CONTEXTMENU = 0x007B
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 1

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIIF_INFO = 0x00000001
NIIF_NOSOUND = 0x00000010

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_CHECKED = 0x00000008
MF_POPUP = 0x00000010
TPM_LEFTALIGN = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
SM_CXSMICON = 49
IDI_APPLICATION = 32512

# 菜单项 id -> 动作名（动作名交给 App 执行）
MENU_ACTIONS = {
    1: "show_main",
    2: "toggle_float",
    3: "toggle_monitor",
    4: "quit",
}

NOTIFY_SETTINGS_KEY = r"Control Panel\NotifyIconSettings"

_log_path: str | None = None


def set_log_path(path: str | None) -> None:
    """把托盘相关的异常写到文件里——窗口模式下 stderr 是 None，报错会丢。"""
    global _log_path
    _log_path = path


def _log(message: str) -> None:
    line = f"[tray] {message}"
    stream = sys.stderr
    if stream is not None:
        try:
            print(line, file=stream, flush=True)
        except Exception:
            pass
    if not _log_path:
        return
    try:
        folder = os.path.dirname(_log_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        if os.path.exists(_log_path) and os.path.getsize(_log_path) > 64 * 1024:
            os.replace(_log_path, _log_path + ".old")
        with open(_log_path, "a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
    except OSError:
        pass


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_byte * 16)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


WNDPROC = (ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)
           if hasattr(ctypes, "WINFUNCTYPE") else None)
LRESULT = ctypes.c_ssize_t


def candidate_icon_paths(base_dir: str) -> list[str]:
    """图标可能在 exe 旁边，也可能被打进 PyInstaller 的 _internal 目录。"""
    paths = []
    meipass = getattr(sys, "_MEIPASS", None)
    for root in (meipass, base_dir, os.path.dirname(os.path.abspath(sys.executable))):
        if root:
            paths.append(os.path.join(root, "assets", "icon.ico"))
    seen = set()
    unique = []
    for path in paths:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            unique.append(os.path.abspath(path))
    return unique


def tray_icon_path(base_dir: str) -> str:
    """返回第一个存在的图标文件；都不存在就返回第一个候选（调用方会回退到 exe 图标）。"""
    candidates = candidate_icon_paths(base_dir)
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0] if candidates else ""


def promote_in_taskbar(exe_path: str | None = None) -> str:
    """让 Windows 11 把图标显示在任务栏通知区，而不是藏进 `^` 溢出区。

    Windows 会在第一次挂上图标后于 `HKCU\\Control Panel\\NotifyIconSettings` 下建一个
    子键，里面记录 ExecutablePath 与 IsPromoted。把 IsPromoted 设成 1，图标就会常驻
    任务栏。

    返回 "ok"（已常驻）/ "missing"（系统还没登记，可稍后重试）/ "denied"（没权限改，
    重试也没用）/ "skip"（非 Windows）。
    """
    if sys.platform != "win32":
        return "skip"
    try:
        import winreg
    except ImportError:
        return "skip"
    target = os.path.normcase(os.path.abspath(exe_path or sys.executable))
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY_SETTINGS_KEY)
    except OSError:
        return "missing"
    try:
        count = winreg.QueryInfoKey(root)[0]
        for index in range(count):
            try:
                name = winreg.EnumKey(root, index)
                sub = winreg.OpenKey(root, name)
            except OSError:
                continue
            try:
                try:
                    stored, _type = winreg.QueryValueEx(sub, "ExecutablePath")
                except OSError:
                    continue
                stored_text = str(stored)
                if not stored_text:
                    continue
                matches = (os.path.normcase(os.path.abspath(stored_text)) == target
                           or os.path.normcase(os.path.basename(stored_text))
                           == os.path.normcase(os.path.basename(target)))
                if not matches:
                    continue
                try:
                    current, _t = winreg.QueryValueEx(sub, "IsPromoted")
                except OSError:
                    current = 0
                if current == 1:
                    return "ok"
                try:
                    winreg.SetValueEx(sub, "IsPromoted", 0, winreg.REG_DWORD, 1)
                except PermissionError as exc:
                    _log(f"没有权限把图标设为常驻（{exc.__class__.__name__}），"
                         "图标会在 ^ 溢出区里")
                    return "denied"
                except OSError as exc:
                    _log(f"写入 IsPromoted 失败：{exc!r}")
                    return "denied"
                _log("已把图标设为常驻任务栏")
                return "ok"
            finally:
                winreg.CloseKey(sub)
    finally:
        winreg.CloseKey(root)
    return "missing"


class TrayIcon:
    """常驻通知区域的图标。非 Windows 平台上 start()/stop() 都是空操作。"""

    def __init__(self, icon_path: str, tooltip: str, on_command, state_provider=None,
                 log_path: str | None = None):
        self.icon_path = icon_path
        self.tooltip = tooltip[:120]
        self.on_command = on_command
        self.state_provider = state_provider or (lambda: {})
        self.available = sys.platform == "win32"
        self.added = False
        self.promoted = False
        self.hwnd = None
        self.last_error = ""
        self._thread: threading.Thread | None = None
        self._icon = None
        self._wndproc = None
        self._class_atom = None
        self._taskbar_created = 0
        self._user32 = None
        self._shell32 = None
        self._kernel32 = None
        self._lock = threading.Lock()
        if log_path:
            set_log_path(log_path)

    # ------------------------------------------------------------ public API
    def start(self) -> bool:
        if not self.available:
            return False
        self._thread = threading.Thread(target=self._run, daemon=True, name="tray-icon")
        self._thread.start()
        for _ in range(60):  # 最多等 1.2 秒，别卡住界面
            if self.added or not self._thread.is_alive():
                break
            self._thread.join(0.02)
        return self.added

    def stop(self) -> None:
        if not self.available or self.hwnd is None:
            return
        try:
            self._user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        except Exception:
            pass
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)

    def set_tooltip(self, text: str) -> None:
        text = (text or "")[:120]
        if text == self.tooltip:
            return
        self.tooltip = text
        with self._lock:
            if not self.added:
                return
            data = self._notify_data()
            data.uFlags = NIF_TIP
            data.szTip = text
            try:
                self._shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))
            except Exception:
                pass

    def show_balloon(self, title: str, text: str, info_flags: int = NIIF_INFO) -> bool:
        """弹一个气泡：告诉用户"我在后台，图标在右下角"。"""
        with self._lock:
            if not self.added:
                return False
            data = self._notify_data()
            data.uFlags = NIF_INFO
            data.szInfoTitle = (title or "")[:63]
            data.szInfo = (text or "")[:255]
            data.dwInfoFlags = info_flags
            try:
                return bool(self._shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data)))
            except Exception as exc:
                _log(f"气泡提示失败：{exc!r}")
                return False

    def ensure_promoted(self, attempts: int = 12, delay: float = 0.5) -> bool:
        """把图标提到任务栏（Windows 11 默认把它收进溢出区）。后台重试，别堵住消息循环。"""
        def worker() -> None:
            for index in range(max(1, attempts)):
                result = promote_in_taskbar(sys.executable)
                if result == "ok":
                    self.promoted = True
                    return
                if result == "denied":
                    return
                if index + 1 < attempts:
                    time.sleep(delay)

        if self.promoted:
            return True
        threading.Thread(target=worker, daemon=True, name="tray-promote").start()
        return False

    # ------------------------------------------------------------ internals
    def _run(self) -> None:
        try:
            self._user32 = ctypes.windll.user32
            self._shell32 = ctypes.windll.shell32
            self._kernel32 = ctypes.windll.kernel32
            self._declare_prototypes()
            self._create_window()
            self._add_icon()
            if not self.added:
                _log(f"挂载图标失败：{self.last_error}")
            self._message_loop()
        except Exception:
            self.added = False
            _log("初始化失败：\n" + traceback.format_exc())
        finally:
            self._cleanup()

    def _declare_prototypes(self) -> None:
        """声明参数类型：LPARAM 在 64 位上是有符号的，不声明会溢出报错。"""
        u, sh, k = self._user32, self._shell32, self._kernel32
        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                     wintypes.LPARAM]
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        u.RegisterClassW.restype = wintypes.WORD
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                      wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                      wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        u.CreateWindowExW.restype = wintypes.HWND
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.DestroyWindow.restype = wintypes.BOOL
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                  wintypes.UINT, wintypes.UINT]
        u.GetMessageW.restype = ctypes.c_int
        u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.restype = ctypes.c_ssize_t
        u.PostQuitMessage.argtypes = [ctypes.c_int]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                   wintypes.LPARAM]
        u.PostMessageW.restype = wintypes.BOOL
        u.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        u.RegisterWindowMessageW.restype = wintypes.UINT
        u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.LoadImageW.restype = wintypes.HANDLE
        u.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
        u.LoadIconW.restype = wintypes.HANDLE
        u.DestroyIcon.argtypes = [wintypes.HANDLE]
        u.DestroyIcon.restype = wintypes.BOOL
        u.GetSystemMetrics.argtypes = [ctypes.c_int]
        u.GetSystemMetrics.restype = ctypes.c_int
        u.CreatePopupMenu.restype = wintypes.HMENU
        u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t,
                                  wintypes.LPCWSTR]
        u.AppendMenuW.restype = wintypes.BOOL
        u.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                     wintypes.LPVOID]
        u.TrackPopupMenu.restype = ctypes.c_int
        u.DestroyMenu.argtypes = [wintypes.HMENU]
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u.GetCursorPos.restype = wintypes.BOOL
        sh.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                      ctypes.POINTER(wintypes.HICON),
                                      ctypes.POINTER(wintypes.HICON), wintypes.UINT]
        sh.ExtractIconExW.restype = wintypes.UINT
        k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        k.GetModuleHandleW.restype = wintypes.HMODULE
        sh.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        sh.Shell_NotifyIconW.restype = wintypes.BOOL

    def _create_window(self) -> None:
        u = self._user32
        self._taskbar_created = u.RegisterWindowMessageW("TaskbarCreated")
        self._wndproc = WNDPROC(self._on_message)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.hInstance = self._kernel32.GetModuleHandleW(None)
        wc.lpszClassName = "ToDoListTrayWindow"
        self._class_atom = u.RegisterClassW(ctypes.byref(wc))
        self.hwnd = u.CreateWindowExW(0, "ToDoListTrayWindow", "ToDoList 托盘",
                                      0, 0, 0, 0, 0, None, None, wc.hInstance, None)
        if not self.hwnd:
            raise OSError("创建托盘消息窗口失败")

    def _load_icon(self):
        """依次尝试：打包/源码里的 icon.ico → exe 自带图标 → 系统默认图标。"""
        u = self._user32
        size = u.GetSystemMetrics(SM_CXSMICON) or 16
        for path in candidate_icon_paths(os.path.dirname(os.path.abspath(sys.executable))):
            if path and os.path.exists(path):
                handle = u.LoadImageW(None, path, IMAGE_ICON, size, size, LR_LOADFROMFILE)
                if handle:
                    return handle
                _log(f"图标加载失败（{path}），改用 exe 图标")
                break
        large = wintypes.HICON()
        small = wintypes.HICON()
        try:
            count = self._shell32.ExtractIconExW(sys.executable, 0,
                                                 ctypes.byref(large), ctypes.byref(small), 1)
        except Exception:
            count = 0
        if count and small.value:
            if large.value:
                u.DestroyIcon(large)
            return small.value
        for handle in (large, small):
            if handle.value:
                u.DestroyIcon(handle)
        _log("没有可用的图标资源，使用系统默认图标")
        return u.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))

    def _notify_data(self) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self.hwnd
        data.uID = 1
        data.szTip = self.tooltip
        return data

    def _add_icon(self) -> None:
        if self._icon is None:
            self._icon = self._load_icon()
        data = self._notify_data()
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._icon
        # 开机自启时外壳可能还没准备好，挂载会失败——重试几次
        for attempt in range(1, 9):
            with self._lock:
                ok = bool(self._shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)))
                self.added = ok
            if ok:
                if attempt > 1:
                    _log(f"第 {attempt} 次挂载图标成功")
                self.ensure_promoted()
                return
            code = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
            self.last_error = f"Shell_NotifyIcon(NIM_ADD) 失败，GetLastError={code}，第 {attempt} 次"
            time.sleep(0.25)

    def _message_loop(self) -> None:
        msg = wintypes.MSG()
        while self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self._user32.TranslateMessage(ctypes.byref(msg))
            self._user32.DispatchMessageW(ctypes.byref(msg))

    def _cleanup(self) -> None:
        try:
            if self.added and self._shell32 is not None:
                data = self._notify_data()
                self._shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        except Exception:
            pass
        self.added = False
        try:
            if self._icon and self._user32 is not None:
                self._user32.DestroyIcon(self._icon)
        except Exception:
            pass
        self._icon = None

    def _on_message(self, hwnd, msg, wparam, lparam):
        u = self._user32
        if msg == WM_TRAY:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._emit("show_main")
            elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu()
            return 0
        if msg == WM_CLOSE:
            u.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            u.PostQuitMessage(0)
            return 0
        if self._taskbar_created and msg == self._taskbar_created:
            # 资源管理器重启后图标会消失，重新挂一次
            self._add_icon()
            return 0
        return u.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self) -> None:
        u = self._user32
        state = {}
        try:
            state = self.state_provider() or {}
        except Exception:
            state = {}
        menu = u.CreatePopupMenu()
        u.AppendMenuW(menu, MF_STRING, 1, "打开主界面")
        float_flag = MF_CHECKED if state.get("float_visible") else MF_STRING
        u.AppendMenuW(menu, MF_STRING | float_flag, 2, "显示悬浮窗")
        monitor_flag = MF_CHECKED if state.get("monitor") else MF_STRING
        u.AppendMenuW(menu, MF_STRING | monitor_flag, 3, "开启监控")
        u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        u.AppendMenuW(menu, MF_STRING, 4, "退出程序")

        point = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(point))
        u.SetForegroundWindow(self.hwnd)
        choice = u.TrackPopupMenu(menu, TPM_LEFTALIGN | TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                  point.x, point.y, 0, self.hwnd, None)
        u.PostMessageW(self.hwnd, WM_NULL, 0, 0)  # 让菜单可靠关闭的老办法
        u.DestroyMenu(menu)
        action = MENU_ACTIONS.get(int(choice or 0))
        if action:
            self._emit(action)

    def _emit(self, action: str) -> None:
        try:
            self.on_command(action)
        except Exception as exc:
            _log(f"命令 {action} 执行失败：{exc!r}")
