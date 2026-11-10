"""浅色主题：配色、字体与圆角控件。

所有「圆角」都由 Canvas 手绘：圆角矩形用多段折线逼近圆弧，
窗口级圆角则配合 Windows 的 -transparentcolor 色键把四角挖空。
"""

from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# ------------------------------------------------------------------ 颜色
BG = "#f1f3f9"              # 窗口底色
SURFACE = "#ffffff"          # 卡片
SOFT = "#f5f7fc"             # 输入框 / 次级面
SOFT_2 = "#edf1f9"           # 悬停
BORDER = "#e4e8f2"           # 细边
BORDER_STRONG = "#d3dae9"
TEXT = "#1b2130"
TEXT_DIM = "#4e5769"
MUTED = "#8f99ab"
ACCENT = "#3f6bff"
ACCENT_DARK = "#2d55db"
ACCENT_SOFT = "#e9efff"
ACCENT_TEXT = "#ffffff"
GREEN = "#12b886"
GREEN_SOFT = "#e2f7ef"
RED = "#ef4d5a"
RED_SOFT = "#fdecef"
AMBER = "#e79a1c"
AMBER_SOFT = "#fdf3e2"
FAMILY = "Microsoft YaHei UI"

KEY = "#ff01fe"              # 透明色键，只用于圆角窗口的挖空区

# 兼容旧命名的别名
PANEL = SURFACE
PANEL_ALT = SOFT
PANEL_HOVER = SOFT_2


# ------------------------------------------------------------------ 基础工具
def get_font(size, weight="normal", overstrike=False):
    return (FAMILY, size, weight, "overstrike") if overstrike else (FAMILY, size, weight)


def measure(text, font, root=None):
    """粗略测量文本像素宽度。"""
    try:
        return tkfont.Font(root=root, font=font).measure(text or "")
    except tk.TclError:
        return int(len(text or "") * 9)


def _to_rgb(color):
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def darken(color, amount=0.10):
    r, g, b = _to_rgb(color)
    return _to_hex((r * (1 - amount), g * (1 - amount), b * (1 - amount)))


def lighten(color, amount=0.10):
    r, g, b = _to_rgb(color)
    return _to_hex((r + (255 - r) * amount, g + (255 - g) * amount, b + (255 - b) * amount))


def with_alpha(color, alpha):
    """把颜色和底色混合，模拟半透明填充。"""
    r, g, b = _to_rgb(color)
    br, bg, bb = _to_rgb(SURFACE)
    return _to_hex((r * alpha + br * (1 - alpha),
                    g * alpha + bg * (1 - alpha),
                    b * alpha + bb * (1 - alpha)))


def parent_bg(widget, fallback=BG):
    try:
        return widget.cget("bg")
    except (tk.TclError, AttributeError):
        return fallback


def round_points(x1, y1, x2, y2, r, steps=10):
    """圆角矩形轮廓点，两条坐标依次排列。"""
    r = max(0.0, min(float(r), (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    points = []

    def arc(cx, cy, start):
        for i in range(steps + 1):
            angle = start + (math.pi / 2.0) * i / steps
            points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))

    arc(x1 + r, y1 + r, math.pi)               # 左上
    arc(x2 - r, y1 + r, -math.pi / 2.0)        # 右上
    arc(x2 - r, y2 - r, 0.0)                   # 右下
    arc(x1 + r, y2 - r, math.pi / 2.0)         # 左下

    flat = []
    for x, y in points:
        flat.extend((x, y))
    return flat


def draw_round(canvas, x1, y1, x2, y2, r, fill=SURFACE, outline="", width=1, tags=()):
    return canvas.create_polygon(
        round_points(x1, y1, x2, y2, r),
        fill=fill, outline=outline, width=width, joinstyle="round", tags=tags)


def attach_hover(widgets, normal, hover):
    for widget in widgets:
        widget.bind("<Enter>", lambda _e, w=widget: w.configure(bg=hover), add="+")
        widget.bind("<Leave>", lambda _e, w=widget: w.configure(bg=normal), add="+")


# ------------------------------------------------------------------ 圆角卡片
class Card(tk.Canvas):
    """白底圆角卡片，内容放进 self.body。"""

    def __init__(self, master, radius=16, fill=SURFACE, outline=BORDER,
                 pad=(18, 16), bg=None, **kwargs):
        super().__init__(master, bg=bg or parent_bg(master), bd=0,
                         highlightthickness=0, **kwargs)
        self._radius = radius
        self._fill = fill
        self._outline = outline
        self._pad = pad
        self._shape = None
        self.body = tk.Frame(self, bg=fill)
        self._window = self.create_window(0, 0, window=self.body, anchor="nw")
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event=None):
        width, height = max(self.winfo_width(), 2), max(self.winfo_height(), 2)
        if self._shape is not None:
            self.delete(self._shape)
        self._shape = draw_round(self, 0.5, 0.5, width - 0.5, height - 0.5,
                                 self._radius, fill=self._fill, outline=self._outline)
        self.tag_lower(self._shape)
        px, py = self._pad
        self.coords(self._window, px, py)
        self.itemconfigure(self._window,
                           width=max(1, width - 2 * px),
                           height=max(1, height - 2 * py))


