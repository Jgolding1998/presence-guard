import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from xml.etree import ElementTree

from presence_guard import actions, desktop


class DesktopTests(unittest.TestCase):
    def test_demo_calls_no_os_and_writes_no_document(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = desktop.DesktopActions(directory, demo=True, allow_lock=True, allow_sheet=True)
            with mock.patch.object(actions.os, "startfile", create=True) as opened:
                for action in ("boss_alert", "lock_absence", "lock_camera_fault", "lock_crowd"):
                    self.assertTrue(runner.run(action).startswith("Simulated"))
                opened.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_only_fixed_action_names_allowed(self):
        for name in ("cmd.exe", "file:///etc/passwd", "https://example.com", "lock_" + "x" * 100):
            with self.assertRaises(ValueError):
                actions.Actions().run(name)

    def test_windows_lock_api_only_when_enabled(self):
        lock = mock.Mock(return_value=True)
        api = SimpleNamespace(user32=SimpleNamespace(LockWorkStation=lock))
        with mock.patch.object(actions.sys, "platform", "win32"), mock.patch.object(actions.ctypes, "windll", api, create=True):
            with self.assertRaises(PermissionError):
                actions.Actions(demo=False).run("lock_absence")
            lock.assert_not_called()
            self.assertEqual(actions.Actions(demo=False, allow_lock=True).run("lock_absence"), "Requested Windows lock")
            lock.assert_called_once_with()
            lock.return_value = False
            with self.assertRaises(OSError):
                actions.Actions(demo=False, allow_lock=True).run("lock_absence")

    def test_synthetic_workbook_has_no_formulas_macros_or_external_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path = actions.create_boss_sheet(Path(directory) / "Boss Alert.xlsx")
            with zipfile.ZipFile(path) as workbook:
                self.assertEqual(len(workbook.namelist()), 5)
                for name in workbook.namelist():
                    body = workbook.read(name)
                    tree = ElementTree.fromstring(body)
                    self.assertFalse(any(element.tag.split("}")[-1] in ("f", "externalLink", "oleObject") for element in tree.iter()))
                    self.assertNotIn(b'TargetMode="External"', body)
                    self.assertNotIn("vba", name.lower())
                self.assertIn(b"DEMONSTRATION DATA", workbook.read("xl/worksheets/sheet1.xml"))

    def test_workbook_uses_fresh_session_and_fixed_name(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = desktop.DesktopActions(directory, demo=False, allow_sheet=True)
            with mock.patch.object(actions.sys, "platform", "win32"), mock.patch.object(actions.os, "startfile", create=True) as opened:
                runner.run("boss_alert")
                opened.assert_called_once()
                path = opened.call_args.args[0]
                self.assertEqual(path.name, "Boss Alert.xlsx")
                self.assertEqual(path.parent.parent.parent, Path(directory) / "runtime")
                self.assertTrue(path.parent.parent.name.startswith("session-"))
                runner.run("boss_alert")
                second = opened.call_args.args[0]
                self.assertEqual(second.parent.parent, path.parent.parent)
                self.assertNotEqual(second.parent, path.parent)
                self.assertTrue(path.is_file())

    def test_planted_alert_folder_rejected_without_changing_other_file(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = desktop.DesktopActions(directory, demo=False, allow_sheet=True)
            root = Path(directory)
            runner.runtime.mkdir()
            runner.session_dir = runner.runtime / "session-test"
            (runner.session_dir / "alert-1").mkdir(parents=True)
            protected = root / "synthetic-protected.txt"
            protected.write_text("synthetic sentinel", encoding="utf-8")
            os.link(protected, runner.session_dir / "alert-1" / "Boss Alert.xlsx")
            with mock.patch.object(actions.sys, "platform", "win32"), mock.patch.object(actions.os, "startfile", create=True) as opened:
                with self.assertRaises(OSError):
                    runner.run("boss_alert")
                opened.assert_not_called()
            self.assertEqual(protected.read_text("utf-8"), "synthetic sentinel")

    def test_reparse_point_is_rejected(self):
        fake = SimpleNamespace(st_mode=0o100600, st_file_attributes=0x400)
        with mock.patch.object(Path, "lstat", return_value=fake), self.assertRaises(OSError):
            desktop.plain_path(Path.cwd() / "synthetic-link")

    def test_missing_launch_permission_does_not_write_runtime(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(actions.sys, "platform", "win32"):
            runner = desktop.DesktopActions(directory, demo=False)
            with self.assertRaises(PermissionError):
                runner.run("boss_alert")
            self.assertEqual(list(Path(directory).iterdir()), [])
