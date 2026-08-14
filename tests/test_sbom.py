import importlib.util
import json
import unittest
from pathlib import Path

from mountie.sbom import format_sbom, load_sbom

REPO_ROOT = Path(__file__).resolve().parent.parent
SBOM_PATH = REPO_ROOT / "mountie" / "data" / "sbom.json"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_sbom", REPO_ROOT / "scripts" / "generate_sbom.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SbomContentTests(unittest.TestCase):
    """The checked-in SBOM must actually describe the current build."""

    @classmethod
    def setUpClass(cls):
        cls.checked_in = json.loads(SBOM_PATH.read_text(encoding="utf-8"))
        cls.regenerated = _load_generator().build_sbom()

    def _without_timestamp(self, sbom):
        sbom = json.loads(json.dumps(sbom))
        sbom["metadata"].pop("timestamp", None)
        return sbom

    def test_checked_in_sbom_matches_manifest_and_pyproject(self):
        self.assertEqual(
            self._without_timestamp(self.checked_in),
            self._without_timestamp(self.regenerated),
            "mountie/data/sbom.json is stale - re-run "
            "scripts/generate_sbom.py and commit the result.",
        )

    def test_components_cover_the_native_dependency_stack(self):
        names = {component["name"] for component in self.checked_in["components"]}
        for expected in ("libsecret", "gvfs-client", "python3-pygobject"):
            self.assertIn(expected, names)

    def test_mountie_is_not_listed_as_its_own_dependency(self):
        names = [component["name"] for component in self.checked_in["components"]]
        self.assertNotIn("mountie", names)


class SbomLoaderTests(unittest.TestCase):
    def test_load_sbom_returns_the_bundled_document(self):
        sbom = load_sbom()
        self.assertIsNotNone(sbom)
        self.assertEqual(sbom["bomFormat"], "CycloneDX")

    def test_format_sbom_lists_every_component(self):
        sbom = load_sbom()
        text = format_sbom(sbom)
        for component in sbom["components"]:
            self.assertIn(component["name"], text)


if __name__ == "__main__":
    unittest.main()