class RoundedShell(tk.Canvas):
    """给无边框窗口用的圆角底板：内容放进 self.body。

    窗口本身设成色键透明色，只保留这张圆角卡片可见。
    """

    def __init__(self, master, radius=18, fill=SURFACE, outline=BORDER,
                 pad=(1, 1), bg=KEY):
        super().__init__(master, bg=bg, bd=0, highlightthickness=0)
        self._radius = radius
        self._fill = fill
        self._outline = outline
        self._pad = pad
        self._shape = None
        self.body = tk.Frame(self, bg=fill)
        self._window = self.create_window(0, 0, window=self.body, anchor="nw")
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event=None):
        width, height = max(self.winfo_width(), 2), max(self.winfo_height(), 2)
        if self._shape is not None:
            self.delete(self._shape)
        self._shape = draw_round(self, 0.5, 0.5, width - 0.5, height - 0.5,
                                 self._radius, fill=self._fill, outline=self._outline)
        self.tag_lower(self._shape)
        px, py = self._pad
        self.coords(self._window, px, py)
        self.itemconfigure(self._window,
                           width=max(1, width - 2 * px),
                           height=max(1, height - 2 * py))

    def relayout(self):
        """窗口尺寸刚被改过时强制重排一次，不依赖 Configure 事件的到达时机。"""
        try:
            self.update_idletasks()
        except tk.TclError:
            return
        if self.winfo_width() > 1:
            self._on_configure()


# ------------------------------------------------------------------ 状态胶囊
class Pill(tk.Canvas):
    """圆角标签：可带一个小圆点，用于监控状态提示。"""

    def __init__(self, master, text="", *, dot=None, height=34, radius=None,
                 fill=SOFT, outline=BORDER, fg=TEXT_DIM, font=None, padx=14, bg=None):
        self._font = font or get_font(9)
        super().__init__(master, height=height, width=120, bg=bg or parent_bg(master),
                         bd=0, highlightthickness=0)
        self._radius = radius if radius is not None else height / 2
        self._fill, self._outline, self._fg = fill, outline, fg
        self._dot = dot
        self._padx = padx
        self._height = height
        self._text = text
        self._paint()

    def _paint(self):
        self.delete("all")
        h = self._height
        text_width = measure(self._text, self._font)
        dot_space = 16 if self._dot else 0
        width = max(h, int(text_width + dot_space + 2 * self._padx))
        self.configure(width=width)
        draw_round(self, 0.5, 0.5, width - 0.5, h - 0.5, self._radius,
                   fill=self._fill, outline=self._outline)
        x = self._padx
        if self._dot:
            cy = h / 2
            self.create_oval(x, cy - 3.5, x + 7, cy + 3.5, fill=self._dot, outline="")
            x += 15
        self.create_text(x, h / 2, text=self._text, anchor="w",
                         fill=self._fg, font=self._font)

    def set_state(self, text=None, dot=None, fg=None, fill=None):
        if text is not None:
            self._text = text
        if dot is not None:
            self._dot = dot
        if fg is not None:
            self._fg = fg
        if fill is not None:
            self._fill = fill
        self._paint()


