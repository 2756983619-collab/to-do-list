"""Read-only macOS application/window sampling via PyObjC.

Imports are lazy so pure matching tests and task management can run without
native dependencies. Only the UI explicitly requests Accessibility permission.
"""
from __future__ import annotations

import os

BROWSER_EXES = {
    "safari", "google chrome", "microsoft edge", "firefox", "brave browser",
    "opera", "vivaldi", "chromium", "arc",
}
SYSTEM_EXES: set[str] = set()


def _native():
    try:
        import AppKit
        import ApplicationServices as AX
        import objc
    except ImportError as exc:
        raise RuntimeError(
            "缺少 Mac 依赖，请运行 .venv/bin/python -m pip install -r requirements.txt"
        ) from exc
    return AppKit, AX, objc


def accessibility_trusted() -> bool:
    _, AX, _ = _native()
    return bool(AX.AXIsProcessTrusted())


def request_accessibility() -> None:
    _, AX, _ = _native()
    AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True})


def _exe(app) -> str:
    url = app.executableURL()
    return os.path.basename(str(url.path())).lower() if url else ""


def process_exe(pid: int) -> str:
    AppKit, _, objc = _native()
    with objc.autorelease_pool():
        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        return _exe(app) if app else ""


def _attribute(AX, element, name, default=None):
    error, value = AX.AXUIElementCopyAttributeValue(element, name, None)
    if error == AX.kAXErrorNoValue or error == AX.kAXErrorAttributeUnsupported:
        return default
    if error != AX.kAXErrorSuccess:
        raise RuntimeError(f"Accessibility read failed: {error}")
    return default if value is None else value


def sample_system_state() -> dict:
    AppKit, AX, objc = _native()
    with objc.autorelease_pool():
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        trusted = bool(AX.AXIsProcessTrusted())
        processes, windows = set(), []
        errors = 0
        front = workspace.frontmostApplication()
        for app in workspace.runningApplications():
            try:
                exe = _exe(app)
                if not exe:
                    continue
                processes.add(exe)
                if not trusted or app.isTerminated() or app.processIdentifier() == os.getpid():
                    continue
                # Background agents have no normal windows. Avoid AX calls to them.
                if app.activationPolicy() != AppKit.NSApplicationActivationPolicyRegular:
                    continue
                element = AX.AXUIElementCreateApplication(app.processIdentifier())
                AX.AXUIElementSetMessagingTimeout(element, 0.2)
                app_windows = _attribute(AX, element, AX.kAXWindowsAttribute, [])
                for window in app_windows:
                    try:
                        title = str(_attribute(AX, window, AX.kAXTitleAttribute, "")).strip()
                        minimized = bool(_attribute(AX, window, AX.kAXMinimizedAttribute, False))
                        if title:
                            windows.append({"hwnd": 0, "pid": int(app.processIdentifier()),
                                            "exe": exe, "title": title,
                                            "minimized": minimized, "hidden": bool(app.isHidden())})
                    except Exception:
                        errors += 1
            except Exception:
                errors += 1
        return {"windows": windows, "processes": processes,
                "foreground": int(front.processIdentifier()) if front else 0,
                "accessibility": trusted, "sampling_errors": errors}


def list_processes() -> set[str]:
    AppKit, _, objc = _native()
    with objc.autorelease_pool():
        return {name for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications()
                if (name := _exe(app))}


def list_windows() -> list[dict]:
    return sample_system_state()["windows"]
