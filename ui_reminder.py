"""置顶的“专注提醒”弹窗：圆角 + 半透明卡片。"""

from __future__ import annotations

import tkinter as tk
import winsound

import theme as T

AUTO_CLOSE_MS = 22000
RADIUS = 18
WIDTH = 456
ALPHA = 0.93


class ReminderPopup(tk.Toplevel):
    """Small always-on-top card that shows which distractor was detected."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.withdraw()
        self.title("专注提醒")          # 标题栏不可见，但对辅助功能 / 自动化识别有用
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=T.KEY)
        self._close_job = None
        try:
            self.attributes("-transparentcolor", T.KEY)
        except tk.TclError:
            pass
        self._build()
        try:
            self.attributes("-alpha", ALPHA)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ layout
    def _build(self) -> None:
        self.shell = T.RoundedShell(self, radius=RADIUS, fill=T.SURFACE,
                                    outline=T.BORDER, pad=(RADIUS, RADIUS))
        self.shell.pack(fill="both", expand=True)
        self.inner = self.shell.body
        self.inner.configure(bg=T.SURFACE)

        head = tk.Frame(self.inner, bg=T.SURFACE)
        head.pack(fill="x")

        badge = tk.Canvas(head, width=46, height=46, bg=T.SURFACE, bd=0,
                          highlightthickness=0)
        badge.pack(side="left")
        T.draw_round(badge, 0.5, 0.5, 45.5, 45.5, 14, fill=T.AMBER_SOFT, outline="")
        badge.create_text(23, 24, text="⏰", font=T.get_font(15))

        titles = tk.Frame(head, bg=T.SURFACE)
        titles.pack(side="left", padx=(13, 0), pady=2)
        tk.Label(titles, text="专注提醒", font=T.get_font(14, "bold"), bg=T.SURFACE,
                 fg=T.TEXT).pack(anchor="w")
        tk.Label(titles, text="检测到干扰源正在运行，回到工作吧！", font=T.get_font(9),
                 bg=T.SURFACE, fg=T.MUTED).pack(anchor="w", pady=(3, 0))

        close = T.RoundedButton(head, "✕", self.close, width=28, height=28, padx=0,
                                radius=9, fill=T.SURFACE, fg=T.MUTED, hover=T.SOFT_2,
                                font=T.get_font(9), bg=T.SURFACE)
        close.pack(side="right", pady=(2, 0))

        self.body_label = tk.Label(self.inner, text="", font=T.get_font(10), bg=T.SURFACE,
                                   fg=T.TEXT_DIM, justify="left", anchor="w",
                                   wraplength=WIDTH - 2 * RADIUS - 6)
        self.body_label.pack(fill="x", pady=(16, 16))

        buttons = tk.Frame(self.inner, bg=T.SURFACE)
        buttons.pack(fill="x", pady=(2, 4))
        T.RoundedButton(buttons, "暂停监控 10 分钟", self._snooze, height=40, padx=16,
                        radius=11, fill=T.SOFT, fg=T.TEXT_DIM, hover=T.SOFT_2,
                        font=T.get_font(9), bg=T.SURFACE).pack(side="left")
        T.RoundedButton(buttons, "马上专注", self.close, height=40, padx=22, radius=11,
                        font=T.get_font(10, "bold"), bg=T.SURFACE).pack(side="right")

        self.bind("<Escape>", lambda _e: self.close())

    # ------------------------------------------------------------ public api
    def show(self, alerts: list[dict]) -> None:
        if not alerts:
            return
        lines = []
        for alert in alerts[:3]:
            name = alert.get("name") or "未命名目标"
            kind = "网站" if alert.get("kind") == "website" else "程序"
            detail = alert.get("detail") or ""
            if detail and detail.lower() != name.lower():
                lines.append(f"• {name}（{kind}）· {detail[:48]}")
            else:
                lines.append(f"• {name}（{kind}）")
        extra = len(alerts) - 3
        text = "正在运行：\n" + "\n".join(lines)
        if extra > 0:
            text += f"\n另外还有 {extra} 个目标…"
        self.body_label.configure(text=text)
        self.title(f"专注提醒 · {alerts[0].get('name') or '干扰源'}")
        self._apply_size()
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(30, self._grab_focus)
        self._beep()
        if self._close_job is not None:
            try:
                self.after_cancel(self._close_job)
            except tk.TclError:
                pass
        self._close_job = self.after(AUTO_CLOSE_MS, self.close)

    def close(self) -> None:
        if self._close_job is not None:
            try:
                self.after_cancel(self._close_job)
            except tk.TclError:
                pass
            self._close_job = None
        self.withdraw()

    def apply_theme(self) -> None:  # reserved for future theming hooks
        pass

    # ------------------------------------------------------------ internals
    def _apply_size(self) -> None:
        self.update_idletasks()
        height = self.inner.winfo_reqheight() + 2 * RADIUS + 2
        screen_w = self.winfo_screenwidth()
        x = max(20, (screen_w - WIDTH) // 2)
        y = 96
        self.geometry(f"{WIDTH}x{height}+{x}+{y}")
        self.shell.relayout()

    def _grab_focus(self) -> None:
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def _snooze(self) -> None:
        self.app.snooze(600)
        self.close()

    def _beep(self) -> None:
        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
