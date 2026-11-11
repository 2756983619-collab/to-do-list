"""Cross-platform regression tests for Mac sampling, permissions and persistence."""
from contextlib import nullcontext
import os
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import app
import monitor
import monitor_macos as mac
import theme


class NativeSamplingTests(unittest.TestCase):
    def sample(self, trusted=True, fail=False, hidden=False):
        application = Mock()
        application.executableURL.return_value.path.return_value = '/Applications/Safari.app/Contents/MacOS/Safari'
        application.processIdentifier.return_value = 777777
        application.isTerminated.return_value = False
        application.isHidden.return_value = hidden
        application.activationPolicy.return_value = 0
        workspace = Mock()
        workspace.runningApplications.return_value = [application]
        workspace.frontmostApplication.return_value = application
        kit = SimpleNamespace(NSWorkspace=Mock(sharedWorkspace=Mock(return_value=workspace)),
                              NSApplicationActivationPolicyRegular=0)
        ax = Mock()
        ax.AXIsProcessTrusted.return_value = trusted
        ax.kAXErrorSuccess = 0
        ax.kAXErrorNoValue = -1
        ax.kAXErrorAttributeUnsupported = -2
        ax.kAXWindowsAttribute = 'windows'
        ax.kAXTitleAttribute = 'title'
        ax.kAXMinimizedAttribute = 'minimized'
        def attribute(element, name, out):
            if fail:
                return -3, None
            return 0, {'windows': ['window'], 'title': 'bilibili - Safari', 'minimized': False}[name]
        ax.AXUIElementCopyAttributeValue.side_effect = attribute
        with patch.object(mac, '_native', return_value=(kit, ax, SimpleNamespace(autorelease_pool=nullcontext))):
            return mac.sample_system_state(), ax

    def test_authorized_window_and_process(self):
        state, ax = self.sample()
        self.assertEqual(state['processes'], {'safari'})
        self.assertEqual(state['windows'][0]['title'], 'bilibili - Safari')
        self.assertTrue(state['accessibility'])
        ax.AXUIElementSetMessagingTimeout.assert_called_once()

    def test_permission_revoked_keeps_process_detection(self):
        state, ax = self.sample(trusted=False)
        self.assertEqual(state['processes'], {'safari'})
        self.assertEqual(state['windows'], [])
        self.assertFalse(state['accessibility'])
        ax.AXUIElementCopyAttributeValue.assert_not_called()

    def test_bad_app_does_not_discard_processes(self):
        state, _ = self.sample(fail=True)
        self.assertEqual(state['processes'], {'safari'})
        self.assertEqual(state['sampling_errors'], 1)

    def test_hidden_browser_does_not_alert(self):
        state, _ = self.sample(hidden=True)
        with patch.object(monitor, 'BROWSER_EXES', mac.BROWSER_EXES):
            self.assertFalse(monitor.detect_target({'kind': 'website', 'match': 'bilibili'}, state)[0])
            self.assertTrue(monitor.detect_target({'kind': 'software', 'match': 'safari'}, state)[0])

    def test_mac_browser_names_and_minimized(self):
        with patch.object(monitor, 'BROWSER_EXES', mac.BROWSER_EXES):
            for exe in mac.BROWSER_EXES:
                window = {'exe': exe, 'title': 'bilibili', 'minimized': False}
                state = {'windows': [window]}
                self.assertTrue(monitor.detect_target({'kind': 'website', 'match': 'bilibili'}, state)[0])
                window['minimized'] = True
                self.assertFalse(monitor.detect_target({'kind': 'website', 'match': 'bilibili'}, state)[0])

    def test_picker_includes_apps_without_exe_suffix(self):
        with patch.object(monitor.sys, 'platform', 'darwin'), patch.object(monitor, 'list_windows', return_value=[]), patch.object(monitor, 'list_processes', return_value={'safari', 'steam'}):
            self.assertEqual({x['exe'] for x in monitor.running_programs()}, {'safari', 'steam'})


