import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mountie import iso_mount


def completed(returncode=0, stdout="", stderr=""):
    return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)


class IsoMountTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.image = Path(self.tempdir.name) / "data.iso"
        self.image.touch()
        self.config = {"id": "image", "kind": "iso", "label": "Data", "path": str(self.image)}

    def test_mount_attaches_read_only_then_mounts_read_only(self):
        callback = mock.Mock()
        calls = []
        results = iter([
            completed(stdout="Mapped file data.iso as /dev/loop7.\n"),
            completed(stdout="Mounted /dev/loop7 at /run/media/alice/DATA.\n"),
        ])
        with mock.patch.object(iso_mount, "_sandboxed", return_value=False), \
             mock.patch.object(iso_mount.shutil, "which", return_value="/usr/bin/udisksctl"), \
             mock.patch.object(iso_mount, "_state_path", return_value=Path(self.tempdir.name) / "state"), \
             mock.patch.object(iso_mount.threading, "Thread") as thread:
            thread.side_effect = lambda target, daemon: mock.Mock(start=target)
            def run(args, **kwargs):
                calls.append(args)
                return next(results)
            iso_mount.mount_image(self.config, callback, run_fn=run)
        callback.assert_called_once_with(True, None, None)
        self.assertIn("--read-only", calls[0])
        self.assertEqual(calls[1][-2:], ["--options", "ro"])
        state = json.loads((Path(self.tempdir.name) / "state").read_text())
        self.assertEqual(state["device"], "/dev/loop7")
        self.assertEqual(state["mountpoint"], "/run/media/alice/DATA")

    def test_missing_image_fails_before_running_commands(self):
        self.config["path"] = str(Path(self.tempdir.name) / "missing.iso")
        callback = mock.Mock()
        with mock.patch.object(iso_mount.threading, "Thread") as thread:
            thread.side_effect = lambda target, daemon: mock.Mock(start=target)
            iso_mount.mount_image(self.config, callback, run_fn=mock.Mock())
        self.assertEqual(callback.call_args.args[1], "image not found")

    def test_flatpak_commands_run_on_host(self):
        with mock.patch.object(iso_mount, "_sandboxed", return_value=True):
            self.assertEqual(
                iso_mount._command("udisksctl", "mount"),
                ["flatpak-spawn", "--host", "udisksctl", "mount"],
            )

    def test_flatpak_preserves_host_visible_document_portal_path(self):
        portal_path = "/run/user/1000/doc/abc/data.iso"
        with mock.patch.object(iso_mount, "_sandboxed", return_value=True), \
             mock.patch.object(Path, "resolve", return_value=Path("/run/flatpak/doc/abc/data.iso")):
            self.assertEqual(iso_mount._source_key(portal_path), portal_path)

    def test_flatpak_uses_document_origin_as_loop_backing_file(self):
        portal_path = "/run/user/1000/doc/abc/data.iso"
        result = completed(stdout=(
            "id: abc\npath: /run/user/1000/doc/abc/data.iso\n"
            "origin: /home/alice/Downloads/data.iso\n"
        ))
        run = mock.Mock(return_value=result)
        with mock.patch.object(iso_mount, "_sandboxed", return_value=True):
            self.assertEqual(
                iso_mount._mount_source_path(portal_path, run),
                "/home/alice/Downloads/data.iso",
            )
        self.assertEqual(run.call_args.args[0][:3], [
            "flatpak-spawn", "--host", "flatpak",
        ])

    def test_state_filename_cannot_escape_runtime_directory(self):
        with mock.patch.dict("os.environ", {"XDG_RUNTIME_DIR": self.tempdir.name}):
            path = iso_mount._state_path("../../outside")
        self.assertEqual(path.parent, Path(self.tempdir.name) / "mountie")


if __name__ == "__main__":
    unittest.main()
