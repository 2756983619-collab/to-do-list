"""简洁月历：在某一天写下要做的事，到那天自动变成任务。

只做三件事：翻月份、点日期、给这一天加/删安排。
"""

from __future__ import annotations

import calendar
import datetime as dt
import tkinter as tk

import store as store_module
import theme as T

WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")
CELL_HEIGHT = 46


def _date_str(date: dt.date) -> str:
    return date.isoformat()


def _parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


class CalendarWindow(tk.Toplevel):
    def __init__(self, app, master=None):
        super().__init__(master)
        self.app = app
        self.store = app.store
        self._today = dt.date.today()
        self._cursor = self._today.replace(day=1)
        self._selected = self._today
        self._kind = "temp"
        self._cells = {}

        self.configure(bg=T.BG)
        self.title("日历 · 安排要做的事")
        self.transient(master)
        self.minsize(400, 540)
        self.geometry("430x620")
        self.resizable(False, True)

        self._build()
        self._refresh_all()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.store.add_listener(self._on_store_change)
        self.after(60, self._place_window)

    def _place_window(self) -> None:
        """相对主窗口居中，同时切浅色标题栏。"""
        try:
            T.style_titlebar(self, dark=False)
            self.update_idletasks()
        except tk.TclError:
            return
        master = self.master if self.master is not None else self.app.root
        try:
            mx, my = master.winfo_rootx(), master.winfo_rooty()
            mw, mh = master.winfo_width(), master.winfo_height()
        except (tk.TclError, AttributeError):
            return
        if mw < 50 or mh < 50:
            return
        width, height = self.winfo_width(), self.winfo_height()
        x = mx + (mw - width) // 2
        y = my + (mh - height) // 2
        x = max(0, min(x, self.winfo_screenwidth() - width))
        y = max(0, min(y, self.winfo_screenheight() - height - 48))
        try:
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    # ------------------------------------------------------------ layout
    def _build(self) -> None:
        card = T.Card(self, radius=16, pad=(16, 14))
        card.pack(fill="both", expand=True, padx=14, pady=14)
        body = card.body
        body.pack_propagate(False)

        head = tk.Frame(body, bg=T.SURFACE)
        head.pack(fill="x")
        T.RoundedButton(head, "‹", lambda: self._shift_month(-1), width=34, height=30,
                        radius=9, fill=T.SOFT, fg=T.TEXT_DIM, hover=T.SOFT_2,
                        bg=T.SURFACE).pack(side="left")
        self.month_label = tk.Label(head, text="", font=T.get_font(12, "bold"),
                                    bg=T.SURFACE, fg=T.TEXT)
        self.month_label.pack(side="left", expand=True)
        T.RoundedButton(head, "›", lambda: self._shift_month(1), width=34, height=30,
                        radius=9, fill=T.SOFT, fg=T.TEXT_DIM, hover=T.SOFT_2,
                        bg=T.SURFACE).pack(side="right")
        T.RoundedButton(head, "今天", self._goto_today, height=30, padx=12, radius=9,
                        fill=T.SOFT, fg=T.TEXT_DIM, hover=T.SOFT_2,
                        bg=T.SURFACE).pack(side="right", padx=(0, 8))

        weekday_row = tk.Frame(body, bg=T.SURFACE)
        weekday_row.pack(fill="x", pady=(12, 4))
        for index, name in enumerate(WEEKDAYS):
            weekday_row.grid_columnconfigure(index, weight=1)
            tk.Label(weekday_row, text=name, font=T.get_font(8), bg=T.SURFACE,
                     fg=T.BORDER_STRONG).grid(row=0, column=index, sticky="ew")

        self.grid_holder = tk.Frame(body, bg=T.SURFACE)
        self.grid_holder.pack(fill="x")
        for index in range(7):
            self.grid_holder.grid_columnconfigure(index, weight=1, uniform="day")

        tk.Frame(body, bg=T.BORDER, height=1).pack(fill="x", pady=(12, 0))

        self.selected_label = tk.Label(body, text="", font=T.get_font(10, "bold"),
                                       bg=T.SURFACE, fg=T.TEXT, anchor="w")
        self.selected_label.pack(fill="x", pady=(12, 6))

        self.event_list = T.ScrollFrame(body, bg=T.SURFACE, max_height=190)
        self.event_list.pack(fill="x")

        add = tk.Frame(body, bg=T.SURFACE)
        add.pack(fill="x", pady=(10, 0))
        chips = tk.Frame(add, bg=T.SURFACE)
        chips.pack(side="left", padx=(0, 8))
        self.kind_buttons = {}
        for kind, label in (("temp", "临时"), ("daily", "每日")):
            button = T.RoundedButton(chips, label, height=36, padx=10, radius=10,
                                     fill=T.SOFT, fg=T.TEXT_DIM, hover=T.SOFT_2,
                                     font=T.get_font(8, "bold"), bg=T.SURFACE)
            button.pack(side="left", padx=(0, 6))
            button._command = (lambda k=kind: self._set_kind(k))
            self.kind_buttons[kind] = button
        self.entry = T.RoundedEntry(add, "这一天要做什么？", height=36, radius=10,
                                    bg=T.SURFACE, padx=10)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _e: self._add_event())
        T.RoundedButton(add, "添加", self._add_event, height=36, padx=14, radius=10,
                        bg=T.SURFACE).pack(side="left", padx=(8, 0))

        tk.Label(body, text="到了这一天，会自动出现在任务清单里",
                 font=T.get_font(8), bg=T.SURFACE, fg=T.BORDER_STRONG,
                 anchor="w").pack(fill="x", pady=(8, 0))

        self._set_kind("temp")

    # ------------------------------------------------------------ data
    def _shift_month(self, delta: int) -> None:
        year = self._cursor.year + (self._cursor.month - 1 + delta) // 12
        month = (self._cursor.month - 1 + delta) % 12 + 1
        self._cursor = dt.date(year, month, 1)
        self._refresh_all()

    def _goto_today(self) -> None:
        self._today = dt.date.today()
        self._cursor = self._today.replace(day=1)
        self._selected = self._today
        self._refresh_all()

    def _select(self, date: dt.date) -> None:
        self._selected = date
        self._refresh_grid()
        self._refresh_selected()

    def _refresh_all(self) -> None:
        self.month_label.configure(
            text=f"{self._cursor.year} 年 {self._cursor.month} 月")
        self._refresh_grid()
        self._refresh_selected()

    def _month_bounds(self) -> tuple[str, str]:
        last_day = calendar.monthrange(self._cursor.year, self._cursor.month)[1]
        first = self._cursor.replace(day=1)
        last = self._cursor.replace(day=last_day)
        return _date_str(first), _date_str(last)

    def _refresh_grid(self) -> None:
        for child in self.grid_holder.winfo_children():
            child.destroy()
        self._cells = {}

        self._today = dt.date.today()
        first, last = self._month_bounds()
        marks = {}
        for event in self.store.events_between(first, last):
            marks.setdefault(event["date"], []).append(event)

        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(
            self._cursor.year, self._cursor.month)
        for row, week in enumerate(weeks):
            self.grid_holder.grid_rowconfigure(row, weight=1)
            for column, date in enumerate(week):
                in_month = date.month == self._cursor.month
                is_today = date == self._today
                is_selected = date == self._selected
                count = len(marks.get(_date_str(date), []))

                cell = tk.Frame(self.grid_holder, bg=T.SURFACE,
                                highlightthickness=1,
                                highlightbackground=T.BORDER if not is_selected
                                else T.ACCENT,
                                cursor="hand2", height=CELL_HEIGHT)
                cell.grid(row=row, column=column, padx=1, pady=1, sticky="nsew")
                cell.pack_propagate(False)

                color = T.TEXT if in_month else T.BORDER_STRONG
                if is_today:
                    color = T.ACCENT
                number = tk.Label(cell, text=str(date.day),
                                  font=T.get_font(9, "bold" if is_today or is_selected
                                                  else "normal"),
                                  bg=T.SURFACE, fg=color)
                number.pack(side="top", pady=(4, 0))

                if count:
                    dot = tk.Label(cell, text="•" * min(count, 3), font=T.get_font(7),
                                   bg=T.SURFACE,
                                   fg=T.ACCENT if is_today else T.GREEN)
                    dot.pack(side="top")
                    dot.bind("<Button-1>", lambda _e, d=date: self._select(d))
                if is_today:
                    cell.configure(highlightbackground=T.ACCENT_SOFT)

                for widget in (cell, number):
                    widget.bind("<Button-1>", lambda _e, d=date: self._select(d))
                self._cells[_date_str(date)] = cell

    def _refresh_selected(self) -> None:
        for child in self.event_list.inner.winfo_children():
            child.destroy()

        date = self._selected
        suffix = "（今天）" if date == self._today else ""
        self.selected_label.configure(
            text=f"{date.month} 月 {date.day} 日 · 周{WEEKDAYS[date.weekday()]}{suffix}")

        events = self.store.events_on(_date_str(date))
        if not events:
            tk.Label(self.event_list.inner, text="这一天还没有安排",
                     font=T.get_font(9), bg=T.SURFACE, fg=T.MUTED, anchor="w",
                     padx=8, pady=10).pack(fill="x")
            return
        for event in events:
            row = tk.Frame(self.event_list.inner, bg=T.SURFACE)
            row.pack(fill="x", pady=1)
            tk.Label(row, text="•", font=T.get_font(9), bg=T.SURFACE,
                     fg=T.GREEN if event.get("pulled") else T.ACCENT).pack(
                side="left", padx=(6, 6))
            tk.Label(row, text=event["text"], font=T.get_font(9), bg=T.SURFACE,
                     fg=T.TEXT_DIM if event.get("pulled") else T.TEXT,
                     anchor="w").pack(side="left", fill="x", expand=True)
            tk.Label(row, text="每日" if event.get("kind") == "daily" else "临时",
                     font=T.get_font(7), bg=T.SURFACE,
                     fg=T.BORDER_STRONG).pack(side="left", padx=(4, 6))
            if event.get("pulled"):
                tk.Label(row, text="已加入", font=T.get_font(7), bg=T.SURFACE,
                         fg=T.GREEN).pack(side="left", padx=(0, 4))
            delete = tk.Label(row, text="✕", font=T.get_font(8), bg=T.SURFACE,
                              fg=T.MUTED, padx=6, cursor="hand2")
            delete.pack(side="right")
            delete.bind("<Button-1>",
                        lambda _e, eid=event["id"]: self.store.delete_event(eid))
            T.attach_hover([delete], T.SURFACE, T.RED)

    # ------------------------------------------------------------ actions
    def _set_kind(self, kind: str) -> None:
        self._kind = kind if kind in ("temp", "daily") else "temp"
        for key, button in self.kind_buttons.items():
            if key == self._kind:
                button.set_style(fill=T.ACCENT_SOFT, fg=T.ACCENT, hover=T.ACCENT_SOFT)
            else:
                button.set_style(fill=T.SOFT, fg=T.TEXT_DIM, hover=T.SOFT_2)

    def _add_event(self) -> None:
        text = self.entry.get_value() if hasattr(self.entry, "get_value") \
            else self.entry.get().strip()
        if not text:
            return
        self.store.add_event(_date_str(self._selected), text, self._kind)
        if hasattr(self.entry, "reset"):
            self.entry.reset()
        else:
            self.entry.delete(0, "end")
        self.app.sync_calendar()

    def _on_store_change(self, reason: str) -> None:
        if reason not in ("events", "tasks"):
            return
        try:
            if not self.winfo_exists():
                return
            self._refresh_all()
        except tk.TclError:
            pass

    def _close(self) -> None:
        try:
            self.destroy()
        except tk.TclError:
            pass
