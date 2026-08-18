# Native (kernel CIFS) mount backend

Status: implemented, opt-in per share, SMB/CIFS only. This is a reference for
the privileged subsystem it depends on — read it before changing anything
under `mountie/native_mount.py`, `data/native-mount-helper/`, or
`scripts/install-native-mount-helper.sh`.

## Why this exists

Mountie's default backend mounts shares through GVfs, which surfaces as a
FUSE mountpoint. That needs no elevated privileges, but every read/write pays
a userspace + FUSE round-trip, which is measurably slower than a kernel-level
`mount -t cifs` for large sequential transfers. The native backend trades
that convenience for throughput, on a per-share, opt-in basis.

## Architecture

```
ShareDialog (backend=native) -> mountie.mounts (dispatch)
                                      -> mountie.native_mount
                                            -> pkexec [flatpak-spawn --host]
                                                 /usr/libexec/mountie-mount-helper
                                                      -> /usr/bin/mount -t cifs
```

- `mountie/native_mount.py` runs inside Mountie (sandboxed or not) and never
  touches privileged state directly. It builds arguments, writes a
  short-lived credentials file, and invokes the wrapper.
- `data/native-mount-helper/mountie-mount-helper` is the only code that runs
  as root. It is installed to `/usr/libexec/mountie-mount-helper` on the
  **host** — outside any Flatpak sandbox — by
  `scripts/install-native-mount-helper.sh`, which the user runs once with
  `sudo`. That script is identical for native and Flatpak installs, because a
  Flatpak sandbox cannot write `/usr/share/polkit-1/actions` or install a
  host binary itself. Settings → General surfaces exactly this command
  (see "In-app discovery and setup" below) rather than expecting a user to
  go find the script themselves.
- `data/native-mount-helper/io.github.HHuckleberry.Mountie.policy` is the
  polkit action that authorizes `pkexec` to run that one wrapper as root.
  Its `org.freedesktop.policykit.exec.path` annotation must exactly match
  the wrapper's installed path, or `pkexec` will not resolve the action.

This is deliberately **not** a persistent D-Bus daemon (the more common
Flatpak-ecosystem pattern, e.g. UDisks2). A one-shot `pkexec`-authorized
script has no standing process, no lifecycle to manage, and no second
release artifact to version against the app — a better fit for a
solo-maintained project distributed via GitHub Releases rather than
Flathub. The tradeoff is accepted explicitly: `pkexec` re-prompts more
often than a cached D-Bus session would, and each mount/unmount pays
subprocess + `pkexec` (+`flatpak-spawn` when sandboxed) overhead. The
polkit action's `allow_active=auth_admin_keep` (not `auth_admin`) keeps
that overhead to one prompt per polkit session rather than one per call.

## Threat model

**Nothing from the sandboxed/unprivileged caller is trusted.** The wrapper
re-derives or independently validates everything before acting:

- The caller's identity comes **only** from `PKEXEC_UID`, which `pkexec`
  itself sets and the caller cannot override via argv.
- The mount source must match a strict `//host/share[/...]` pattern (plain
  hostname or IPv4 only — see "Known limitations" below). This is checked
  even though the wrapper never invokes a shell, as defense in depth against
  a future refactor that might.
- The mountpoint and credentials-file arguments must each resolve
  (`realpath`) to a **direct child** of `/run/user/<PKEXEC_UID>/mountie/`,
  must already exist, and must be owned by `PKEXEC_UID`. The wrapper never
  creates directories itself — only the unprivileged caller does, before
  invoking the wrapper — so a symlink or path-traversal attempt to point
  outside that tree is rejected rather than silently followed.
- The credentials file must additionally be a regular file at mode exactly
  `0600`. Its contents are parsed as UTF-8 and allow only one `username`, one
  `password`, and an optional `domain` field; duplicate or injected option
  fields are rejected without printing credential values.
- Validation returns open file descriptors rather than paths. The helper
  addresses the mountpoint through `/proc/self/fd` and copies credentials
  from the validated descriptor into a root-owned temporary file. Renaming or
  replacing either caller-owned path after validation therefore cannot change
  what the privileged operation uses.
