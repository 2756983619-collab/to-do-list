"""Opt-in macOS pixel/input regression: TODO_LIST_NATIVE_TESTS=1 python -m unittest tests.test_native_overlay -v."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == 'darwin' and os.environ.get('TODO_LIST_NATIVE_TESTS') == '1',
                     'requires macOS desktop, Accessibility and screen capture')
class NativeOverlayTests(unittest.TestCase):
    def test_no_black_rim_and_drag_does_not_activate_app(self):
        import AppKit as A
        import Quartz as Q
        import app

        external = A.NSWorkspace.sharedWorkspace().frontmostApplication()
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'TODO_LIST_DATA': str(Path(folder) / 'todo.json')}):
            instance = app.App()
            task_id = instance.store.add_task("悬浮窗点击测试")
            def pump(seconds=0.15):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    instance.root.update()
                    time.sleep(0.01)
            try:
                instance.float.show()
                instance.float.geometry('+400+200')
                pump()
                own = A.NSApplication.sharedApplication()
                floating = next(w for w in own.windows() if str(w.title()) == instance.float.title())
                self.assertTrue(floating.isKindOfClass_(A.NSPanel))
                self.assertTrue(floating.styleMask() & A.NSWindowStyleMaskNonactivatingPanel)
                self.assertEqual(floating.contentRectForFrameRect_(floating.frame()).size.height, floating.frame().size.height)

                instance.reminder.show([{'kind': 'software', 'name': '边缘测试', 'detail': '临时测试'}])
                pump()
                reminder = next(w for w in own.windows() if str(w.title()) == instance.reminder.title())
                for index, window in enumerate((floating, reminder)):
                    file = str(Path(folder) / f'overlay-{index}.png')
                    capture = subprocess.run(['/usr/sbin/screencapture', '-x', '-o', '-l', str(window.windowNumber()), file], capture_output=True, text=True)
                    self.assertEqual(capture.returncode, 0, capture.stderr)
                    bitmap = A.NSBitmapImageRep.imageRepWithContentsOfFile_(file)
                    self.assertIsNotNone(bitmap)
                    def rgba(x, y):
                        return bitmap.colorAtX_y_(x, y).colorUsingColorSpace_(A.NSColorSpace.genericRGBColorSpace()).getRed_green_blue_alpha_(None, None, None, None)
                    self.assertLess(rgba(0, 0)[3], 0.05, 'corner must be transparent')
                    # The old Canvas produced near-opaque black pixels on these edges.
                    for x, y in [(1, bitmap.pixelsHigh() // 2), (bitmap.pixelsWide() // 2, 0)]:
                        r, g, b, alpha = rgba(x, y)
                        self.assertTrue(alpha < 0.05 or min(r, g, b) > 0.7, (index, x, y, (r,g,b,alpha)))
                instance.reminder.close()
                pump(0.8)
                external.activateWithOptions_(A.NSApplicationActivateIgnoringOtherApps)
                pump(0.4)
                self.assertFalse(own.isActive(), 'test needs another application in front')
                before = (instance.float.winfo_x(), instance.float.winfo_y())
                x = instance.float.title_label.winfo_rootx() + 30
                y = instance.float.title_label.winfo_rooty() + 15
                def gesture():
                    for kind, point in [(Q.kCGEventLeftMouseDown, (x,y)), (Q.kCGEventLeftMouseDragged, (x+25,y+15)), (Q.kCGEventLeftMouseUp, (x+25,y+15))]:
                        event = Q.CGEventCreateMouseEvent(None, kind, point, Q.kCGMouseButtonLeft)
                        Q.CGEventSetIntegerValueField(event, Q.kCGMouseEventClickState, 1)
                        Q.CGEventPost(Q.kCGHIDEventTap, event)
                        time.sleep(0.15)
                sender = threading.Thread(target=gesture, daemon=True)
                sender.start()
                pump(0.8)
                sender.join(timeout=2)
                self.assertFalse(own.isActive(), 'dragging the overlay activated the application')
                self.assertNotEqual(before, (instance.float.winfo_x(), instance.float.winfo_y()), 'must exercise an actual drag')
                self.assertEqual(instance.store.settings['float_pos'], [instance.float.winfo_x(), instance.float.winfo_y()])
                checkbox = instance.float.scroller.inner.winfo_children()[0].winfo_children()[0]
                cx = checkbox.winfo_rootx() + checkbox.winfo_width() // 2
                cy = checkbox.winfo_rooty() + checkbox.winfo_height() // 2
                def toggle():
                    for kind in (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp):
                        event = Q.CGEventCreateMouseEvent(None, kind, (cx, cy), Q.kCGMouseButtonLeft)
                        Q.CGEventSetIntegerValueField(event, Q.kCGMouseEventClickState, 1)
                        Q.CGEventPost(Q.kCGHIDEventTap, event)
                        time.sleep(0.15)
                toggler = threading.Thread(target=toggle, daemon=True)
                toggler.start()
                pump(0.8)
                toggler.join(timeout=2)
                self.assertTrue(instance.store.tasks[0]['done'])
                self.assertFalse(own.isActive(), 'toggling a floating task activated the application')

                # The explicit main-window button must still work from an inactive panel.
                instance.hide_main()
                button = instance.float._drag_excluded[1]
                bx = button.winfo_rootx() + button.winfo_width() // 2
                by = button.winfo_rooty() + button.winfo_height() // 2
                def click():
                    for kind in (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp):
                        event = Q.CGEventCreateMouseEvent(None, kind, (bx, by), Q.kCGMouseButtonLeft)
                        Q.CGEventSetIntegerValueField(event, Q.kCGMouseEventClickState, 1)
                        Q.CGEventPost(Q.kCGHIDEventTap, event)
                        time.sleep(0.15)
                clicker = threading.Thread(target=click, daemon=True)
                clicker.start()
                pump(0.8)
                clicker.join(timeout=2)
                self.assertEqual(instance.root.state(), 'normal')
            finally:
                instance.quit()
                instance.worker.join(timeout=2)
                external.activateWithOptions_(A.NSApplicationActivateIgnoringOtherApps)