# ------------------------------------------------------------------ 按钮
class RoundedButton(tk.Canvas):
    def __init__(self, master, text="", command=None, *, fill=ACCENT, hover=None,
                 fg=ACCENT_TEXT, outline="", radius=10, font=None, padx=20,
                 height=38, width=None, bg=None):
        self._font = font or get_font(9, "bold")
        self._text = text
        self._radius = radius
        self._height = height
        self._command = command
        self._fill, self._fg, self._outline = fill, fg, outline
        self._hover = hover or darken(fill, 0.08)
        self._hovering = False
        self._padx = padx
        w = width or int(measure(text, self._font) + 2 * padx)
        super().__init__(master, width=w, height=height, bg=bg or parent_bg(master),
                         bd=0, highlightthickness=0, cursor="hand2")
        self._paint()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _paint(self):
        self.delete("all")
        width = self.winfo_width()
        if width <= 1:
            width = int(self["width"])
        height = self._height
        draw_round(self, 0.5, 0.5, width - 0.5, height - 0.5, self._radius,
                   fill=self._hover if self._hovering else self._fill,
                   outline=self._outline)
        self.create_text(width / 2, height / 2, text=self._text,
                         fill=self._fg, font=self._font)

    def _on_enter(self, _event=None):
        self._hovering = True
        self._paint()

    def _on_leave(self, _event=None):
        self._hovering = False
        self._paint()

    def _on_press(self, _event=None):
        self._hovering = False
        self._paint()

    def _on_release(self, event=None):
        self._hovering = True
        self._paint()
        if event is not None:
            if 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self._height:
                if self._command is not None:
                    self._command()

    def set_text(self, text):
        if text == self._text:
            return
        self._text = text
        width = int(max(measure(text, self._font) + 2 * self._padx, 60))
        self.configure(width=width)
        self._paint()

    def set_style(self, fill=None, fg=None, hover=None, outline=None):
        if fill is not None:
            self._fill = fill
        if fg is not None:
            self._fg = fg
        if hover is not None:
            self._hover = hover
        if outline is not None:
            self._outline = outline
        self._paint()


# ------------------------------------------------------------------ 滑块
class Slider(tk.Canvas):
    def __init__(self, master, *, from_=0, to=100, value=0, width=110, height=24,
                 command=None, bg=None):
        super().__init__(master, width=width, height=height, bg=bg or parent_bg(master),
                         bd=0, highlightthickness=0, cursor="hand2")
        self._from = float(from_)
        self._to = float(to)
        self._value = float(value)
        self._command = command
        self._width = width
        self._height = height
        self._paint()
        self.bind("<Button-1>", self._on_drag)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<Configure>", lambda _e: self._paint())

    def _real_width(self):
        width = self.winfo_width()
        return width if width > 1 else self._width

    def _paint(self):
        self.delete("all")
        width = self._real_width()
        height = self._height
        pad = 8
        y = height / 2
        self.create_line(pad, y, width - pad, y, fill=BORDER_STRONG, width=4,
                         capstyle="round")
        span = max(1e-6, self._to - self._from)
        ratio = max(0.0, min(1.0, (self._value - self._from) / span))
        x = pad + (width - 2 * pad) * ratio
        if x > pad + 0.5:
            self.create_line(pad, y, x, y, fill=ACCENT, width=4, capstyle="round")
        self.create_oval(x - 7, y - 7, x + 7, y + 7, fill="#ffffff", outline=ACCENT,
                         width=2)
        self._ratio = ratio

    def _on_drag(self, event):
        width = self._real_width()
        pad = 8
        ratio = (event.x - pad) / max(1, width - 2 * pad)
        ratio = max(0.0, min(1.0, ratio))
        value = self._from + (self._to - self._from) * ratio
        self._value = value
        self._paint()
        if self._command is not None:
            self._command(value)

    def set(self, value, notify=False):
        self._value = max(self._from, min(self._to, float(value)))
        self._paint()
        if notify and self._command is not None:
            self._command(self._value)

    def get(self):
        return self._value


# ------------------------------------------------------------------ 输入框
_NAV_KEYS = {
    "Left", "Right", "Home", "End", "Up", "Down", "Prior", "Next",
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
    "Tab", "ISO_Left_Tab", "Escape", "Caps_Lock",
}


