#!/usr/bin/env python3
"""Regenerates mountie/data/sbom.json, the software bill of materials shown
in Settings -> Diagnostics.

This is a maintainer tool, not something Mountie runs itself. It reads the
two places dependencies are actually pinned - the Flatpak manifest (native
stack: runtime, base app, and every built module) and pyproject.toml (Python
package requirements) - and writes a CycloneDX-shaped JSON document
describing what a build actually contains.

The output is a static snapshot, not something computed at runtime, so it
only stays accurate if this is re-run and the result committed whenever a
dependency in the Flatpak manifest or pyproject.toml changes. tests/test_sbom.py
cross-checks the checked-in file against both sources and fails if they've
drifted apart.

    python3 scripts/generate_sbom.py
"""

import json
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "io.github.HHuckleberry.Mountie.yml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
INIT_PATH = REPO_ROOT / "mountie" / "__init__.py"
OUTPUT_PATH = REPO_ROOT / "mountie" / "data" / "sbom.json"

VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')


def _app_version():
    """mountie/__init__.py is pyproject.toml's dynamic version source too -
    read the same way, without importing the package."""
    match = VERSION_RE.search(INIT_PATH.read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"Could not find __version__ in {INIT_PATH}")
    return match.group(1)

# pip install commands pin versions as "name==1.2.3"; this pulls each
# package/version pair out of a module's build-commands.
PIP_PIN_RE = re.compile(r'"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.!+-]+)"')


def _module_component(module):
    """Describe one Flatpak module as a CycloneDX component."""
    name = module["name"]
    sources = module.get("sources", [])
    source = sources[0] if sources else {}

    version = None
    for command in module.get("build-commands", []):
        match = PIP_PIN_RE.search(command)
        if match:
            version = match.group(2)
            break

    external_refs = []
    hashes = []
    if source.get("type") == "archive" or source.get("type") == "file":
        if source.get("url"):
            external_refs.append({"type": "distribution", "url": source["url"]})
        if source.get("sha256"):
            hashes.append({"alg": "SHA-256", "content": source["sha256"]})
        if version is None:
            version_match = re.search(
                r"-([0-9][0-9A-Za-z.]*)\.(?:tar\.[a-z.]+|whl)$", source.get("url", "")
            )
            if version_match:
                version = version_match.group(1)
    elif source.get("type") == "git":
        version = source.get("tag") or source.get("commit", "")[:12]
        if source.get("url"):
            external_refs.append({"type": "vcs", "url": source["url"]})
        if source.get("commit"):
            hashes.append({"alg": "SHA-1", "content": source["commit"]})

    component = {
        "type": "library",
        "name": name,
        "version": version or "unknown",
        "scope": "required",
    }
    if external_refs:
        component["externalReferences"] = external_refs
    if hashes:
        component["hashes"] = hashes
    return component


def _flatpak_components(manifest):
    components = [
        {
            "type": "framework",
            "name": manifest["runtime"],
            "version": manifest["runtime-version"],
            "scope": "required",
            "description": "Flatpak runtime Mountie is sandboxed against.",
        },
        {
            "type": "framework",
            "name": manifest["base"],
            "version": manifest["base-version"],
            "scope": "required",
            "description": "Flatpak base app; supplies PyQt5 and its Qt libraries.",
        },
    ]
    # Mountie itself is already the top-level metadata.component below;
    # listing its own module a second time as a dependency of itself would
    # be confusing, and its manifest tag lags the in-development version in
    # pyproject.toml between releases.
    components.extend(
        _module_component(m) for m in manifest["modules"] if m["name"] != "mountie"
    )
    return components


def _python_components(pyproject):
    project = pyproject["project"]
    components = []
    for requirement in project.get("dependencies", []):
        # No version pin lives in pyproject.toml: PyQt5 comes from the
        # Flatpak base app above, and PyGObject's exact pinned version is
        # the python3-pygobject module above. This entry records the
        # unpinned requirement itself, not a resolved version.
        components.append({
            "type": "library",
            "name": requirement,
            "version": "unpinned (see Flatpak base app / module list)",
            "scope": "required",
        })
    return components


def build_sbom():
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    with PYPROJECT_PATH.open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    mountie_module = next(m for m in manifest["modules"] if m["name"] == "mountie")
    mountie_source = mountie_module["sources"][0]

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tools": [{"name": "scripts/generate_sbom.py"}],
            "component": {
                "type": "application",
                "name": "mountie",
                "version": _app_version(),
                "externalReferences": [
                    {"type": "vcs", "url": mountie_source["url"]},
                ],
                "hashes": [
                    {"alg": "SHA-1", "content": mountie_source["commit"]},
                ],
            },
        },
        "components": _flatpak_components(manifest) + _python_components(pyproject),
    }


def main():
    sbom = build_sbom()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(sbom['components'])} components to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
