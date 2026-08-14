import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bump_version = _load("bump_version", "scripts/bump_version.py")
pin_flatpak_release = _load("pin_flatpak_release", "scripts/pin_flatpak_release.py")


class BumpVersionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)

        init_path = root / "__init__.py"
        init_path.write_text('"""Doc."""\n\n__version__ = "0.2.1"\n', encoding="utf-8")
        metainfo_path = root / "metainfo.xml"
        metainfo_path.write_text(
            "<component>\n  <releases>\n"
            '    <release version="0.2.1" date="2026-08-12">\n'
            "      <description><p>Old.</p></description>\n"
            "    </release>\n  </releases>\n</component>\n",
            encoding="utf-8",
        )

        self.init_path = init_path
        self.metainfo_path = metainfo_path
        patches = (
            mock.patch.object(bump_version, "INIT_PATH", init_path),
            mock.patch.object(bump_version, "METAINFO_PATH", metainfo_path),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_writes_new_version_leaving_the_rest_of_the_file_intact(self):
        bump_version._write_init_version("0.3.0")
        text = self.init_path.read_text(encoding="utf-8")
        self.assertIn('__version__ = "0.3.0"', text)
        self.assertIn('"""Doc."""', text)

    def test_inserts_release_entry_right_after_releases_tag_as_the_newest(self):
        entry = bump_version._release_entry_xml(
            "0.3.0", "New stuff.", ["Added a thing", "Fixed <a bug> & other"]
        )
        bump_version._insert_release_entry(entry)
        text = self.metainfo_path.read_text(encoding="utf-8")
        self.assertLess(text.index('version="0.3.0"'), text.index('version="0.2.1"'))
        self.assertIn("New stuff.", text)
        self.assertIn("Added a thing", text)
        # XML-unsafe characters in a --change must come out escaped.
        self.assertIn("Fixed &lt;a bug&gt; &amp; other", text)
        self.assertIn('<release version="0.2.1"', text)

    def test_rejects_non_semver_version(self):
        with mock.patch.object(bump_version.sys, "argv", ["bump_version.py", "not-a-version"]):
            with self.assertRaises(SystemExit):
                bump_version.main()

    def test_rejects_a_version_that_is_not_newer(self):
        with mock.patch.object(bump_version.sys, "argv", ["bump_version.py", "0.2.1"]), \
             mock.patch.object(bump_version, "_regenerate_sbom"):
            with self.assertRaises(SystemExit):
                bump_version.main()

    def test_main_bumps_version_and_regenerates_the_sbom(self):
        with mock.patch.object(
            bump_version.sys, "argv",
            ["bump_version.py", "0.3.0", "--summary", "Summary.", "--change", "A change"],
        ), mock.patch.object(bump_version, "_regenerate_sbom") as regen:
            bump_version.main()
        self.assertIn('__version__ = "0.3.0"', self.init_path.read_text(encoding="utf-8"))
        regen.assert_called_once()


SAMPLE_MANIFEST = """\
modules:
  - name: setuptools
    buildsystem: simple
    sources:
      - type: file
        url: https://example.com/setuptools.whl
        sha256: deadbeef

  # A comment explaining the pin, which must survive editing.
  - name: mountie
    buildsystem: simple
    build-commands:
      - pip3 install .
    sources:
      # Pinned to a tag AND its commit - see the note above.
      - type: git
        url: https://github.com/HHuckleberry/Mountie.git
        tag: v0.2.0
        commit: e43e3414a332743ee5f7a6a9437f0eacf4bc69e1
"""


class PinFlatpakReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.manifest_path = Path(self.tempdir.name) / "manifest.yml"
        self.manifest_path.write_text(SAMPLE_MANIFEST, encoding="utf-8")
        patcher = mock.patch.object(pin_flatpak_release, "MANIFEST_PATH", self.manifest_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pins_only_the_mountie_modules_tag_and_commit(self):
        new_commit = "1" * 40
        pin_flatpak_release.pin_manifest("v0.3.0", new_commit)
        text = self.manifest_path.read_text(encoding="utf-8")
        self.assertIn(f"tag: v0.3.0\n        commit: {new_commit}", text)
        self.assertNotIn("v0.2.0", text)
        # Comments and the unrelated setuptools module must be untouched.
        self.assertIn("A comment explaining the pin, which must survive editing.", text)
        self.assertIn("sha256: deadbeef", text)

    def test_resolve_commit_reads_a_real_tag_from_git(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            run = lambda *args: subprocess.run(
                args, cwd=repo_dir, check=True, capture_output=True, text=True
            )
            run("git", "init", "-q")
            run("git", "config", "user.email", "test@example.com")
            run("git", "config", "user.name", "Test")
            (Path(repo_dir) / "f.txt").write_text("x", encoding="utf-8")
            run("git", "add", "f.txt")
            run("git", "commit", "-q", "-m", "init")
            run("git", "tag", "v9.9.9")
            expected = run("git", "rev-parse", "HEAD").stdout.strip()

            with mock.patch.object(pin_flatpak_release, "REPO_ROOT", Path(repo_dir)):
                resolved = pin_flatpak_release.resolve_commit("v9.9.9")
        self.assertEqual(resolved, expected)

    def test_resolve_commit_raises_for_a_missing_tag(self):
        with mock.patch.object(pin_flatpak_release, "REPO_ROOT", REPO_ROOT):
            with self.assertRaises(SystemExit):
                pin_flatpak_release.resolve_commit("v0.0.0-does-not-exist")


if __name__ == "__main__":
    unittest.main()