- **fstype is hardcoded to `cifs`** inside the wrapper; it is not an accepted
  argument at all. There is nothing to allowlist because there is only one
  value.
- **Mount options are composed entirely by the wrapper**:
  `credentials=<file>,uid=<uid>,gid=<gid>,file_mode=0600,dir_mode=0700`. The
  caller cannot inject arbitrary `-o` values (no `vers=` override, no
  multichannel, nothing configurable in this version — see below).
- Unmount requests are checked against `/proc/self/fdinfo` and
  `/proc/self/mountinfo`; the validated descriptor must identify a live CIFS
  mount. The helper cannot be used to unmount an unrelated filesystem.
- The wrapper runs `mount`/`umount` with an explicit argument list and passed
  descriptors — never through a shell or string interpolation — then removes
  its root-owned credential copy.
- Any validation failure prints one line to stderr and exits `2` before
  `mount`/`umount` is ever touched.

`tests/test_native_mount_helper.py` enforces this by monkeypatching the final
subprocess call to fail the test if it's ever reached during a
validation-failure case — a validation bug that falls through can't silently
pass.

## The `/run/user/<uid>/mountie` convention

Each native-mount share gets a mountpoint at
`/run/user/<uid>/mountie/<share-id>` — deterministic, already user-owned
tmpfs (the same runtime-dir convention GVfs itself uses), and cleared
automatically at logout. The same directory holds the transient credentials
file written just before each mount attempt and deleted immediately after,
success or failure.

Under Flatpak, this path has to be visible identically to both the sandboxed
Mountie process (which creates the mountpoint directory and writes the
credentials file) and the host-side wrapper (which validates and mounts onto
that same path) — that's what the manifest's `--filesystem=xdg-run/mountie`
finish-arg is for. Without it, the sandboxed process would write into its own
private runtime-dir view, invisible to the host.

`mountie/native_mount.py`'s `is_mounted`/`local_path` don't just check
`os.path.ismount()` — they also parse `/proc/self/mountinfo` to confirm the
live mount at that exact path is actually `fstype=cifs`, closing the (cheap
to close) edge case where something unrelated occupies the same
deterministic path.

## Known limitations (v1)

- **SMB/CIFS only.** NFS is out of scope for this pass; `mounts.validate_share`
  and `settings._read_config` both reject `backend=native` combined with any
  protocol other than `smb`.
- **No advanced mount options.** No `vers=` override, no multichannel, no
  `seal`. Some older NAS devices need an explicit SMB version negotiated —
  currently unsupported. A future version could accept a small, still-wrapper-validated
  allowlist of options if this becomes a real need.
- **Host format is conservative.** The wrapper's `SOURCE_RE` accepts plain
  hostnames and IPv4 addresses only — no IPv6 literals, no port suffix, no
  internationalized hostnames (GVfs's `mounts._uri_host` supports all of
  these; the native path currently does not). A share whose host doesn't fit
  produces a clear "invalid share" error rather than a crash.
- **No live external-change detection.** GVfs mounts refresh automatically
  via `Gio.VolumeMonitor`'s `mount-added`/`mount-removed`/`mount-changed`
  signals. A kernel `mount -t cifs` made or removed outside Mountie doesn't
  fire those signals, so a native-mount share's status only updates on
  startup, the manual refresh button, or right after Mountie's own
  mount/unmount call completes. Not solved here; a `/proc/mounts` poller
  would be the natural follow-up if this proves to matter in practice.
- **`pkexec` UX, not a cached D-Bus session.** See "Architecture" above.

## Release smoke test

Automated tests mock the final privileged system calls. Before publishing a
release that changes this backend, also test on a disposable machine or VM:

1. Install the helper using the script exported by Mountie, not the checkout
   copy, and confirm Settings reports it as installed.
2. Configure a disposable native SMB share and run
   `python3 scripts/test_configured_share.py "Share label"`.
3. Confirm `/proc/self/mountinfo` reports the resulting mount as `cifs`, then
   disconnect it through Mountie.
4. Run the Settings removal command and confirm both the helper and policy are
   gone while an ordinary GVfs share still connects.