class RoundedEntry(tk.Canvas):
    """圆角输入框：Canvas 画底，内部嵌一个无边框 Entry。"""

    def __init__(self, master, placeholder=None, *, height=40, radius=10,
                 fill=SOFT, outline=BORDER, focus_outline=ACCENT, padx=13,
                 font=None, bg=None, width=120):
        super().__init__(master, height=height, width=width, bg=bg or parent_bg(master),
                         bd=0, highlightthickness=0)
        self._height = height
        self._radius = radius
        self._fill = fill
        self._outline = outline
        self._focus_outline = focus_outline
        self._padx = padx
        self._focused = False
        self.entry = tk.Entry(self, bg=fill, fg=TEXT, relief="flat", bd=0,
                              insertbackground=TEXT, highlightthickness=0,
                              font=font or get_font(10), disabledbackground=fill,
                              disabledforeground=MUTED, selectbackground=ACCENT_SOFT,
                              selectforeground=TEXT)
        self._window = self.create_window(padx, height / 2, window=self.entry, anchor="w")
        self.entry.bind("<FocusIn>", self._on_focus_in, add="+")
        self.entry.bind("<FocusOut>", self._on_focus_out, add="+")
        self.bind("<Configure>", self._on_configure)
        self._shape = None
        self._paint()
        self._placeholder_active = False
        self._placeholder = placeholder
        if placeholder:
            self._install_placeholder(placeholder)

    # -- 绘制
    def _paint(self):
        width = self.winfo_width()
        if width <= 1:
            width = int(self["width"])
        height = self._height
        if self._shape is not None:
            self.delete(self._shape)
        outline = self._focus_outline if self._focused else self._outline
        self._shape = draw_round(self, 0.5, 0.5, width - 0.5, height - 0.5,
                                 self._radius, fill=self._fill, outline=outline,
                                 width=1.5 if self._focused else 1)
        self.tag_lower(self._shape)

    def _on_configure(self, event=None):
        self._paint()
        width = max(self.winfo_width(), 2)
        self.coords(self._window, self._padx, self._height / 2)
        self.itemconfigure(self._window,
                           width=max(1, width - 2 * self._padx - 2))

    def _on_focus_in(self, _event=None):
        self._focused = True
        self._paint()

    def _on_focus_out(self, _event=None):
        self._focused = False
        self._paint()

    # -- 占位提示
    def _install_placeholder(self, placeholder):
        """占位提示：点击/聚焦即消失，失焦且为空时再出现。"""
        entry = self.entry
        state = {"active": False}

        def show(text):
            state["active"] = False
            entry.delete(0, "end")
            entry.insert(0, text)
            entry.configure(fg=MUTED)
            try:
                entry.icursor(0)
            except tk.TclError:
                pass

        def hide(_event=None):
            if state["active"]:
                return None
            state["active"] = True
            entry.delete(0, "end")
            entry.configure(fg=TEXT)
            return None

        def restore(_event=None):
            if state["active"] and not entry.get().strip():
                show(self._placeholder)
            return None

        def on_key(event):
            # 还没输入过内容时，普通字符要先清掉提示再插入
            if state["active"] or event.keysym in _NAV_KEYS:
                return None
            if event.state & 0x4 or event.state & 0x8:
                hide()
                return None
            if event.char and event.char >= " ":
                hide()
                return None
            return "break"

        entry.bind("<FocusIn>", hide, add="+")
        entry.bind("<Button-1>", hide, add="+")
        entry.bind("<FocusOut>", restore, add="+")
        entry.bind("<KeyPress>", on_key, add="+")

        self._placeholder_state = state
        self._show_placeholder = show

        def get_value():
            return entry.get().strip() if state["active"] else ""

        def set_value(value):
            state["active"] = True
            entry.delete(0, "end")
            entry.insert(0, value)
            entry.configure(fg=TEXT)
            entry.icursor("end")

        def reset():
            show(self._placeholder)

        self.get_value = get_value
        self.set_value = set_value
        self.reset = reset
        show(placeholder)

    def set_placeholder(self, text):
        self._placeholder = text
        if not getattr(self, "_placeholder_state", {}).get("active"):
            if hasattr(self, "_show_placeholder"):
                self._show_placeholder(text)

    def get_value(self):
        return self.entry.get().strip()

    def set_value(self, value):
        self.entry.delete(0, "end")
        self.entry.insert(0, value)

    def reset(self):
        self.entry.delete(0, "end")

    def focus_set(self):
        try:
            self.entry.focus_set()
        except tk.TclError:
            pass

    def bind(self, sequence=None, func=None, add=None):
        if sequence is None:
            return super().bind()
        self.entry.bind(sequence, func, add)
        return None


