import importlib.machinery
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name, relative_path):
    path = REPO_ROOT / relative_path
    # The helper script has no .py suffix (it's invoked directly by pkexec),
    # so spec_from_file_location can't infer a loader from the extension.
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = _load("mountie_mount_helper", "data/native-mount-helper/mountie-mount-helper")
generate_installer = _load(
    "generate_native_mount_installer", "scripts/generate_native_mount_installer.py"
)

_REAL_REALPATH = os.path.realpath


def _fake_realpath_rooted_at(base, uid):
    """A realpath stand-in that maps /run/user/<uid>/mountie to `base` and
    otherwise falls back to the real os.path.realpath."""
    marker = f"/run/user/{uid}/mountie"
    def realpath(p):
        return str(base) if p == marker else _REAL_REALPATH(p)
    return realpath


class CallerUidTests(unittest.TestCase):
    def test_reads_pkexec_uid(self):
        with mock.patch.dict(os.environ, {"PKEXEC_UID": "1000"}, clear=False):
            self.assertEqual(helper.caller_uid(), 1000)

    def test_missing_pkexec_uid_fails(self):
        env = dict(os.environ)
        env.pop("PKEXEC_UID", None)
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit):
                helper.caller_uid()

    def test_non_integer_pkexec_uid_fails(self):
        with mock.patch.dict(os.environ, {"PKEXEC_UID": "not-a-number"}, clear=False):
            with self.assertRaises(SystemExit):
                helper.caller_uid()


class RequireUncSourceTests(unittest.TestCase):
    def test_accepts_plain_host_and_share(self):
        self.assertEqual(
            helper.require_unc_source("//nas.local/data"), "//nas.local/data"
        )

    def test_accepts_ipv4_host_and_nested_share(self):
        self.assertEqual(
            helper.require_unc_source("//192.168.1.5/Team Docs/2026"),
            "//192.168.1.5/Team Docs/2026",
        )

    def test_rejects_missing_leading_slashes(self):
        with self.assertRaises(SystemExit):
            helper.require_unc_source("nas.local/data")

    def test_rejects_shell_metacharacters_in_share(self):
        with self.assertRaises(SystemExit):
            helper.require_unc_source("//host/share; rm -rf /")

    def test_rejects_backtick_command_substitution(self):
        with self.assertRaises(SystemExit):
            helper.require_unc_source("//host/`whoami`")

    def test_rejects_oversized_source(self):
        with self.assertRaises(SystemExit):
            helper.require_unc_source("//host/" + "a" * 5000)


class RequireSafeMountpointTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name) / "mountie"
        self.base.mkdir()
        self.uid = os.getuid()

    def _base_for_uid(self, uid):
        # require_safe_mountpoint/require_safe_credentials_file always
        # resolve /run/user/<uid>/mountie, so point that at our tempdir.
        return mock.patch(
            "os.path.realpath", side_effect=_fake_realpath_rooted_at(self.base, uid),
        )

    def test_accepts_owned_directory_directly_under_base(self):
        target = self.base / "share-1"
        target.mkdir()
        with self._base_for_uid(self.uid):
            fd, resolved = helper.require_safe_mountpoint(str(target), self.uid)
            self.addCleanup(os.close, fd)
            self.assertEqual(resolved, str(target))

    def test_rejects_missing_directory(self):
        with self._base_for_uid(self.uid):
            with self.assertRaises(SystemExit):
                helper.require_safe_mountpoint(str(self.base / "missing"), self.uid)

    def test_rejects_nested_path_traversal(self):
        escape_target = Path(self.tempdir.name) / "outside"
        escape_target.mkdir()
        traversal = self.base / ".." / "outside"
        with self._base_for_uid(self.uid):
            with self.assertRaises(SystemExit):
                helper.require_safe_mountpoint(str(traversal), self.uid)

    def test_rejects_symlink_escape(self):
        escape_target = Path(self.tempdir.name) / "outside"
        escape_target.mkdir()
        link = self.base / "escape"
        link.symlink_to(escape_target)
        with self._base_for_uid(self.uid):
            with self.assertRaises(SystemExit):
                helper.require_safe_mountpoint(str(link), self.uid)

    def test_rejects_wrong_owner(self):
        target = self.base / "share-1"
        target.mkdir()
        with self._base_for_uid(self.uid):
            with mock.patch("os.fstat") as stat_fn:
                stat_fn.return_value = mock.Mock(
                    st_mode=0o40700, st_uid=self.uid + 12345,
                )
                with mock.patch("stat.S_ISDIR", return_value=True):
                    with self.assertRaises(SystemExit):
                        helper.require_safe_mountpoint(str(target), self.uid)

    def test_rejects_non_directory(self):
        target = self.base / "not-a-dir"
        target.write_text("x")
        with self._base_for_uid(self.uid):
            with self.assertRaises(SystemExit):
                helper.require_safe_mountpoint(str(target), self.uid)


class RequireSafeCredentialsFileTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name) / "mountie"
        self.base.mkdir()
        self.uid = os.getuid()

    def _base_for_uid(self, uid):
        return mock.patch(
            "os.path.realpath", side_effect=_fake_realpath_rooted_at(self.base, uid),
        )

    def test_accepts_regular_file_with_mode_0600(self):
        target = self.base / "creds-1"
        target.write_text("password=x\n")
        os.chmod(target, 0o600)
        with self._base_for_uid(self.uid):
            fd, resolved = helper.require_safe_credentials_file(str(target), self.uid)
            self.addCleanup(os.close, fd)
            self.assertEqual(resolved, str(target))

    def test_rejects_wrong_mode(self):
        target = self.base / "creds-1"
        target.write_text("password=x\n")
        os.chmod(target, 0o644)
        with self._base_for_uid(self.uid):
            with self.assertRaises(SystemExit):
                helper.require_safe_credentials_file(str(target), self.uid)

    def test_rejects_directory(self):
        target = self.base / "creds-dir"
        target.mkdir()
        with self._base_for_uid(self.uid):
            with self.assertRaises(SystemExit):
                helper.require_safe_credentials_file(str(target), self.uid)

    def test_rejects_path_outside_base(self):
        outside = Path(self.tempdir.name) / "creds-outside"
        outside.write_text("password=x\n")
        os.chmod(outside, 0o600)
        with self._base_for_uid(self.uid):
            with self.assertRaises(SystemExit):
                helper.require_safe_credentials_file(str(outside), self.uid)

    def test_rejects_file_replaced_with_symlink_after_path_validation(self):
        target = self.base / "creds-1"
        target.write_text("password=original\n")
        os.chmod(target, 0o600)
        outside = Path(self.tempdir.name) / "outside"
        outside.write_text("password=replaced\n")
        os.chmod(outside, 0o600)

        def validate_then_replace(path, _uid, _label):
            resolved = str(target)
            target.unlink()
            target.symlink_to(outside)
            return resolved

        with mock.patch.object(
            helper, "_require_child_of_base", side_effect=validate_then_replace
        ):
            with self.assertRaises(SystemExit):
                helper.require_safe_credentials_file(str(target), self.uid)

    def test_rejects_runtime_directory_replaced_with_symlink_after_validation(self):
        target = self.base / "creds-1"
        target.write_text("password=original\n")
        os.chmod(target, 0o600)
        moved_base = Path(self.tempdir.name) / "moved-mountie"
        outside_base = Path(self.tempdir.name) / "outside-base"
        outside_base.mkdir()

        def validate_then_replace(path, _uid, _label):
            resolved = str(target)
            self.base.rename(moved_base)
            self.base.symlink_to(outside_base)
            return resolved

        with mock.patch.object(
            helper, "_require_child_of_base", side_effect=validate_then_replace
        ):
            with self.assertRaises(SystemExit):
                helper.require_safe_credentials_file(str(target), self.uid)