Never use personal credentials or a production share for this release check.

## In-app discovery and setup (Settings → General)

Settings shows whether the helper is installed and, if not, a ready-to-copy
shell-quoted `sudo bash -- <path>` command — this is `mountie/native_mount.py`'s
`is_helper_installed()`/`export_installer_for_host()`, wired up in
`mountie/app/settings.py`'s `SettingsDialog._native_mount_card()`.

- **Detecting "installed" from inside a sandbox is not a plain file check.**
  Paths like `/usr/libexec/...` resolve inside the Flatpak *runtime*, not the
  real host, from inside the sandbox — `os.path.exists` would silently check
  the wrong filesystem. `is_helper_installed()` instead runs
  `flatpak-spawn --host test -x /usr/libexec/mountie-mount-helper` when
  sandboxed (using the same `org.freedesktop.Flatpak` grant the mount path
  itself needs), and a direct `os.access` check otherwise. This only tests
  existence/executability — it never runs the wrapper, so it needs no
  `pkexec` prompt just to check.
- **A Flatpak-only user has no access to the git repo**, so
  `scripts/install-native-mount-helper.sh` has to be reachable from inside
  the running app itself. The manifest's `mountie` module installs it to
  `/app/share/mountie/install-native-mount-helper.sh`
  (`BUNDLED_INSTALLER_PATH`); `bundled_installer_source()` reads from there
  when present, falling back to the checkout's own `scripts/` directory for
  a native install.
- **`/app` isn't visible from a host terminal**, so the button doesn't just
  point at the bundled copy — `export_installer_for_host()` copies it into
  Mountie's own XDG data directory first. For a native install that's
  already a real host path; under Flatpak, writing to what this process
  sees as `~/.local/share/mountie/...` actually lands in the real host's
  `~/.var/app/io.github.HHuckleberry.Mountie/data/mountie/...` (Flatpak
  bind-mounts the app's private data dir to what looks like a normal XDG
  path from inside the sandbox). `host_data_dir()` computes that *displayed*
  host-real path independently of the *write* path, since a host terminal
  can't resolve the sandbox-internal one — those are two different strings
  for the same underlying file when sandboxed, and the same string when not.
  When the helper is already installed, the same card offers a removal
  command that invokes the exported installer with `--uninstall`.

## Changing the wrapper or its policy

- **The wrapper and policy files are the single source of truth.** Edit
  `data/native-mount-helper/mountie-mount-helper` and
  `data/native-mount-helper/io.github.HHuckleberry.Mountie.policy` directly,
  then regenerate the installer:
  ```sh
  python3 scripts/generate_native_mount_installer.py
  ```
  `scripts/install-native-mount-helper.sh` is a **generated file** — it
  embeds both source files verbatim via quoted heredocs so it's fully
  self-contained (no sibling files, works however a user obtained it: git
  checkout, GitHub release, or exported from inside the Flatpak). Don't hand
  edit it; `tests/test_native_mount_helper.py`'s `GeneratedInstallerTests`
  fails the suite if it's stale relative to the two source files.
- If the wrapper's installed path ever changes, update it in three places
  that must stay in sync: `mountie/native_mount.py`'s `WRAPPER_PATH`, the
  `WRAPPER_DEST` inside `data/native-mount-helper/mountie-mount-helper`'s
  generated installer template, and the `.policy` file's
  `org.freedesktop.policykit.exec.path` annotation.
- Any change to the wrapper's argument contract (`do_mount`/`do_unmount` in
  `data/native-mount-helper/mountie-mount-helper`) needs matching changes in
  `mountie/native_mount.py`'s `mount_share`/`unmount_share` (which build the
  argv) and in `tests/test_native_mount_helper.py`.
- After changing the wrapper or policy file, users need to re-run the
  installer to pick it up — it always overwrites, so re-running is always
  safe. Settings' status card reflects this automatically next time it's
  opened.
- To remove the privileged integration, unmount native shares and run the
  installer with `--uninstall`. It removes only the fixed helper and policy
  paths; the ordinary GVfs backend is unaffected.
