import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from presence_guard import server
from scripts import install_camera, verify_shared

ROOT = Path(__file__).absolute().parent.parent


class ProjectTests(unittest.TestCase):
    def test_shared_sources_match_recorded_hashes_and_optional_checkout(self):
        self.assertEqual(verify_shared.verify(), [])
        with tempfile.TemporaryDirectory() as directory:
            upstream = Path(directory)
            (upstream / "jarvis").mkdir()
            for vendored, source in verify_shared.PAIRS.items():
                shutil.copyfile(ROOT / vendored, upstream / source)
            self.assertEqual(verify_shared.verify(upstream=upstream), [])
            (upstream / "jarvis/presence.py").write_text("changed source", encoding="utf-8")
            self.assertEqual(verify_shared.verify(upstream=upstream), ["presence_guard/presence.py"])

    def test_manifest_has_only_relative_safe_paths_and_two_hashes(self):
        manifest = json.loads((ROOT / "shared-source.json").read_text("utf-8"))
        self.assertEqual(manifest["schema"], 1)
        self.assertEqual(len(manifest["files"]), 2)
        for entry in manifest["files"]:
            for key in ("source_path", "vendored_path"):
                self.assertFalse(Path(entry[key]).is_absolute())
                self.assertNotIn("..", Path(entry[key]).parts)
            self.assertEqual(entry["sha256"], hashlib.sha256((ROOT / entry["vendored_path"]).read_bytes()).hexdigest())

    def test_pinned_camera_package_matches_installer(self):
        self.assertEqual((ROOT / "requirements-camera.txt").read_text("utf-8").strip(), install_camera.PACKAGE)

    def test_shared_bytes_are_exempt_from_checkout_line_ending_conversion(self):
        attributes = (ROOT / ".gitattributes").read_text("utf-8")
        for name in verify_shared.PAIRS:
            self.assertIn(name + " -text", attributes.splitlines())

    def test_optional_installer_uses_venv_official_wheels_and_no_secret_env(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / ".venv"
            target.mkdir()
            (target / "pyvenv.cfg").write_text("home = synthetic", encoding="utf-8")
            python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python.parent.mkdir()
            python.write_bytes(b"synthetic executable fixture")
            run = mock.Mock()
            sentinel_name = "SYNTHETIC_" + "API_" + "KEY"
            sentinel_value = "DO_NOT_" + "PASS"
            with mock.patch.dict(os.environ, {sentinel_name: sentinel_value, "PIP_INDEX_URL": "https://private.invalid"}):
                install_camera.install(root, run=run)
            self.assertEqual(run.call_count, 2)
            command = run.call_args_list[0].args[0]
            self.assertEqual(command[0], str(python))
            self.assertIn("--isolated", command)
            self.assertIn("--require-virtualenv", command)
            self.assertIn("--only-binary=:all:", command)
            self.assertIn("https://pypi.org/simple", command)
            self.assertIn(install_camera.PACKAGE, command)
            self.assertNotIn(sentinel_name, run.call_args.kwargs["env"])
            self.assertNotIn("PIP_INDEX_URL", run.call_args.kwargs["env"])
            self.assertNotIn("shell", run.call_args.kwargs)
            self.assertEqual(run.call_args.kwargs["timeout"], 300)

    def test_installer_decline_does_not_spawn(self):
        with mock.patch("builtins.input", return_value="no"), mock.patch.object(install_camera, "install") as install, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(install_camera.main(), 0)
            install.assert_not_called()

    def test_foreign_venv_directory_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / ".venv").mkdir()
            run = mock.Mock()
            with self.assertRaises(ValueError):
                install_camera.install(Path(directory), run=run)
            run.assert_not_called()

    def test_parser_defaults_inactive_demo_no_desktop_permissions(self):
        args = server.build_parser().parse_args([])
        self.assertFalse(args.camera)
        self.assertFalse(args.allow_lock)
        self.assertFalse(args.allow_spreadsheet)
        self.assertFalse(args.open)

    def test_desktop_permissions_cannot_be_smuggled_into_demo(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for flag in ("--allow-lock", "--allow-spreadsheet"):
                with self.assertRaises(SystemExit):
                    server.main([flag])
            with self.assertRaises(SystemExit):
                server.main(["--camera", "--demo"])

    def test_asset_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "web").mkdir()
            target = root / "synthetic-private.txt"
            target.write_text("synthetic sentinel", encoding="utf-8")
            try:
                (root / "web/index.html").symlink_to(target)
            except OSError:
                self.skipTest("Local symlink creation is unavailable")
            with self.assertRaises(OSError):
                server.load_assets(root)

    def test_web_has_no_storage_network_devices_or_eval(self):
        source = (ROOT / "web/app.js").read_text("utf-8")
        for forbidden in ("localStorage", "sessionStorage", "indexedDB", "getUserMedia", "eval(", "innerHTML", "new Function", "WebSocket"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("https://", source)
        self.assertNotIn("http://", source)
