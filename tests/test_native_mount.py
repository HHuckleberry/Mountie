import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mountie import native_mount


class SandboxDetectionTests(unittest.TestCase):
    def test_detects_flatpak_marker(self):
        marker = mock.Mock(spec=Path)
        marker.exists.return_value = True
        self.assertTrue(native_mount.is_sandboxed(marker))

    def test_absent_marker_means_native_install(self):
        marker = mock.Mock(spec=Path)
        marker.exists.return_value = False
        self.assertFalse(native_mount.is_sandboxed(marker))


class MountpointTests(unittest.TestCase):
    def test_mountpoint_is_derived_from_uid_and_share_id(self):
        self.assertEqual(
            native_mount.mountpoint_for("abc123", uid=1000),
            Path("/run/user/1000/mountie/abc123"),
        )

    def test_defaults_to_current_uid(self):
        with mock.patch("os.getuid", return_value=4242):
            self.assertEqual(
                native_mount.mountpoint_for("abc123"),
                Path("/run/user/4242/mountie/abc123"),
            )


class IsMountedTests(unittest.TestCase):
    def _share(self):
        return {"id": "abc123"}

    def test_true_only_when_mounted_and_cifs(self):
        self.assertTrue(native_mount.is_mounted(
            self._share(), ismount_fn=lambda _p: True, cifs_check_fn=lambda _p: True,
        ))

    def test_false_when_not_a_mountpoint(self):
        self.assertFalse(native_mount.is_mounted(
            self._share(), ismount_fn=lambda _p: False, cifs_check_fn=lambda _p: True,
        ))

    def test_false_when_mounted_but_not_cifs(self):
        # Something unrelated occupying the same deterministic path.
        self.assertFalse(native_mount.is_mounted(
            self._share(), ismount_fn=lambda _p: True, cifs_check_fn=lambda _p: False,
        ))

    def test_local_path_returns_none_when_not_mounted(self):
        self.assertIsNone(native_mount.local_path(
            self._share(), ismount_fn=lambda _p: False, cifs_check_fn=lambda _p: True,
        ))

    def test_local_path_returns_mountpoint_when_mounted(self):
        path = native_mount.local_path(
            self._share(), ismount_fn=lambda _p: True, cifs_check_fn=lambda _p: True,
        )
        self.assertEqual(path, str(native_mount.mountpoint_for("abc123")))


class CifsMountinfoCheckTests(unittest.TestCase):
    def test_matches_cifs_fstype_at_exact_path(self):
        mountinfo = (
            "36 35 0:32 / /run/user/1000/mountie/abc123 rw,relatime "
            "shared:1 - cifs //host/share rw,vers=3.1.1\n"
        )
        with mock.patch("builtins.open", mock.mock_open(read_data=mountinfo)):
            self.assertTrue(native_mount._is_cifs_mount(
                Path("/run/user/1000/mountie/abc123")
            ))

    def test_no_match_for_different_path(self):
        mountinfo = (
            "36 35 0:32 / /run/user/1000/mountie/other rw,relatime "
            "shared:1 - cifs //host/share rw,vers=3.1.1\n"
        )
        with mock.patch("builtins.open", mock.mock_open(read_data=mountinfo)):
            self.assertFalse(native_mount._is_cifs_mount(
                Path("/run/user/1000/mountie/abc123")
            ))

    def test_non_cifs_fstype_at_matching_path_is_rejected(self):
        mountinfo = (
            "36 35 0:32 / /run/user/1000/mountie/abc123 rw,relatime "
            "shared:1 - tmpfs tmpfs rw\n"
        )
        with mock.patch("builtins.open", mock.mock_open(read_data=mountinfo)):
            self.assertFalse(native_mount._is_cifs_mount(
                Path("/run/user/1000/mountie/abc123")
            ))

    def test_missing_mountinfo_fails_open_not_closed(self):
        with mock.patch("builtins.open", side_effect=OSError):
            self.assertTrue(native_mount._is_cifs_mount(
                Path("/run/user/1000/mountie/abc123")
            ))