# ------------------------------------------------------------------ 勾选框
class CheckBox(tk.Canvas):
    def __init__(self, master, value=False, command=None, *, size=18,
                 bg=None, accent=ACCENT, radius=None):
        super().__init__(master, width=size, height=size, bg=bg or parent_bg(master),
                         bd=0, highlightthickness=0, cursor="hand2")
        self._size = size
        self._radius = radius if radius is not None else max(4, size * 0.28)
        self._accent = accent
        self._value = bool(value)
        self._command = command
        self._hover = False
        self._paint()
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _paint(self):
        self.delete("all")
        s = self._size
        pad = 1
        if self._value:
            draw_round(self, pad, pad, s - pad, s - pad, self._radius,
                       fill=self._accent, outline=self._accent)
            self.create_line(s * 0.28, s * 0.52, s * 0.43, s * 0.67,
                             s * 0.73, s * 0.35, fill="#ffffff", width=2,
                             capstyle="round", joinstyle="round")
        else:
            outline = self._accent if self._hover else BORDER_STRONG
            draw_round(self, pad, pad, s - pad, s - pad, self._radius,
                       fill="#ffffff", outline=outline)

    def _on_enter(self, _event=None):
        self._hover = True
        self._paint()

    def _on_leave(self, _event=None):
        self._hover = False
        self._paint()

    def set_value(self, value):
        self._value = bool(value)
        self._paint()

    def get_value(self):
        return self._value

    def _on_click(self, _event=None):
        self._value = not self._value
        self._paint()
        if self._command is not None:
            self._command(self._value)


# ------------------------------------------------------------------ 开关
class ToggleSwitch(tk.Canvas):
    def __init__(self, master, value=False, command=None, *, bg=None, width=46,
                 height=26, on_color=ACCENT):
        super().__init__(master, width=width, height=height, bg=bg or parent_bg(master),
                         bd=0, highlightthickness=0, cursor="hand2")
        self._sw = width
        self._sh = height
        self._value = bool(value)
        self._command = command
        self._on_color = on_color
        self._pos = 1.0 if self._value else 0.0
        self._paint()
        self.bind("<Button-1>", self._on_click)

    def _paint(self):
        self.delete("all")
        w, h = self._sw, self._sh
        if self._value:
            track, outline = self._on_color, self._on_color
        else:
            track, outline = SOFT_2, BORDER_STRONG
        draw_round(self, 0.5, 0.5, w - 0.5, h - 0.5, h / 2, fill=track,
                   outline=outline)
        pad = 3
        r = (h - 2 * pad) / 2
        cx = pad + r + (w - 2 * pad - 2 * r) * self._pos
        cy = h / 2
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#ffffff",
                         outline=BORDER if not self._value else "", width=1)

    def _animate_to(self, target):
        start = self._pos
        steps = 5

        def step(i=1):
            if i > steps:
                self._pos = target
                self._paint()
                return
            self._pos = start + (target - start) * (i / steps)
            self._paint()
            self.after(14, lambda: step(i + 1))

        step()

    def set_value(self, value, animate=True):
        value = bool(value)
        if value == self._value:
            return
        self._value = value
        if animate:
            self._animate_to(1.0 if value else 0.0)
        else:
            self._pos = 1.0 if value else 0.0
            self._paint()

    def get_value(self):
        return self._value

    def _on_click(self, _event=None):
        self._value = not self._value
        self._animate_to(1.0 if self._value else 0.0)
        if self._command is not None:
            self._command(self._value)


