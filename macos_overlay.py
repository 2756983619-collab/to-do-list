"""Native macOS overlay setup. Call only from the Tk main thread."""
from __future__ import annotations


def prepare_panel(master, name: str) -> None:
    """Tk 9 must know the NSPanel class before creating the toplevel."""
    if int(master.tk.call("info", "patchlevel").split(".")[0]) >= 9:
        path = master._w.rstrip(".") + "." + name
        master.tk.call("wm", "attributes", path, "-class", "nspanel")


def configure_panel(window) -> None:
    """Keep Tk's window type consistent with a nonactivating native panel."""
    if int(window.tk.call("info", "patchlevel").split(".")[0]) >= 9:
        window.tk.call("wm", "attributes", window._w, "-stylemask", ("nonactivatingpanel", "docmodal"))
    else:
        window.tk.call("::tk::unsupported::MacWindowStyle", "style", window._w,
                       "floating", ("noTitleBar", "noActivates", "nonActivating", "doesNotHide"))


def finish_overlay(window, radius: int, *, panel: bool = False) -> None:
    """Clip the opaque Tk canvas natively, rather than painting clear pixels black.

    Window titles are unique for the two overlays in this process. Avoid treating
    winfo_id() (a Tk drawable on macOS) as an Objective-C object pointer.
    """
    import AppKit
    import Quartz  # Registers the CALayer methods used below with PyObjC.

    native = next((item for item in AppKit.NSApplication.sharedApplication().windows()
                   if str(item.title()) == window.title()), None)
    if native is None:
        raise RuntimeError("找不到悬浮窗的 macOS 原生窗口")
    if panel:
        configure_panel(window)
        if not native.isKindOfClass_(AppKit.NSPanel):
            raise RuntimeError("悬浮窗需要使用 macOS 非激活面板")
        native.setHidesOnDeactivate_(False)
        native.setBecomesKeyOnlyIfNeeded_(True)
        _install_native_drag(window, native)
    native.setOpaque_(False)
    native.setBackgroundColor_(AppKit.NSColor.clearColor())
    native.setHasShadow_(False)
    view = native.contentView()
    view.setWantsLayer_(True)
    view.layer().setCornerRadius_(float(radius))
    view.layer().setMasksToBounds_(True)


def _install_native_drag(window, native):
    """Handle title drags before Tk's focus handling, without reentering Tcl.

    Cocoa's event monitor runs inside Tk's native event dispatch. Calling Tk
    there can reenter its interpreter with an invalid Python thread state.
    Native events only update Cocoa and flags; a Tk timer saves/shows later.
    """
    import AppKit

    previous = getattr(window, "_native_drag_monitor", None)
    if previous is not None:
        AppKit.NSEvent.removeMonitor_(previous)
    origin_x, origin_y = window.winfo_rootx(), window.winfo_rooty()
    def rect(widget):
        return (widget.winfo_rootx() - origin_x, widget.winfo_rooty() - origin_y,
                widget.winfo_width(), widget.winfo_height())
    header = rect(window._drag_widgets[0])
    excluded = [rect(widget) for widget in window._drag_excluded]
    state = {"drag": None, "save": False, "show": False, "pressed": False, "clicks": []}
    def contains(rectangle, x, y):
        rx, ry, width, height = rectangle
        return rx <= x < rx + width and ry <= y < ry + height
    def handle(event):
        kind = event.type()
        if kind == AppKit.NSEventTypeLeftMouseDown:
            if event.windowNumber() != native.windowNumber():
                return event
            point = event.locationInWindow()
            x, y = point.x, native.contentView().bounds().size.height - point.y
            if not contains(header, x, y) or any(contains(r, x, y) for r in excluded):
                state["pressed"] = True
                state["clicks"].append(("press", x, y))
                return None
            if event.clickCount() == 2:
                state["show"] = True
                return None
            mouse, origin = AppKit.NSEvent.mouseLocation(), native.frame().origin
            state["drag"] = (mouse.x, mouse.y, origin.x, origin.y)
            return None
        if state["pressed"]:
            if kind == AppKit.NSEventTypeLeftMouseUp:
                point = event.locationInWindow()
                state["clicks"].append(("release", point.x, native.contentView().bounds().size.height - point.y))
                state["pressed"] = False
            return None
        if state["drag"] is None:
            return event
        if kind == AppKit.NSEventTypeLeftMouseDragged:
            mouse = AppKit.NSEvent.mouseLocation()
            mx, my, wx, wy = state["drag"]
            native.setFrameOrigin_((wx + mouse.x - mx, wy + mouse.y - my))
            return None
        if kind == AppKit.NSEventTypeLeftMouseUp:
            state["drag"] = None
            state["save"] = True
            return None
        return event

    window._native_drag_state = state
    mask = (AppKit.NSEventMaskLeftMouseDown | AppKit.NSEventMaskLeftMouseDragged |
            AppKit.NSEventMaskLeftMouseUp)
    window._native_drag_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, handle)
    if not getattr(window, "_native_drag_cleanup_bound", False):
        def poll():
            nonlocal origin_x, origin_y, header, excluded
            origin_x, origin_y = window.winfo_rootx(), window.winfo_rooty()
            header = rect(window._drag_widgets[0])
            excluded = [rect(widget) for widget in window._drag_excluded]
            current = window._native_drag_state
            clicks, current["clicks"] = current["clicks"], []
            for phase, x, y in clicks:
                window._dispatch_native_click(phase, x, y)
            if current["save"]:
                current["save"] = False
                window._save_native_position()
            if current["show"]:
                current["show"] = False
                window.app.show_main()
            window._native_drag_job = window.after(50, poll)
        window._native_drag_job = window.after(50, poll)
        def cleanup(event):
            if event.widget is window:
                token = getattr(window, "_native_drag_monitor", None)
                if token is not None:
                    AppKit.NSEvent.removeMonitor_(token)
                    window._native_drag_monitor = None
                window.after_cancel(window._native_drag_job)
        window.bind("<Destroy>", cleanup, add="+")
        window._native_drag_cleanup_bound = True