def completed(returncode, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["mountie-mount-helper"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class ClassifyNativeSetupErrorTests(unittest.TestCase):
    def test_sandboxed_mentions_flatpak_spawn(self):
        status, title = native_mount.classify_native_setup_error("flatpak-spawn", True)
        self.assertEqual(status, "native mount unavailable")
        self.assertIn("flatpak-spawn", title)

    def test_native_install_mentions_pkexec(self):
        status, title = native_mount.classify_native_setup_error("pkexec", False)
        self.assertEqual(status, "native mount unavailable")
        self.assertIn("pkexec", title)


class ClassifyNativeMountErrorTests(unittest.TestCase):
    def test_authentication_cancelled_is_distinct_from_authentication_failed(self):
        status, _title = native_mount.classify_native_mount_error(
            completed(1, stderr="Error executing command as another user: Not authorized")
        )
        self.assertEqual(status, "authentication cancelled")

    def test_bad_credentials_is_authentication_failed(self):
        status, _title = native_mount.classify_native_mount_error(
            completed(1, stderr="mount error(13): Permission denied")
        )
        self.assertEqual(status, "authentication failed")

    def test_missing_policy_action_is_not_set_up(self):
        status, _title = native_mount.classify_native_mount_error(
            completed(1, stderr="Unknown action io.github.HHuckleberry.Mountie.mount-helper")
        )
        self.assertEqual(status, "native mount not set up")

    def test_share_not_found(self):
        status, _title = native_mount.classify_native_mount_error(
            completed(1, stderr="mount error(2): No such file or directory")
        )
        self.assertEqual(status, "share not found")

    def test_host_unreachable(self):
        status, _title = native_mount.classify_native_mount_error(
            completed(1, stderr="mount error(112): Host is down")
        )
        self.assertEqual(status, "host unreachable")

    def test_unrecognized_output_falls_back_to_generic_error(self):
        status, _title = native_mount.classify_native_mount_error(completed(1, stderr="???"))
        self.assertEqual(status, "error")


class RunWrapperTests(unittest.TestCase):
    def test_missing_pkexec_reports_setup_error_without_running(self):
        run_fn = mock.Mock()
        ok, status, _msg = native_mount._run_wrapper(
            ["mount"], run_fn=run_fn, sandboxed_fn=lambda: False, which_fn=lambda _n: None,
        )
        self.assertFalse(ok)
        self.assertEqual(status, "native mount unavailable")
        run_fn.assert_not_called()

    def test_sandboxed_call_uses_flatpak_spawn_host_prefix(self):
        run_fn = mock.Mock(return_value=completed(0))
        native_mount._run_wrapper(
            ["mount", "a", "b"], run_fn=run_fn, sandboxed_fn=lambda: True, which_fn=lambda _n: "/x",
        )
        argv = run_fn.call_args.args[0]
        self.assertEqual(argv[:3], ["flatpak-spawn", "--host", "pkexec"])

    def test_unsandboxed_call_uses_pkexec_directly(self):
        run_fn = mock.Mock(return_value=completed(0))
        native_mount._run_wrapper(
            ["mount", "a", "b"], run_fn=run_fn, sandboxed_fn=lambda: False, which_fn=lambda _n: "/x",
        )
        argv = run_fn.call_args.args[0]
        self.assertEqual(argv[0], "pkexec")
        self.assertEqual(argv[1], native_mount.WRAPPER_PATH)

    def test_success_returns_no_status_or_message(self):
        run_fn = mock.Mock(return_value=completed(0))
        ok, status, message = native_mount._run_wrapper(
            ["mount"], run_fn=run_fn, sandboxed_fn=lambda: False, which_fn=lambda _n: "/x",
        )
        self.assertTrue(ok)
        self.assertIsNone(status)
        self.assertIsNone(message)

    def test_timeout_is_reported_without_crashing(self):
        run_fn = mock.Mock(side_effect=subprocess.TimeoutExpired(cmd="pkexec", timeout=30))
        ok, status, message = native_mount._run_wrapper(
            ["mount"], run_fn=run_fn, sandboxed_fn=lambda: False, which_fn=lambda _n: "/x",
        )
        self.assertFalse(ok)
        self.assertEqual(status, "error")
        self.assertIn("timed out", message)

    def test_missing_wrapper_binary_is_reported(self):
        run_fn = mock.Mock(side_effect=FileNotFoundError("no such file"))
        ok, status, _message = native_mount._run_wrapper(
            ["mount"], run_fn=run_fn, sandboxed_fn=lambda: False, which_fn=lambda _n: "/x",
        )
        self.assertFalse(ok)
        self.assertEqual(status, "native mount unavailable")


class CredentialsFileTests(unittest.TestCase):
    def test_writes_expected_lines_with_restrictive_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = native_mount._write_credentials_file(
                Path(directory), "alice", "secret", "WORKGROUP"
            )
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            contents = Path(path).read_text()
            self.assertIn("username=alice\n", contents)
            self.assertIn("domain=WORKGROUP\n", contents)
            self.assertIn("password=secret\n", contents)

    def test_omits_domain_line_when_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            path = native_mount._write_credentials_file(Path(directory), "alice", "secret", "")
            self.assertNotIn("domain=", Path(path).read_text())

    def test_rejects_control_characters_that_could_inject_fields(self):
        for field, values in (
            ("username", ("alice\npassword=changed", "secret", "")),
            ("password", ("alice", "secret\ndomain=evil", "")),
            ("domain", ("alice", "secret", "WORK\rGROUP")),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, field):
                    native_mount._write_credentials_file(Path(directory), *values)
                self.assertEqual(list(Path(directory).iterdir()), [])


class MountShareTests(unittest.TestCase):
    def _wait(self, received, timeout=2):
        deadline = time.monotonic() + timeout
        while not received and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_successful_mount_creates_and_cleans_up_credentials_file(self):
        seen_creds_paths = []

        def run_fn(argv, **_kwargs):
            creds_path = argv[-1]
            seen_creds_paths.append(creds_path)
            self.assertTrue(Path(creds_path).exists())
            return completed(0)

        received = []
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                native_mount, "base_runtime_dir", return_value=Path(directory)
            ), mock.patch.object(
                native_mount, "mountpoint_for", return_value=Path(directory) / "share-1"
            ):
                native_mount.mount_share(
                    {"id": "share-1", "host": "nas.local", "share": "data", "username": "alice"},
                    "secret",
                    lambda *args: received.append(args),
                    run_fn=run_fn, sandboxed_fn=lambda: False, which_fn=lambda _n: "/x",
                )
            self._wait(received)
            self.assertEqual(received, [(True, None, None)])
            self.assertFalse(Path(seen_creds_paths[0]).exists())

    def test_failed_mount_still_cleans_up_credentials_file(self):
        seen_creds_paths = []

        def run_fn(argv, **_kwargs):
            seen_creds_paths.append(argv[-1])
            return completed(1, stderr="mount error(13): Permission denied")

        received = []
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                native_mount, "base_runtime_dir", return_value=Path(directory)
            ), mock.patch.object(
                native_mount, "mountpoint_for", return_value=Path(directory) / "share-1"
            ):
                native_mount.mount_share(
                    {"id": "share-1", "host": "nas.local", "share": "data", "username": "alice"},
                    "secret",
                    lambda *args: received.append(args),
                    run_fn=run_fn, sandboxed_fn=lambda: False, which_fn=lambda _n: "/x",
                )
            self._wait(received)
            self.assertEqual(len(received), 1)
            self.assertFalse(received[0][0])
            self.assertEqual(received[0][1], "authentication failed")
            self.assertFalse(Path(seen_creds_paths[0]).exists())

    def test_source_is_built_from_host_and_share(self):
        captured = []

        def run_fn(argv, **_kwargs):
            captured.append(argv)
            return completed(0)

        received = []
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                native_mount, "base_runtime_dir", return_value=Path(directory)
            ), mock.patch.object(
                native_mount, "mountpoint_for", return_value=Path(directory) / "share-1"
            ):
                native_mount.mount_share(
                    {"id": "share-1", "host": "nas.local", "share": "/data/", "username": ""},
                    "",
                    lambda *args: received.append(args),
                    run_fn=run_fn, sandboxed_fn=lambda: False, which_fn=lambda _n: "/x",
                )
            self._wait(received)
        argv = captured[0]
        self.assertIn("//nas.local/data", argv)