class DataLocationTests(unittest.TestCase):
    def test_mac_default_and_unwritable(self):
        with patch.object(app.sys, 'platform', 'darwin'), patch.dict(os.environ, {}, clear=True), patch.object(app, '_writable', return_value=True):
            self.assertEqual(app._data_dir(), os.path.expanduser('~/Library/Application Support/ToDoList'))
        with patch.object(app.sys, 'platform', 'darwin'), patch.dict(os.environ, {}, clear=True), patch.object(app, '_writable', return_value=False):
            with self.assertRaises(OSError):
                app._data_dir()

    def test_override_bypasses_default_location(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'TODO_LIST_DATA': os.path.join(folder, 'custom.json')}), patch.object(app, '_writable') as probe:
            self.assertEqual(app._data_dir(), folder)
            probe.assert_not_called()

    def test_migration_preserves_existing_target(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'data').mkdir()
            (base / 'data/todo.json').write_text('old', encoding='utf8')
            dest = base / 'new/todo.json'
            with patch.object(app, 'BASE_DIR', folder), patch.object(app, 'DATA_FILE', str(dest)):
                app._migrate_legacy_data()
                self.assertEqual(dest.read_text(), 'old')
                dest.write_text('new', encoding='utf8')
                app._migrate_legacy_data()
                self.assertEqual(dest.read_text(), 'new')

    def test_windows_location_retained(self):
        with patch.object(app.sys, 'platform', 'win32'), patch.dict(os.environ, {'APPDATA': '/roaming'}, clear=True), patch.object(app.os.path, 'exists', return_value=False), patch.object(app, '_writable', return_value=True):
            self.assertEqual(app._data_dir(), os.path.join('/roaming', 'ToDoList'))


class PermissionTests(unittest.TestCase):
    def test_prompt_once_and_observe_later_authorization(self):
        instance = app.App.__new__(app.App)
        instance.root = Mock()
        instance._permission_prompted = False
        with patch.object(app.sys, 'platform', 'darwin'), patch.object(monitor, 'accessibility_trusted', side_effect=[False, False, True]), patch.object(monitor, 'request_accessibility') as request, patch.object(app.messagebox, 'showinfo') as message:
            self.assertFalse(instance.ensure_accessibility())
            self.assertFalse(instance.ensure_accessibility())
            self.assertTrue(instance.ensure_accessibility())
            request.assert_called_once()
            message.assert_called_once()


class WorkerTests(unittest.TestCase):
    def test_permission_and_partial_failure_status_recovers(self):
        out = queue.Queue()
        provider = lambda: {'enabled': True, 'targets': []}
        sampler = Mock(side_effect=[{'accessibility': False}, {'accessibility': True, 'sampling_errors': 1}, {'accessibility': True}])
        worker = monitor.MonitorThread(provider, out, sampler=sampler)
        worker.tick()
        self.assertFalse(out.get()[1]['accessibility'])
        worker.tick()
        self.assertEqual(out.get()[1]['sampling_errors'], 1)
        worker.tick()
        self.assertEqual(out.get()[1]['sampling_errors'], 0)
        worker.start()
        worker.stop()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())

    def test_sampling_exception_recovers_next_tick(self):
        out = queue.Queue()
        sampler = Mock(side_effect=[RuntimeError('temporary'), {'windows': []}])
        worker = monitor.MonitorThread(lambda: {'enabled': True}, out, sampler=sampler, poll=0.01)
        worker.start()
        try:
            self.assertEqual(out.get(timeout=1)[0], 'error')
            self.assertEqual(out.get(timeout=1)[0], 'status')
        finally:
            worker.stop()
            worker.join(timeout=1)

    def test_snooze_suppresses_alerts(self):
        out = queue.Queue()
        worker = monitor.MonitorThread(lambda: {'enabled': True, 'snooze_until': 1000,
            'targets': [{'id': 's', 'kind': 'software', 'match': 'safari'}]}, out,
            sampler=lambda: {'processes': {'safari'}})
        with patch.object(monitor.time, 'time', return_value=100):
            worker.tick()
        self.assertTrue(out.get()[1]['snoozed'])
        self.assertTrue(out.empty())

    def test_trackpad_small_deltas(self):
        with patch.object(theme.sys, 'platform', 'darwin'):
            self.assertEqual(theme.wheel_units(1), -1)
            self.assertEqual(theme.wheel_units(-2), 2)
            self.assertEqual(theme.wheel_units(0), 0)
        with patch.object(theme.sys, 'platform', 'win32'):
            self.assertEqual(theme.wheel_units(120), -1)


if __name__ == '__main__':
    unittest.main()
