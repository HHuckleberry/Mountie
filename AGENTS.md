# Mountie — Agent Instructions

## Versioning

Bump the version after completing each feature or meaningful internal
change, even if it isn't being pushed or published yet — this is how the
maintainer tracks internally which build is running, independent of when
it actually ships.

- Bump the **patch** component (`x.y.Z`) for internal/incremental changes.
  Minor/major bumps are the maintainer's call, not automatic.
- Use `scripts/bump_version.py NEW_VERSION --summary "..." --change "..."` —
  it updates `mountie/__init__.py`, stages an AppStream `<release>` entry in
  `data/io.github.HHuckleberry.Mountie.metainfo.xml`, and regenerates
  `mountie/data/sbom.json` in one step.
- Do this as soon as a feature is functionally complete and tested — not
  only at release time. "Publishing" (commit, tag, push, cut a GitHub
  Release) is a separate, later, explicitly-requested step; don't wait for
  that to bump the version, and don't skip the bump just because a change
  hasn't been published yet.
