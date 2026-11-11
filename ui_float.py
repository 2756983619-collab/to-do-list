"""悬浮窗：类似音乐播放器歌词窗口的置顶小窗，显示任务并控制监控开关。"""

from __future__ import annotations

import sys
import tkinter as tk

import theme as T

WIDTH = 344
RADIUS = 16
MAX_HEIGHT = 660


class _PinButton(T.RoundedButton):
    """钉子按钮：圆头 + 针尖，和其他图标保持同一套线条风格。"""

    def _paint(self):
        super()._paint()
        width = self.winfo_width()
        if width <= 1:
            width = int(self["width"])
        cx, cy = width / 2.0, self._height / 2.0
        color = self._fg
        # 图钉：圆头 + 针杆 + 底座，1 倍尺寸下也能认出是"钉"
        self.create_oval(cx - 3.5, cy - 7.5, cx + 3.5, cy - 0.5, fill=color, outline="")
        self.create_line(cx, cy - 0.5, cx, cy + 3.5, fill=color, width=2, capstyle=tk.ROUND)
        self.create_line(cx - 3.5, cy + 5, cx + 3.5, cy + 5, fill=color, width=2,
                         capstyle=tk.ROUND)


class FloatWindow(tk.Toplevel):
    def __init__(self, app):
        if sys.platform == "darwin":
            import macos_overlay
            macos_overlay.prepare_panel(app.root, "focus_float")
        super().__init__(app.root, name="focus_float")
        if sys.platform == "darwin":
            macos_overlay.configure_panel(self)
        self.app = app
        self.store = app.store
        self.title("专注清单 · 悬浮窗")
        if sys.platform != "darwin":
            self.overrideredirect(True)
        self.attributes("-topmost", True)
        T.configure_overlay(self)
        self._drag_offset = None
        self._pinned = False
        self._front_job = None
        self._hint_job = None
        self._pos = (self.winfo_screenwidth() - WIDTH - 40, 120)
        self.withdraw()
        self._build()
        self._restore_position()
        self.apply_alpha(float(self.store.settings.get("float_opacity", 0.94)))
        self.refresh_pin()
        if sys.platform == "darwin":
            self.bind("<Map>", lambda event: self.after_idle(self._finish_macos_overlay)
                      if event.widget is self else None, add="+")

    def _dispatch_native_click(self, phase, x, y):
        # Aqua cannot route first clicks reliably into an inactive Tk panel.
        # Resolve the widget within this overlay, then run its normal Tk binding.
        root_x, root_y = self.winfo_rootx() + x, self.winfo_rooty() + y
        def find(parent):
            for child in reversed(parent.winfo_children()):
                if (child.winfo_ismapped() and child.winfo_rootx() <= root_x < child.winfo_rootx() + child.winfo_width()
                        and child.winfo_rooty() <= root_y < child.winfo_rooty() + child.winfo_height()):
                    return find(child)
            return parent
        if phase == "press":
            target = find(self)
            self._native_click_target = target
        else:
            target = getattr(self, "_native_click_target", None)
            self._native_click_target = None
        if target is not None and target.winfo_exists():
            target.event_generate("<ButtonPress-1>" if phase == "press" else "<ButtonRelease-1>",
                                  x=int(root_x - target.winfo_rootx()), y=int(root_y - target.winfo_rooty()))

    def _save_native_position(self):
        self._pos = (self.winfo_x(), self.winfo_y())
        self.store.set_setting("float_pos", list(self._pos), notify=False)

    def _finish_macos_overlay(self):
        import macos_overlay
        macos_overlay.finish_overlay(self, RADIUS, panel=True)

    # ------------------------------------------------------------ layout
    def _build(self) -> None:
        self.shell = T.RoundedShell(self, radius=RADIUS, fill=T.SURFACE,
                                    outline=T.BORDER, pad=(RADIUS, RADIUS))
        self.shell.pack(fill="both", expand=True)
        self.inner = self.shell.body

        # ---- header (drag handle)
        header = tk.Frame(self.inner, bg=T.SURFACE, height=40, cursor="fleur")
        header.pack(fill="x")
        header.pack_propagate(False)
        self.header = header
        grip = tk.Label(header, text="⋮⋮", font=T.get_font(9), bg=T.SURFACE,
                        fg=T.BORDER_STRONG, cursor="fleur")
        grip.pack(side="left", padx=(0, 8))
        self.grip = grip
        self.title_label = tk.Label(header, text="专注清单", font=T.get_font(11, "bold"),
                                    bg=T.SURFACE, fg=T.TEXT, cursor="fleur")
        self.title_label.pack(side="left")
        self.dot = tk.Canvas(header, width=8, height=8, bg=T.SURFACE, bd=0,
                             highlightthickness=0, cursor="fleur")
        self.dot.pack(side="left", padx=(8, 0))
        self._dot_id = self.dot.create_oval(0, 0, 8, 8, fill=T.MUTED, outline="")

        btn_close = T.RoundedButton(header, "✕", self.app.hide_float, width=28, height=26,
                                    padx=0, radius=9, fill=T.SURFACE, fg=T.TEXT_DIM,
                                    hover=T.SOFT_2, font=T.get_font(9), bg=T.SURFACE)
        btn_close.pack(side="right")
        btn_pin = _PinButton(header, "", self.toggle_pin, width=28, height=26,
                             padx=0, radius=9, fill=T.SURFACE, fg=T.TEXT_DIM,
                             hover=T.SOFT_2, font=T.get_font(9), bg=T.SURFACE)
        btn_pin.pack(side="right", padx=(0, 2))
        self.btn_pin = btn_pin
        btn_main = T.RoundedButton(header, "▣", self.app.show_main, width=28, height=26,
                                   padx=0, radius=9, fill=T.SURFACE, fg=T.TEXT_DIM,
                                   hover=T.SOFT_2, font=T.get_font(9), bg=T.SURFACE)
        btn_main.pack(side="right", padx=(0, 2))

        self._drag_widgets = (header, grip, self.title_label, self.dot)
        self._drag_excluded = (btn_close, btn_pin, btn_main)
        for widget in self._drag_widgets:
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)
            widget.bind("<ButtonRelease-1>", self._end_drag)
            widget.bind("<Double-Button-1>", lambda _e: self.app.show_main())

        tk.Frame(self.inner, bg=T.BORDER, height=1).pack(fill="x")

        # ---- task list
        self.list_holder = tk.Frame(self.inner, bg=T.SURFACE)
        self.list_holder.pack(fill="x")
        self.scroller = T.ScrollFrame(self.list_holder, bg=T.SURFACE, max_height=320)
        self.scroller.pack(fill="both", expand=True)
        self.scroller.inner.configure(padx=0)

        tk.Frame(self.inner, bg=T.BORDER, height=1).pack(fill="x", pady=(6, 0))

        # ---- footer: monitor switch + status
        footer = tk.Frame(self.inner, bg=T.SURFACE)
        footer.pack(fill="x", pady=(10, 8))
        self.footer = footer
        line1 = tk.Frame(footer, bg=T.SURFACE)
        line1.pack(fill="x")
        self.switch = T.ToggleSwitch(line1, value=self.store.settings.get("monitor", False),
                                     command=self._on_switch, bg=T.SURFACE,
                                     width=44, height=24, on_color=T.GREEN)
        self.switch.pack(side="left", pady=(1, 0))
        self.switch_state = tk.Label(line1, text="监控已关闭", font=T.get_font(9, "bold"),
                                     bg=T.SURFACE, fg=T.MUTED)
        self.switch_state.pack(side="left", padx=(10, 0))
        self.interval_label = tk.Label(line1, text="", font=T.get_font(8), bg=T.SURFACE,
                                       fg=T.MUTED)
        self.interval_label.pack(side="right")
        self.detail_label = tk.Label(footer, text="", font=T.get_font(8), bg=T.SURFACE,
                                     fg=T.MUTED, anchor="w", justify="left",
                                     wraplength=WIDTH - 2 * RADIUS - 6)
        # 先不占位，等 apply_status 有内容时再 pack，避免底部空白

        self._build_menu()
        self.refresh_rows()
        self.refresh_monitor()

    def _build_menu(self) -> None:
        self.menu = tk.Menu(self, tearoff=0, bg=T.SURFACE, fg=T.TEXT,
                            activebackground=T.ACCENT, activeforeground="#ffffff",
                            bd=0, font=T.get_font(9))
        self.menu.add_command(label="打开主界面", command=self.app.show_main)
        self.menu.add_command(label="隐藏悬浮窗", command=self.app.hide_float)
        self._pin_var = tk.BooleanVar(master=self, value=bool(self._pinned))
        self.menu.add_checkbutton(label="钉住悬浮窗（固定最前 · 不可拖动）",
                                  variable=self._pin_var, command=self._on_menu_pin)
        self.menu.add_separator()
        self.menu.add_command(label="退出程序", command=self.app.quit)
        # Toplevel bindings cover every child, including task labels and drag handles.
        self.bind("<Button-3>", self._popup_menu)
        if sys.platform == "darwin":
            self.bind("<Button-2>", self._popup_menu)
            self.bind("<Control-Button-1>", self._popup_menu)

    def _popup_menu(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()
        return "break"

    # ------------------------------------------------------------ tasks
    def refresh_rows(self) -> None:
        for child in self.scroller.inner.winfo_children():
            child.destroy()
        tasks = self.store.tasks
        if not tasks:
            tk.Label(self.scroller.inner, text="还没有任务，去主界面添加吧",
                     font=T.get_font(9), bg=T.SURFACE, fg=T.MUTED,
                     anchor="w", padx=2, pady=18).pack(fill="x")
        for task in tasks:
            self._make_row(task)
        self._apply_size()
        self._schedule_size()

    def _make_row(self, task: dict) -> None:
        done = bool(task.get("done"))
        row = tk.Frame(self.scroller.inner, bg=T.SURFACE)
        row.pack(fill="x", pady=1)
        box = T.CheckBox(row, value=done, bg=T.SURFACE, size=18,
                         command=lambda _v, tid=task["id"]: self.store.toggle_task(tid))
        box.pack(side="left", padx=(2, 10), pady=7)
        label = tk.Label(row, text=task["text"], font=T.get_font(10, overstrike=done),
                         bg=T.SURFACE, fg=T.MUTED if done else T.TEXT, anchor="w",
                         justify="left", wraplength=WIDTH - 2 * RADIUS - 42,
                         cursor="hand2")
        label.pack(side="left", fill="x", expand=True, pady=6)
        label.bind("<Button-1>", lambda _e, tid=task["id"]: self.store.toggle_task(tid))
        T.attach_hover([row, label], T.SURFACE, T.SOFT)

    def _apply_size(self) -> None:
        self.scroller.refresh()
        self.update_idletasks()
        total = self.inner.winfo_reqheight() + 2 * RADIUS + 2
        height = max(160, min(MAX_HEIGHT, total))
        x, y = self._target_pos()
        sign_x = "+" if x >= 0 else "-"
        sign_y = "+" if y >= 0 else "-"
        self.geometry(f"{WIDTH}x{height}{sign_x}{abs(x)}{sign_y}{abs(y)}")
        self.shell.relayout()

    def _target_pos(self):
        if self.winfo_ismapped():
            return self.winfo_x(), self.winfo_y()
        return self._pos

    def _schedule_size(self) -> None:
        """布局稳定后再量一次，避免首次测量偏大/偏小。"""
        self.after_idle(self._apply_size)

    # ------------------------------------------------------------ monitor state
    def _on_switch(self, value: bool) -> None:
        self.app.set_monitor(bool(value))

    def refresh_monitor(self) -> None:
        enabled = bool(self.store.settings.get("monitor"))
        try:
            self.switch.set_value(enabled, animate=True)
        except tk.TclError:
            pass
        interval = int(self.store.settings.get("interval", 90))
        self.interval_label.configure(text=f"每 {interval} 秒" if enabled else "")
        self.refresh_status()

    def apply_status(self, text: str, color: str, enabled: bool) -> None:
        self.dot.itemconfigure(self._dot_id, fill=color)
        self.switch_state.configure(
            text="监控已开启" if enabled else "监控已关闭",
            fg=T.GREEN if enabled else T.MUTED,
        )
        calls = getattr(self.app, "alert_count", 0)
        last = getattr(self.app, "last_alert_at", 0.0)
        stamp = ""
        if last:
            import time as _time
            stamp = _time.strftime("%H:%M", _time.localtime(last))
        if enabled:
            detail = f"{text}"
            if calls:
                detail += f" · 已提醒 {calls} 次"
            if stamp:
                detail += f" · 上次 {stamp}"
        elif stamp:
            detail = f"上次提醒 {stamp}"
        else:
            detail = ""
        if detail:
            self.detail_label.configure(text=detail)
            if not self.detail_label.winfo_ismapped():
                self.detail_label.pack(anchor="w", pady=(6, 0))
        else:
            self.detail_label.pack_forget()
        self._schedule_size()

    def refresh_status(self) -> None:
        self.app.refresh_status()

    # ------------------------------------------------------------ pinned
    def toggle_pin(self) -> None:
        self.set_pinned(not self._pinned)

    def _on_menu_pin(self) -> None:
        self.set_pinned(bool(self._pin_var.get()))

    def refresh_pin(self) -> None:
        """跟随设置里的 float_pinned（主界面/菜单改过都走这里）。"""
        self.set_pinned(bool(self.store.settings.get("float_pinned", False)), save=False)

    def set_pinned(self, pinned: bool, save: bool = True) -> None:
        pinned = bool(pinned)
        changed = pinned != self._pinned
        self._pinned = pinned
        if save and changed:
            self.store.set_setting("float_pinned", pinned)
        if getattr(self, "_pin_var", None) is not None:
            try:
                self._pin_var.set(pinned)
            except tk.TclError:
                pass
        self._apply_pin_visual()
        if pinned:
            try:
                self.attributes("-topmost", True)
                self.lift()
            except tk.TclError:
                pass
            self._keep_front()
        else:
            self._stop_keep_front()

    def _apply_pin_visual(self) -> None:
        pinned = self._pinned
        try:
            self.btn_pin.set_style(
                fill=T.ACCENT_SOFT if pinned else T.SURFACE,
                fg=T.ACCENT if pinned else T.TEXT_DIM,
                hover=T.ACCENT_SOFT if pinned else T.SOFT_2,
            )
            self.grip.configure(fg=T.ACCENT if pinned else T.BORDER_STRONG)
            cursor = "arrow" if pinned else "fleur"
            for widget in self._drag_widgets:
                widget.configure(cursor=cursor)
        except tk.TclError:
            return

    def _keep_front(self) -> None:
        """钉住时定期抬到最前，压住别的置顶窗口。"""
        self._stop_keep_front()
        if self._pinned:
            self._front_job = self.after(1500, self._keep_front_tick)

    def _keep_front_tick(self) -> None:
        self._front_job = None
        if not self._pinned:
            return
        try:
            if self.winfo_viewable():
                self.attributes("-topmost", True)
                self.lift()
        except tk.TclError:
            return
        self._front_job = self.after(1500, self._keep_front_tick)

    def _stop_keep_front(self) -> None:
        if self._front_job is not None:
            try:
                self.after_cancel(self._front_job)
            except tk.TclError:
                pass
            self._front_job = None

    # ------------------------------------------------------------ hint
    def flash_hint(self, text: str, ms: int = 4200) -> None:
        """底部临时显示一句话，几秒后恢复真实状态。"""
        try:
            self.detail_label.configure(text=text)
            if not self.detail_label.winfo_ismapped():
                self.detail_label.pack(anchor="w", pady=(6, 0))
            self._schedule_size()
        except tk.TclError:
            return
        if self._hint_job is not None:
            try:
                self.after_cancel(self._hint_job)
            except tk.TclError:
                pass
        self._hint_job = self.after(ms, self._clear_hint)

    def _clear_hint(self) -> None:
        self._hint_job = None
        enabled = bool(self.store.settings.get("monitor"))
        try:
            self.apply_status(getattr(self.app, "status_text", ""),
                              getattr(self.app, "status_color", T.MUTED), enabled)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ window control
    def show(self) -> None:
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        if self._pinned:
            self._keep_front()
        self._apply_size()
        self._schedule_size()

    def hide(self) -> None:
        self.withdraw()

    def apply_alpha(self, value: float) -> None:
        alpha = max(0.35, min(1.0, float(value)))
        try:
            self.attributes("-alpha", alpha)
        except tk.TclError:
            return
        # 越透明越需要一圈更实的描边，否则白卡在花哨壁纸上会糊掉
        outline = T.BORDER if alpha >= 0.98 else T.BORDER_STRONG
        if outline != self.shell._outline:
            self.shell._outline = outline
            self.shell.relayout()

    def _restore_position(self) -> None:
        pos = self.store.settings.get("float_pos")
        placed = T.clamp_position(pos, WIDTH, 260, self) if pos else None
        if placed:
            x, y = placed
        else:
            x = self.winfo_screenwidth() - WIDTH - 40
            y = 120
        self._pos = (x, y)
        self.geometry(f"{WIDTH}x260+{x}+{y}")

    # ------------------------------------------------------------ dragging
    def _start_drag(self, event):
        if self._pinned:
            return
        self._drag_offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _on_drag(self, event):
        if self._pinned or not self._drag_offset:
            return
        dx, dy = self._drag_offset
        self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _end_drag(self, _event=None):
        if not self._drag_offset:
            return
        self._drag_offset = None
        self._pos = (self.winfo_x(), self.winfo_y())
        self.store.set_setting("float_pos", list(self._pos), notify=False)