class ReadSafeCredentialsTests(unittest.TestCase):
    def _read(self, payload):
        with tempfile.TemporaryFile() as handle:
            handle.write(payload)
            handle.flush()
            return helper.read_safe_credentials(handle.fileno())

    def test_accepts_and_canonicalizes_expected_fields(self):
        payload = self._read(b"password=secret\ndomain=WORK\nusername=alice\n")
        self.assertEqual(
            payload, b"username=alice\ndomain=WORK\npassword=secret\n"
        )

    def test_rejects_injected_mount_option_field(self):
        with self.assertRaises(SystemExit):
            self._read(b"username=alice\npassword=secret\nuid=0\n")

    def test_rejects_duplicate_password_field(self):
        with self.assertRaises(SystemExit):
            self._read(b"username=alice\npassword=one\npassword=two\n")

    def test_rejects_invalid_encoding_without_echoing_the_secret(self):
        with self.assertRaises(SystemExit):
            self._read(b"username=alice\npassword=\xff\n")


class DoMountUnmountNeverExecWithoutValidationTests(unittest.TestCase):
    """A validation bug must never reach the privileged subprocess call."""

    def test_do_mount_with_bad_source_never_execs(self):
        with mock.patch.object(helper.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                helper.do_mount(["not-unc", "/tmp/x", "/tmp/y"], os.getuid())
            run.assert_not_called()

    def test_do_mount_wrong_argument_count_never_execs(self):
        with mock.patch.object(helper.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                helper.do_mount(["//host/share"], os.getuid())
            run.assert_not_called()

    def test_do_unmount_wrong_argument_count_never_execs(self):
        with mock.patch.object(helper.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                helper.do_unmount([], os.getuid())
            run.assert_not_called()

    def test_main_rejects_unknown_action_without_exec(self):
        with mock.patch.object(helper.subprocess, "run") as run:
            with mock.patch.dict(os.environ, {"PKEXEC_UID": str(os.getuid())}, clear=False):
                with self.assertRaises(SystemExit):
                    helper.main(["mountie-mount-helper", "delete-everything"])
            run.assert_not_called()

    def test_unmount_rejects_a_non_cifs_directory(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            helper, "require_safe_mountpoint"
        ) as validate, mock.patch.object(helper.subprocess, "run") as run:
            fd = os.open(directory, os.O_RDONLY)
            validate.return_value = (fd, directory)
            with self.assertRaises(SystemExit):
                helper.do_unmount([directory], os.getuid())
            run.assert_not_called()


class GeneratedInstallerTests(unittest.TestCase):
    def test_checked_in_script_matches_the_source_files(self):
        expected = generate_installer.build_script()
        actual = generate_installer.OUTPUT_PATH.read_text(encoding="utf-8")
        self.assertEqual(
            actual, expected,
            "scripts/install-native-mount-helper.sh is stale - re-run "
            "scripts/generate_native_mount_installer.py and commit the result.",
        )

    def test_embeds_the_wrapper_source_verbatim(self):
        script = generate_installer.build_script()
        wrapper_source = generate_installer.WRAPPER_SRC.read_text(encoding="utf-8")
        self.assertIn(wrapper_source, script)

    def test_embeds_the_policy_source_verbatim(self):
        script = generate_installer.build_script()
        policy_source = generate_installer.POLICY_SRC.read_text(encoding="utf-8")
        self.assertIn(policy_source, script)

    def test_output_is_valid_bash_syntax(self):
        import subprocess
        result = subprocess.run(
            ["bash", "-n", str(generate_installer.OUTPUT_PATH)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_uninstall_removes_only_the_two_fixed_installed_paths(self):
        script = generate_installer.build_script()
        self.assertIn('rm -f -- "$WRAPPER_DEST" "$POLICY_DEST"', script)
        self.assertIn('case "${1:-install}" in', script)
        self.assertIn("--uninstall)", script)

    def test_wrapper_heredoc_uses_a_quoted_delimiter(self):
        # An unquoted heredoc delimiter would let the shell expand $variables
        # inside the embedded Python source - it must stay quoted.
        script = generate_installer.build_script()
        self.assertIn("<<'MOUNTIE_WRAPPER_EOF'", script)
        self.assertIn("<<'MOUNTIE_POLICY_EOF'", script)


if __name__ == "__main__":
    unittest.main()