# ------------------------------------------------------------------ 滚动区
class ScrollFrame(tk.Frame):
    def __init__(self, master, bg=SURFACE, max_height=None):
        super().__init__(master, bg=bg)
        self._bg = bg
        self._max_height = max_height
        self.canvas = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                  style="Slim.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas._wheel_canvas = self.canvas

    def _on_inner_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        if self._max_height:
            wanted = max(24, min(self.inner.winfo_reqheight(), self._max_height))
            if int(self.canvas["height"]) != wanted:
                self.canvas.configure(height=wanted)

    def _on_canvas_configure(self, event=None):
        self.canvas.itemconfigure(self._window, width=max(1, self.canvas.winfo_width()))

    def _on_scroll(self, first, last):
        self.vbar.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.vbar.pack_forget()
        else:
            self.vbar.pack(side="right", fill="y", padx=(2, 0))

    def refresh(self):
        """内容变更后重新量高，避免高度停留在此前的值。"""
        try:
            self.update_idletasks()
        except tk.TclError:
            return
        self._on_inner_configure()

    def scroll_to_end(self):
        self.canvas.update_idletasks()
        self.canvas.yview_moveto(1.0)


def install_wheel_router(root):
    """滚轮事件只作用于指针下面那个滚动区。"""

    def on_wheel(event):
        widget = root.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            canvas = getattr(widget, "_wheel_canvas", None)
            if canvas is not None:
                try:
                    canvas.yview_scroll(int(-event.delta / 120), "units")
                except tk.TclError:
                    return "break"
                return "break"
            widget = widget.master
        return None

    root.bind_all("<MouseWheel>", on_wheel)


# ------------------------------------------------------------------ ttk 样式
def style_widgets(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.layout("Slim.Vertical.TScrollbar", [
        ("Vertical.Scrollbar.trough", {
            "sticky": "ns",
            "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
        }),
    ])
    style.configure("Slim.Vertical.TScrollbar",
                    troughcolor=SURFACE, background=BORDER_STRONG,
                    darkcolor=BORDER_STRONG, lightcolor=BORDER_STRONG,
                    bordercolor=SURFACE, arrowcolor=MUTED, width=8, relief="flat")
    style.map("Slim.Vertical.TScrollbar",
              background=[("active", MUTED), ("pressed", TEXT_DIM)])

    style.configure("App.TSpinbox",
                    fieldbackground=SOFT, background=SOFT, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=SOFT, darkcolor=SOFT,
                    arrowcolor=TEXT_DIM, arrowsize=11, relief="flat", padding=(6, 3))
    style.map("App.TSpinbox",
              fieldbackground=[("disabled", SOFT)],
              bordercolor=[("focus", ACCENT), ("!focus", BORDER)])
    style.configure("App.Horizontal.TScale",
                    background=ACCENT, troughcolor=SOFT_2, borderwidth=0,
                    sliderlength=16, sliderthickness=8)
    return style


def style_titlebar(window, dark=False):
    """把原生标题栏切成浅色（Windows 10/11 深色标题栏属性的反向操作）。"""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return
    window.update_idletasks()
    try:
        hwnd = wintypes.HWND(int(window.wm_frame(), 16))
    except (ValueError, tk.TclError):
        try:
            hwnd = wintypes.HWND(window.winfo_id())
        except tk.TclError:
            return
    value = ctypes.c_int(1 if dark else 0)
    try:
        dwm = ctypes.windll.dwmapi
    except AttributeError:
        return
    for attribute in (20, 19):
        if dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                     ctypes.sizeof(value)) == 0:
            break


# 旧名字保留
def enable_dark_titlebar(window):
    style_titlebar(window, dark=True)


def place_top_right(win, width, height, margin=36, top=120):
    screen_w = win.winfo_screenwidth()
    x = screen_w - width - margin
    win.geometry(f"{width}x{height}+{x}+{top}")


def clamp_position(pos, width, height, win):
    if not pos:
        return None
    try:
        x, y = int(pos[0]), int(pos[1])
    except (TypeError, ValueError):
        return None
    return max(-width + 80, min(win.winfo_screenwidth() - 80, x)), \
        max(0, min(win.winfo_screenheight() - 60, y))