class UnmountShareTests(unittest.TestCase):
    def test_unmounts_by_derived_mountpoint(self):
        captured = []

        def run_fn(argv, **_kwargs):
            captured.append(argv)
            return completed(0)

        received = []
        native_mount.unmount_share(
            {"id": "share-1"},
            lambda *args: received.append(args),
            run_fn=run_fn, sandboxed_fn=lambda: False, which_fn=lambda _n: "/x",
        )
        deadline = time.monotonic() + 2
        while not received and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(received, [(True, None, None)])
        self.assertEqual(captured[0][2], "unmount")
        self.assertEqual(captured[0][3], str(native_mount.mountpoint_for("share-1")))


class IsHelperInstalledTests(unittest.TestCase):
    def test_native_install_checks_file_access_directly(self):
        access_fn = mock.Mock(return_value=True)
        self.assertTrue(native_mount.is_helper_installed(
            sandboxed_fn=lambda: False, access_fn=access_fn,
        ))
        access_fn.assert_called_once_with(native_mount.WRAPPER_PATH, os.X_OK)

    def test_native_install_reports_missing_wrapper(self):
        self.assertFalse(native_mount.is_helper_installed(
            sandboxed_fn=lambda: False, access_fn=lambda *_: False,
        ))

    def test_sandboxed_asks_the_host_via_flatpak_spawn(self):
        run_fn = mock.Mock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0,
        ))
        self.assertTrue(native_mount.is_helper_installed(
            sandboxed_fn=lambda: True, run_fn=run_fn,
        ))
        argv = run_fn.call_args.args[0]
        self.assertEqual(argv, ["flatpak-spawn", "--host", "test", "-x", native_mount.WRAPPER_PATH])

    def test_sandboxed_missing_wrapper_reports_false(self):
        run_fn = mock.Mock(return_value=subprocess.CompletedProcess(args=[], returncode=1))
        self.assertFalse(native_mount.is_helper_installed(sandboxed_fn=lambda: True, run_fn=run_fn))

    def test_sandboxed_missing_flatpak_spawn_reports_false_not_crash(self):
        run_fn = mock.Mock(side_effect=FileNotFoundError())
        self.assertFalse(native_mount.is_helper_installed(sandboxed_fn=lambda: True, run_fn=run_fn))

    def test_sandboxed_timeout_reports_false_not_crash(self):
        run_fn = mock.Mock(side_effect=subprocess.TimeoutExpired(cmd="flatpak-spawn", timeout=5))
        self.assertFalse(native_mount.is_helper_installed(sandboxed_fn=lambda: True, run_fn=run_fn))


