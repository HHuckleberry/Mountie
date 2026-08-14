#!/usr/bin/env python3
"""Phase 2 of cutting a release: point the Flatpak manifest's mountie module
at the tag just pushed, and regenerate the SBOM to match.

Run this after scripts/bump_version.py, committing its result, tagging, and
pushing the tag - not before, since the commit being pinned has to actually
exist and be reachable.

    python3 scripts/pin_flatpak_release.py v0.3.0

Edits io.github.HHuckleberry.Mountie.yml with a targeted text substitution
rather than a YAML parse/dump round-trip, because the manifest's extensive
explanatory comments (why each pin exists, what broke without it) would not
survive being re-serialized by a YAML library.
"""

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "io.github.HHuckleberry.Mountie.yml"

# Captures the mountie module's own block so the tag/commit substitution
# can't accidentally touch a different module.
MODULE_BLOCK_RE = re.compile(
    r"(- name: mountie\b.*?\n)(.*?)(\n  - name: |\Z)", re.DOTALL
)
TAG_COMMIT_RE = re.compile(r"(tag: )\S+(\n\s+commit: )[0-9a-f]{40}")


def resolve_commit(tag):
    try:
        result = subprocess.run(
            ["git", "rev-parse", f"{tag}^{{commit}}"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as error:
        raise SystemExit(
            f"error: could not resolve {tag} to a commit - has it been "
            f"created with 'git tag {tag}'?\n{error.stderr.strip()}"
        )
    return result.stdout.strip()


def pin_manifest(tag, commit):
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    match = MODULE_BLOCK_RE.search(text)
    if not match:
        raise SystemExit(f"error: could not find the mountie module in {MANIFEST_PATH}")
    block = match.group(2)
    updated_block, count = TAG_COMMIT_RE.subn(rf"\g<1>{tag}\g<2>{commit}", block)
    if count != 1:
        raise SystemExit(
            "error: expected exactly one tag/commit pair in the mountie "
            "module's sources - manifest structure may have changed."
        )
    updated_text = text[:match.start(2)] + updated_block + text[match.end(2):]
    MANIFEST_PATH.write_text(updated_text, encoding="utf-8")


def regenerate_sbom():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "generate_sbom", REPO_ROOT / "scripts" / "generate_sbom.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()


def main():
    if len(sys.argv) != 2 or not sys.argv[1].startswith("v"):
        print(__doc__)
        return 2
    tag = sys.argv[1]

    commit = resolve_commit(tag)
    pin_manifest(tag, commit)
    print(f"Pinned the mountie module to {tag} ({commit})")

    regenerate_sbom()

    print(
        f"\nNext: review the diff, commit as \"Pin Flatpak source for {tag}\", "
        "and rebuild the .flatpak bundle for the release."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
