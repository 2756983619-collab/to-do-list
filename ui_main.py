"""主界面：任务清单编辑 + 监控目标管理（浅色圆角）。"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

import monitor
import theme as T

KIND_META = {"website": ("🌐", "网站"), "software": ("🖥", "软件")}


class MainWindow(tk.Tk):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.store = app.store
        self._kind = "website"
        self._toast_job = None
        self._loading = False

        T.style_widgets(self)
        T.install_wheel_router(self)
        self.configure(bg=T.BG)
        self.title("To-Do List · 专注清单")
        self.minsize(920, 600)
        geometry = self.store.settings.get("main_geometry")
        self.geometry(geometry if isinstance(geometry, str) else "1040x670")
        self._load_icon()

        self._build()
        self.refresh_all()
        self.protocol("WM_DELETE_WINDOW", self.app.close_main)
        self.bind("<Control-n>", lambda _e: self.task_entry.focus_set())
        if sys.platform == "darwin":
            self.bind("<Command-n>", lambda _e: self.task_entry.focus_set())
            self.bind("<Command-q>", lambda _e: self.app.quit())
            self.createcommand("tk::mac::Quit", self.app.quit)
            menu = tk.Menu(self)
            permissions = tk.Menu(menu, tearoff=False)
            permissions.add_command(label="打开辅助功能设置…", command=self.app.open_accessibility_settings)
            permissions.add_command(label="检查授权状态…", command=self.app.ensure_accessibility)
            menu.add_cascade(label="监控权限", menu=permissions)
            self.configure(menu=menu)
        self.after(80, lambda: T.style_titlebar(self, dark=False))
        self.after(120, self._initial_focus)

    # ------------------------------------------------------------ window chrome
    def _load_icon(self) -> None:
        import os
        import sys
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        icon = os.path.join(base, "assets", "icon.png")
        self._logo_small = None
        try:
            self._icon_image = tk.PhotoImage(file=icon)
            self.iconphoto(True, self._icon_image)
            self._logo_small = self._icon_image.subsample(8, 8)
        except tk.TclError:
            pass

    def _initial_focus(self) -> None:
        try:
            self.task_entry.focus_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ layout
    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=3, minsize=480)
        self.grid_columnconfigure(1, weight=2, minsize=390)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_tasks()
        self._build_targets()
        self._build_footer()

        self.toast_pill = T.Pill(self, "", height=36, fill="#1b2130", fg="#ffffff",
                                 outline="", padx=18, bg=T.BG)

    def _build_header(self) -> None:
        head = tk.Frame(self, bg=T.BG)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=26, pady=(20, 16))

        left = tk.Frame(head, bg=T.BG)
        left.pack(side="left")
        if getattr(self, "_logo_small", None) is not None:
            tk.Label(left, image=self._logo_small, bg=T.BG).pack(side="left",
                                                                 padx=(0, 12), pady=3)
        titles = tk.Frame(left, bg=T.BG)
        titles.pack(side="left")
        tk.Label(titles, text="专注清单", font=T.get_font(17, "bold"), bg=T.BG,
                 fg=T.TEXT).pack(anchor="w")
        tk.Label(titles, text="To-Do List · 任务管理与干扰源监控", font=T.get_font(9),
                 bg=T.BG, fg=T.MUTED).pack(anchor="w", pady=(3, 0))

        right = tk.Frame(head, bg=T.BG)
        right.pack(side="right")
        self.monitor_btn = T.RoundedButton(right, "开始监控", self.app.toggle_monitor,
                                           height=40, padx=22, radius=11, font=T.get_font(10, "bold"))
        self.monitor_btn.pack(side="right")
        self.status_pill = T.Pill(right, "监控已暂停", dot=T.MUTED, height=36,
                                  bg=T.BG, font=T.get_font(9))
        self.status_pill.pack(side="right", padx=(0, 12))

    def _build_tasks(self) -> None:
        card = T.Card(self, radius=16, pad=(18, 16))
        card.grid(row=1, column=0, sticky="nsew", padx=(26, 9), pady=(0, 14))
        body = card.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)

        top = tk.Frame(body, bg=T.SURFACE)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        tk.Label(top, text="任务清单", font=T.get_font(13, "bold"), bg=T.SURFACE,
                 fg=T.TEXT).pack(side="left")
        self.counter_label = tk.Label(top, text="", font=T.get_font(9), bg=T.SURFACE,
                                      fg=T.MUTED)
        self.counter_label.pack(side="right")

        add_row = tk.Frame(body, bg=T.SURFACE)
        add_row.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.task_entry = T.RoundedEntry(add_row, "写下一件要做的事，例如：整理实验数据",
                                         height=42, radius=11, bg=T.SURFACE)
        self.task_entry.pack(side="left", fill="x", expand=True)
        self.task_entry.bind("<Return>", self._add_task)
        T.RoundedButton(add_row, "添加", self._add_task, height=42, padx=22,
                        radius=11).pack(side="left", padx=(10, 0))

        self.task_scroller = T.ScrollFrame(body, bg=T.SURFACE)
        self.task_scroller.grid(row=2, column=0, sticky="nsew")

        bottom = tk.Frame(body, bg=T.SURFACE)
        bottom.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.clear_btn = tk.Label(bottom, text="清除已完成", font=T.get_font(9),
                                  bg=T.SURFACE, fg=T.MUTED, cursor="hand2")
        self.clear_btn.pack(side="left")
        self.clear_btn.bind("<Button-1>", lambda _e: self._clear_done())
        T.attach_hover([self.clear_btn], T.SURFACE, T.RED)
        tk.Label(bottom, text="回车添加 · 勾选后划线保留", font=T.get_font(8),
                 bg=T.SURFACE, fg=T.BORDER_STRONG).pack(side="right")

    def _build_targets(self) -> None:
        card = T.Card(self, radius=16, pad=(18, 16))
        card.grid(row=1, column=1, sticky="nsew", padx=(9, 26), pady=(0, 14))
        body = card.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(5, weight=1)

        top = tk.Frame(body, bg=T.SURFACE)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        tk.Label(top, text="监控目标", font=T.get_font(13, "bold"), bg=T.SURFACE,
                 fg=T.TEXT).pack(side="left")
        self.target_count = tk.Label(top, text="", font=T.get_font(9), bg=T.SURFACE,
                                     fg=T.MUTED)
        self.target_count.pack(side="right")

        kinds = tk.Frame(body, bg=T.SURFACE)
        kinds.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.kind_buttons = {}
        for kind, (icon, label) in KIND_META.items():
            button = T.RoundedButton(kinds, f"{icon} {label}", height=36, padx=16,
                                     radius=10, fill=T.SOFT, fg=T.TEXT_DIM,
                                     hover=T.SOFT_2, font=T.get_font(9, "bold"),
                                     bg=T.SURFACE)
            button.pack(side="left", padx=(0, 8))
            button._command = (lambda k=kind: self._set_kind(k))
            self.kind_buttons[kind] = button

        self.target_name = T.RoundedEntry(body, "名称（可留空，例如 Bilibili）",
                                          height=40, radius=11, bg=T.SURFACE)
        self.target_name.grid(row=2, column=0, sticky="ew", pady=(0, 9))
        self.target_name.bind("<Return>", lambda _e: self._add_target())

        match_row = tk.Frame(body, bg=T.SURFACE)
        match_row.grid(row=3, column=0, sticky="ew")
        self.target_match = T.RoundedEntry(match_row, "例如 bilibili", height=40,
                                           radius=11, bg=T.SURFACE)
        self.target_match.pack(side="left", fill="x", expand=True)
        self.target_match.bind("<Return>", lambda _e: self._add_target())
        T.RoundedButton(match_row, "添加", self._add_target, height=40, padx=18,
                        radius=11).pack(side="left", padx=(9, 0))

        self.hint_label = tk.Label(body, text="", font=T.get_font(8), bg=T.SURFACE,
                                   fg=T.MUTED, justify="left", anchor="w",
                                   wraplength=340)
        self.hint_label.grid(row=4, column=0, sticky="ew", pady=(9, 10))

        self.target_scroller = T.ScrollFrame(body, bg=T.SURFACE)
        self.target_scroller.grid(row=5, column=0, sticky="nsew")

        bottom = tk.Frame(body, bg=T.SURFACE)
        bottom.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        scan = tk.Label(bottom, text="🔍 扫描运行中的程序", font=T.get_font(9),
                        bg=T.SURFACE, fg=T.ACCENT, cursor="hand2")
        scan.pack(side="left")
        scan.bind("<Button-1>", lambda _e: self._scan())
        T.attach_hover([scan], T.SURFACE, T.ACCENT_DARK)
        tk.Label(bottom, text="勾选即生效", font=T.get_font(8), bg=T.SURFACE,
                 fg=T.BORDER_STRONG).pack(side="right")

    def _build_footer(self) -> None:
        foot = tk.Frame(self, bg=T.BG)
        foot.grid(row=2, column=0, columnspan=2, sticky="ew", padx=26, pady=(0, 18))

        left = tk.Frame(foot, bg=T.BG)
        left.pack(side="left")
        self.repeat_box = T.CheckBox(left, value=True, bg=T.BG, size=18,
                                     command=self._on_repeat_toggle)
        self.repeat_box.pack(side="left")
        self.repeat_label = tk.Label(left, text="持续提醒", font=T.get_font(9), bg=T.BG,
                                     fg=T.TEXT_DIM, cursor="hand2")
        self.repeat_label.pack(side="left", padx=(8, 2))
        self.repeat_label.bind("<Button-1>", lambda _e: self.repeat_box._on_click())
        tk.Label(left, text="每", font=T.get_font(9), bg=T.BG, fg=T.MUTED).pack(
            side="left", padx=(8, 5))
        self.interval_var = tk.StringVar(value="90")
        self.interval_spin = ttk.Spinbox(left, from_=10, to=3600, increment=10, width=4,
                                         textvariable=self.interval_var,
                                         font=T.get_font(9), style="App.TSpinbox",
                                         justify="center", command=self._on_interval_change)
        self.interval_spin.pack(side="left", ipady=2)
        self.interval_spin.bind("<FocusOut>", lambda _e: self._on_interval_change())
        self.interval_spin.bind("<Return>", lambda _e: self._on_interval_change())
        tk.Label(left, text="秒提醒一次", font=T.get_font(9), bg=T.BG,
                 fg=T.MUTED).pack(side="left", padx=(5, 16))

        self.float_box = T.CheckBox(left, value=True, bg=T.BG, size=18,
                                    command=self._on_float_toggle)
        self.float_box.pack(side="left")
        self.float_label = tk.Label(left, text="显示悬浮窗", font=T.get_font(9), bg=T.BG,
                                    fg=T.TEXT_DIM, cursor="hand2")
        self.float_label.pack(side="left", padx=(8, 0))
        self.float_label.bind("<Button-1>", lambda _e: self.float_box._on_click())

        self.pin_box = T.CheckBox(left, value=False, bg=T.BG, size=18,
                                  command=self._on_pin_toggle)
        self.pin_box.pack(side="left", padx=(16, 0))
        self.pin_label = tk.Label(left, text="钉住悬浮窗", font=T.get_font(9), bg=T.BG,
                                  fg=T.TEXT_DIM, cursor="hand2")
        self.pin_label.pack(side="left", padx=(8, 0))
        self.pin_label.bind("<Button-1>", lambda _e: self.pin_box._on_click())

        right = tk.Frame(foot, bg=T.BG)
        right.pack(side="right")
        tk.Label(right, text="悬浮窗不透明度", font=T.get_font(9), bg=T.BG,
                 fg=T.MUTED).pack(side="left", padx=(0, 6))
        self.opacity_slider = T.Slider(right, from_=40, to=100, value=90, width=104,
                                       height=24, bg=T.BG, command=self._on_opacity)
        self.opacity_slider.pack(side="left")
        self.opacity_value = tk.Label(right, text="90%", font=T.get_font(9), bg=T.BG,
                                      fg=T.TEXT_DIM, width=4, anchor="e")
        self.opacity_value.pack(side="left", padx=(6, 0))
        tk.Label(right, text="自动保存", font=T.get_font(8), bg=T.BG,
                 fg=T.BORDER_STRONG).pack(side="left", padx=(10, 0))

    # ------------------------------------------------------------ refresh
    def refresh_all(self) -> None:
        self._loading = True
        try:
            self._refresh_tasks()
            self._refresh_targets()
            self._refresh_settings()
        finally:
            self._loading = False
        if getattr(self.app, "float", None) is not None:
            self.app.refresh_status()

    def _refresh_tasks(self) -> None:
        for child in self.task_scroller.inner.winfo_children():
            child.destroy()
        tasks = self.store.tasks
        if not tasks:
            tk.Label(self.task_scroller.inner,
                     text="还没有任务 —— 在上面输入框写下第一件事",
                     font=T.get_font(9), bg=T.SURFACE, fg=T.MUTED, anchor="w",
                     padx=10, pady=22).pack(fill="x")
        for task in tasks:
            self._make_task_row(task)
        stats = self.store.stats()
        self.counter_label.configure(text=f"{stats['done']} / {stats['total']} 已完成")

    def _bind_row_hover(self, row, widgets, normal=T.SURFACE, hover=T.SOFT):
        """行悬停时整行染一层浅底，让列表更有反馈感。"""
        targets = [row] + [w for w in widgets if w is not None]

        def tint(color):
            for widget in targets:
                try:
                    widget.configure(bg=color)
                except tk.TclError:
                    pass

        row.bind("<Enter>", lambda _e: tint(hover))
        row.bind("<Leave>", lambda _e: tint(normal))
        for widget in widgets:
            if widget is None:
                continue
            widget.bind("<Enter>", lambda _e: tint(hover), add="+")
            widget.bind("<Leave>", lambda _e: tint(normal), add="+")

    def _make_task_row(self, task: dict) -> None:
        done = bool(task.get("done"))
        row = tk.Frame(self.task_scroller.inner, bg=T.SURFACE)
        row.pack(fill="x", pady=1)

        box = T.CheckBox(row, value=done, bg=T.SURFACE, size=19,
                         command=lambda _v, tid=task["id"]: self.store.toggle_task(tid))
        box.pack(side="left", padx=(6, 11), pady=7)

        entry = tk.Entry(row, bg=T.SURFACE, fg=T.MUTED if done else T.TEXT,
                         relief="flat", bd=0, insertbackground=T.TEXT,
                         highlightthickness=0, font=T.get_font(10, overstrike=done),
                         selectbackground=T.ACCENT_SOFT, selectforeground=T.TEXT,
                         disabledbackground=T.SURFACE, disabledforeground=T.MUTED)
        entry.insert(0, task["text"])
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<Return>", lambda _e, tid=task["id"], w=entry: self._commit_task(tid, w))
        entry.bind("<FocusOut>", lambda _e, tid=task["id"], w=entry: self._commit_task(tid, w))
        entry.bind("<Escape>", lambda _e, w=entry: w.selection_clear())

        delete = tk.Label(row, text="✕", font=T.get_font(9), bg=T.SURFACE, fg=T.MUTED,
                          padx=8, cursor="hand2")
        delete.pack(side="right", padx=(4, 2))
        delete.bind("<Button-1>", lambda _e, tid=task["id"]: self.store.delete_task(tid))
        T.attach_hover([delete], T.SURFACE, T.RED)
        self._bind_row_hover(row, [entry, delete])

    def _refresh_targets(self) -> None:
        for child in self.target_scroller.inner.winfo_children():
            child.destroy()
        targets = self.store.targets
        if not targets:
            tk.Label(self.target_scroller.inner,
                     text="还没有监控目标\n添加后，一旦它被打开就会弹窗提醒你干活",
                     font=T.get_font(9), bg=T.SURFACE, fg=T.MUTED, anchor="w",
                     justify="left", padx=10, pady=18).pack(fill="x")
        for target in targets:
            self._make_target_row(target)
        self.target_count.configure(text=f"{len(targets)} 个")
        self._update_hint()

    def _make_target_row(self, target: dict) -> None:
        enabled = bool(target.get("enabled", True))
        icon = KIND_META.get(target.get("kind"), ("•", "?"))[0]
        row = tk.Frame(self.target_scroller.inner, bg=T.SURFACE)
        row.pack(fill="x", pady=1)

        T.CheckBox(row, value=enabled, bg=T.SURFACE, size=19,
                   accent=T.ACCENT if enabled else T.BORDER_STRONG,
                   command=lambda _v, tid=target["id"]: self.store.toggle_target(tid)).pack(
            side="left", padx=(6, 10), pady=7)
        tk.Label(row, text=icon, font=T.get_font(9), bg=T.SURFACE,
                 fg=T.ACCENT if enabled else T.BORDER_STRONG).pack(side="left",
                                                                  padx=(0, 7))

        name = tk.Label(row, text=target["name"], font=T.get_font(9, "bold"),
                        bg=T.SURFACE, fg=T.TEXT if enabled else T.MUTED, anchor="w")
        name.pack(side="left", fill="x", expand=True)

        match = tk.Label(row, text=target["match"], font=T.get_font(8), bg=T.SURFACE,
                         fg=T.BORDER_STRONG)
        match.pack(side="left", padx=(6, 6))

        delete = tk.Label(row, text="✕", font=T.get_font(9), bg=T.SURFACE, fg=T.MUTED,
                          padx=7, cursor="hand2")
        delete.pack(side="right", padx=(0, 2))
        delete.bind("<Button-1>", lambda _e, tid=target["id"]: self.store.delete_target(tid))
        T.attach_hover([delete], T.SURFACE, T.RED)
        self._bind_row_hover(row, [name, match, delete])

    def _refresh_settings(self) -> None:
        settings = self.store.settings
        self.repeat_box.set_value(bool(settings.get("repeat_alert", True)))
        self.float_box.set_value(bool(settings.get("float_visible", True)))
        self.pin_box.set_value(bool(settings.get("float_pinned", False)))
        self.interval_var.set(str(int(settings.get("interval", 90))))
        opacity = float(settings.get("float_opacity", 0.9)) * 100
        self.opacity_slider.set(opacity)
        if hasattr(self, "opacity_value"):
            self.opacity_value.configure(text=f"{int(round(opacity))}%")
        self._set_kind(self._kind, refresh=False)

    def _update_hint(self) -> None:
        if self._kind == "website":
            text = "输入关键词或域名，如 bilibili、youtube、weibo.com。\n浏览器某个窗口的当前标签页标题包含它，即视为“正在访问”。"
        else:
            text = "输入程序名，如 steam、wechat、notepad。\n检测到该进程在运行（或有窗口标题命中）即提醒。"
        self.hint_label.configure(text=text)

    # ------------------------------------------------------------ tasks actions
    def _add_task(self, _event=None):
        text = self.task_entry.get_value() if hasattr(self.task_entry, "get_value") \
            else self.task_entry.get().strip()
        if not text:
            self.toast("先写点内容吧")
            return
        self.store.add_task(text)
        if hasattr(self.task_entry, "reset"):
            self.task_entry.reset()
        else:
            self.task_entry.delete(0, "end")
        self.task_entry.focus_set()
        self.after(30, self.task_scroller.scroll_to_end)

    def _commit_task(self, task_id: str, entry: tk.Entry) -> None:
        text = entry.get().strip()
        if not text:
            self.store.delete_task(task_id)   # 清空内容视为删除该任务
            return
        self.store.set_task_text(task_id, text)

    def _clear_done(self) -> None:
        removed = self.store.clear_done()
        self.toast(f"已清除 {removed} 个已完成任务" if removed else "没有已完成的任务")

    # ------------------------------------------------------------ target actions
    def _set_kind(self, kind: str, refresh: bool = True) -> None:
        self._kind = kind
        for key, button in self.kind_buttons.items():
            if key == kind:
                button.set_style(fill=T.ACCENT_SOFT, fg=T.ACCENT, hover=T.ACCENT_SOFT)
            else:
                button.set_style(fill=T.SOFT, fg=T.TEXT_DIM, hover=T.SOFT_2)
        if hasattr(self.target_match, "set_placeholder"):
            self.target_match.set_placeholder(
                "例如 bilibili" if kind == "website" else "例如 steam")
        if refresh:
            self._update_hint()

    def _add_target(self) -> None:
        kind = self._kind
        name = self.target_name.get_value() if hasattr(self.target_name, "get_value") \
            else self.target_name.get().strip()
        match = self.target_match.get_value() if hasattr(self.target_match, "get_value") \
            else self.target_match.get().strip()
        if not match:
            self.toast("请填写检测条件")
            self.target_match.focus_set()
            return
        ok = self.store.add_target(kind, name, match)
        if ok:
            self.toast(f"已添加监控：{match}")
            self.target_name.reset()
            self.target_match.reset()
        else:
            self.toast("这个目标已经添加过了")

    def _scan(self) -> None:
        try:
            self._scan_available()
        except Exception as exc:
            from tkinter import messagebox
            messagebox.showerror("扫描失败", str(exc), parent=self)

    def _scan_available(self) -> None:
        kind = self._kind
        if kind == "software":
            items = monitor.running_programs()
            title = "选择要监控的程序"
            hint = "双击列表项即可填入，再点“添加”。"
        else:
            if not self.app.ensure_accessibility():
                return
            windows = [w for w in monitor.list_windows()
                       if w.get("exe") in monitor.BROWSER_EXES and not w.get("minimized") and not w.get("hidden")]
            items = [{"exe": w["exe"], "title": w["title"]} for w in windows]
            title = "选择浏览器中正在看的网站"
            hint = "列出当前所有浏览器窗口的标签页标题，双击取关键词。"
        if not items:
            self.toast("没有扫描到可选项，请先打开对应窗口")
            return
        ProgramPicker(self, items, title, hint, lambda item: self._apply_pick(kind, item))

    def _apply_pick(self, kind: str, item: dict) -> None:
        exe = (item.get("exe") or "").strip()
        title = (item.get("title") or "").strip()
        if kind == "software":
            stem = exe[:-4] if exe.endswith(".exe") else exe
            name = title.split(" - ")[0].strip() or stem
            self.target_name.set_value(name[:32])
            self.target_match.set_value(stem)
        else:
            keyword = _keyword_from_title(title)
            self.target_name.set_value(keyword[:32])
            self.target_match.set_value(keyword)
        self.toast("已填入检测条件，确认后点“添加”")
        self.target_match.focus_set()

    # ------------------------------------------------------------ settings actions
    def _on_repeat_toggle(self, value: bool) -> None:
        if self._loading:
            return
        self.store.set_setting("repeat_alert", bool(value), notify=False)
        self.app.refresh_status()

    def _on_interval_change(self, _event=None) -> None:
        if self._loading:
            return
        try:
            value = int(float(self.interval_var.get()))
        except (TypeError, ValueError):
            self.interval_var.set(str(self.store.settings.get("interval", 90)))
            return
        value = max(10, min(3600, value))
        self.interval_var.set(str(value))
        self.store.set_setting("interval", value, notify=False)
        self.app.refresh_status()

    def _on_float_toggle(self, value: bool) -> None:
        if self._loading:
            return
        self.store.set_setting("float_visible", bool(value))

    def _on_pin_toggle(self, value: bool) -> None:
        if self._loading:
            return
        self.store.set_setting("float_pinned", bool(value))

    def _on_opacity(self, value=None) -> None:
        if self._loading:
            return
        raw = float(value if value is not None else self.opacity_slider.get())
        alpha = max(0.4, min(1.0, raw / 100.0))
        self.store.set_setting("float_opacity", round(alpha, 2), notify=False)
        if hasattr(self, "opacity_value"):
            self.opacity_value.configure(text=f"{int(round(alpha * 100))}%")
        float_window = getattr(self.app, "float", None)
        if float_window is not None:
            float_window.apply_alpha(alpha)

    # ------------------------------------------------------------ status / toast
    def apply_status(self, text: str, color: str, enabled: bool) -> None:
        self.status_pill.set_state(text=text, dot=color)
        if enabled:
            self.monitor_btn.set_text("暂停监控")
            self.monitor_btn.set_style(fill=T.SOFT, fg=T.TEXT_DIM, hover=T.SOFT_2)
        else:
            self.monitor_btn.set_text("开始监控")
            self.monitor_btn.set_style(fill=T.ACCENT, fg=T.ACCENT_TEXT, hover=T.ACCENT_DARK)

    def toast(self, message: str) -> None:
        self.toast_pill.set_state(text=message)
        self.toast_pill.place(relx=0.5, rely=0.95, anchor="s")
        if self._toast_job is not None:
            try:
                self.after_cancel(self._toast_job)
            except tk.TclError:
                pass
        self._toast_job = self.after(2200, self._hide_toast)

    def _hide_toast(self) -> None:
        self._toast_job = None
        try:
            self.toast_pill.place_forget()
        except tk.TclError:
            pass


class ProgramPicker(tk.Toplevel):
    """浅色圆角的列表选择窗：扫描运行中的程序 / 浏览器标签页。"""

    def __init__(self, master, items, title, hint, on_pick):
        super().__init__(master)
        self.on_pick = on_pick
        self.all_items = list(items)
        self.filtered = list(items)
        self.configure(bg=T.BG)
        self.title(title)
        self.geometry("580x520")
        self.transient(master)
        self.minsize(480, 400)

        head = tk.Frame(self, bg=T.BG)
        head.pack(fill="x", padx=22, pady=(20, 0))
        tk.Label(head, text=title, font=T.get_font(13, "bold"), bg=T.BG, fg=T.TEXT).pack(
            anchor="w")
        tk.Label(head, text=hint, font=T.get_font(9), bg=T.BG, fg=T.MUTED).pack(
            anchor="w", pady=(4, 0))

        self.search = T.RoundedEntry(self, "搜索程序名或窗口标题…", height=42,
                                     radius=11, bg=T.BG)
        self.search.pack(fill="x", padx=22, pady=(14, 12))
        self.search.bind("<KeyRelease>", lambda _e: self._apply_filter())

        card = T.Card(self, radius=14, pad=(10, 10))
        card.pack(fill="both", expand=True, padx=22)
        list_wrap = card.body
        self.listbox = tk.Listbox(list_wrap, bg=T.SURFACE, fg=T.TEXT, font=T.get_font(9),
                                  relief="flat", bd=0, highlightthickness=0,
                                  selectbackground=T.ACCENT_SOFT,
                                  selectforeground=T.ACCENT, activestyle="none",
                                  exportselection=False)
        scrollbar = ttk.Scrollbar(list_wrap, orient="vertical",
                                  command=self.listbox.yview,
                                  style="Slim.Vertical.TScrollbar")
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=4)
        scrollbar.pack(side="right", fill="y", padx=(4, 0))
        self.listbox.bind("<Double-Button-1>", lambda _e: self._choose())
        self.listbox.bind("<Return>", lambda _e: self._choose())

        buttons = tk.Frame(self, bg=T.BG)
        buttons.pack(fill="x", padx=22, pady=(14, 18))
        T.RoundedButton(buttons, "填入所选", self._choose, height=40, padx=20,
                        radius=11).pack(side="right")
        T.RoundedButton(buttons, "取消", self.destroy, height=40, padx=18, radius=11,
                        fill=T.SURFACE, fg=T.TEXT_DIM, hover=T.SOFT_2,
                        outline=T.BORDER).pack(side="right", padx=(0, 8))

        self._fill()
        self.search.focus_set()
        self.after(60, lambda: T.style_titlebar(self, dark=False))
        self._center_on(master)

    def _fill(self) -> None:
        self.listbox.delete(0, "end")
        for item in self.filtered[:500]:
            title = item.get("title") or ""
            exe = item.get("exe") or ""
            if title and exe:
                text = f"{title}   ·   {exe}"
            else:
                text = title or exe
            self.listbox.insert("end", f"  {text}")

    def _center_on(self, master) -> None:
        try:
            master.update_idletasks()
            mx, my = master.winfo_rootx(), master.winfo_rooty()
            mw, mh = master.winfo_width(), master.winfo_height()
        except tk.TclError:
            return
        width, height = 580, 540
        x = max(0, mx + (mw - width) // 2)
        y = max(0, my + (mh - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _apply_filter(self) -> None:
        query = (self.search.get_value() if hasattr(self.search, "get_value") else "").lower()
        if query:
            self.filtered = [item for item in self.all_items
                             if query in (item.get("title") or "").lower()
                             or query in (item.get("exe") or "").lower()]
        else:
            self.filtered = list(self.all_items)
        self._fill()

    def _choose(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        index = int(selection[0])
        if index >= len(self.filtered):
            return
        item = self.filtered[index]
        self.on_pick(item)
        self.destroy()


_BROWSER_SUFFIXES = (" - google chrome", " - microsoft​edge", " - microsoft edge",
                     " — mozilla firefox", " - mozilla firefox", " - opera",
                     " - vivaldi", " - brave")


def _keyword_from_title(title: str) -> str:
    """Best-effort site keyword out of a browser tab title."""
    text = (title or "").strip()
    lowered = text.lower()
    for suffix in _BROWSER_SUFFIXES:
        if lowered.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    for separator in (" | ", " - ", " — ", " – ", " · "):
        if separator in text:
            text = text.split(separator)[0].strip()
            break
    return text[:28] or (title or "")[:28]