class HostDataDirTests(unittest.TestCase):
    def test_native_install_matches_xdg_data_dir(self):
        home = Path("/home/alice")
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(Path, "home", return_value=home):
                self.assertEqual(
                    native_mount.host_data_dir(sandboxed_fn=lambda: False, home=home),
                    native_mount._xdg_data_dir(),
                )

    def test_sandboxed_uses_the_var_app_convention(self):
        home = Path("/home/alice")
        self.assertEqual(
            native_mount.host_data_dir(sandboxed_fn=lambda: True, home=home),
            home / ".var" / "app" / "io.github.HHuckleberry.Mountie" / "data",
        )


class BundledInstallerSourceTests(unittest.TestCase):
    def test_prefers_the_flatpak_bundled_copy_when_present(self):
        bundled = mock.Mock(spec=Path)
        bundled.exists.return_value = True
        self.assertEqual(
            native_mount.bundled_installer_source(bundled_path=bundled), bundled
        )

    def test_falls_back_to_the_repo_scripts_directory(self):
        bundled = mock.Mock(spec=Path)
        bundled.exists.return_value = False
        source = native_mount.bundled_installer_source(bundled_path=bundled)
        self.assertEqual(source.name, "install-native-mount-helper.sh")
        self.assertTrue(source.exists())


class ExportInstallerForHostTests(unittest.TestCase):
    def test_copies_the_installer_and_makes_it_executable(self):
        with tempfile.TemporaryDirectory() as source_dir, \
             tempfile.TemporaryDirectory() as data_dir:
            source = Path(source_dir) / "install-native-mount-helper.sh"
            source.write_text("#!/usr/bin/env bash\necho hi\n")

            write_path, host_path = native_mount.export_installer_for_host(
                sandboxed_fn=lambda: False, source=source, xdg_data_dir=Path(data_dir),
            )

            self.assertTrue(write_path.exists())
            self.assertEqual(write_path.read_text(), source.read_text())
            self.assertEqual(os.stat(write_path).st_mode & 0o777, 0o755)
            self.assertEqual(write_path, host_path)

    def test_reports_a_different_host_path_when_sandboxed(self):
        with tempfile.TemporaryDirectory() as source_dir, \
             tempfile.TemporaryDirectory() as data_dir:
            source = Path(source_dir) / "install-native-mount-helper.sh"
            source.write_text("#!/usr/bin/env bash\necho hi\n")

            write_path, host_path = native_mount.export_installer_for_host(
                sandboxed_fn=lambda: True, source=source, xdg_data_dir=Path(data_dir),
            )

            self.assertNotEqual(write_path, host_path)
            self.assertIn(".var/app/io.github.HHuckleberry.Mountie/data", str(host_path))
            self.assertTrue(write_path.exists())


if __name__ == "__main__":
    unittest.main()
