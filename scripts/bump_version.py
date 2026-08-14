#!/usr/bin/env python3
"""Phase 1 of cutting a release: bump the version and stage AppStream
release notes. mountie/__init__.py's __version__ is the single source of
truth (pyproject.toml reads it dynamically - see [tool.setuptools.dynamic]
there), so this is the only file a version number itself needs editing in.

Usage:
    python3 scripts/bump_version.py 0.3.0 \\
        --summary "A software bill of materials and update notifications." \\
        --change "Add a Software Bill of Materials viewer to Settings" \\
        --change "Add an opt-out startup check for newer releases"

This does not touch the Flatpak manifest's git tag/commit pin - that commit
doesn't exist yet at this point. After committing this, tagging, and
pushing the tag, run scripts/pin_flatpak_release.py to finish the release.
"""

import argparse
import importlib.util
import re
import sys
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

REPO_ROOT = Path(__file__).resolve().parent.parent
INIT_PATH = REPO_ROOT / "mountie" / "__init__.py"
METAINFO_PATH = REPO_ROOT / "data" / "io.github.HHuckleberry.Mountie.metainfo.xml"


def _regenerate_sbom():
    """Call generate_sbom.py's own build+write logic directly, rather than
    running it as __main__ - its sys.exit(main()) would otherwise raise
    SystemExit straight through this function and skip everything after."""
    spec = importlib.util.spec_from_file_location(
        "generate_sbom", REPO_ROOT / "scripts" / "generate_sbom.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()

VERSION_RE = re.compile(r'(__version__\s*=\s*)"([^"]+)"')
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _current_version():
    match = VERSION_RE.search(INIT_PATH.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"error: could not find __version__ in {INIT_PATH}")
    return match.group(2)


def _version_tuple(text):
    return tuple(int(part) for part in text.split("."))


def _write_init_version(new_version):
    text = INIT_PATH.read_text(encoding="utf-8")
    updated, count = VERSION_RE.subn(rf'\g<1>"{new_version}"', text)
    if count != 1:
        raise SystemExit(f"error: expected exactly one __version__ assignment in {INIT_PATH}")
    INIT_PATH.write_text(updated, encoding="utf-8")


def _release_entry_xml(new_version, summary, changes):
    today = date.today().isoformat()
    lines = [f'    <release version="{new_version}" date="{today}">', "      <description>"]
    if summary:
        lines.append(f"        <p>{escape(summary)}</p>")
    if changes:
        lines.append("        <ul>")
        lines.extend(f"          <li>{escape(change)}</li>" for change in changes)
        lines.append("        </ul>")
    lines.extend(["      </description>", "    </release>"])
    return "\n".join(lines)


def _insert_release_entry(entry_xml):
    text = METAINFO_PATH.read_text(encoding="utf-8")
    marker = "<releases>"
    index = text.index(marker) + len(marker)
    updated = text[:index] + "\n" + entry_xml + text[index:]
    METAINFO_PATH.write_text(updated, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", help="new version, e.g. 0.3.0")
    parser.add_argument("--summary", default="", help="one-line release summary for AppStream")
    parser.add_argument(
        "--change", dest="changes", action="append", default=[],
        help="a changelog bullet point; repeat for multiple",
    )
    args = parser.parse_args()

    if not SEMVER_RE.match(args.version):
        raise SystemExit(f"error: {args.version!r} is not a MAJOR.MINOR.PATCH version")

    current = _current_version()
    if _version_tuple(args.version) <= _version_tuple(current):
        raise SystemExit(
            f"error: {args.version} is not newer than the current version {current}"
        )

    _write_init_version(args.version)
    _insert_release_entry(_release_entry_xml(args.version, args.summary, args.changes))

    print(f"Bumped {current} -> {args.version} in {INIT_PATH}")
    print(f"Staged a release entry in {METAINFO_PATH}")
    if not args.summary and not args.changes:
        print(
            "No --summary/--change given - the staged entry has an empty "
            "<description>. Fill it in before committing."
        )
    print("\nRegenerating the SBOM (it embeds the app version)...")
    _regenerate_sbom()

    print(
        f"\nNext: review the diff, commit as \"Release Mountie {args.version}\", "
        f"tag it \"v{args.version}\", push the tag, then run "
        f"scripts/pin_flatpak_release.py v{args.version}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
