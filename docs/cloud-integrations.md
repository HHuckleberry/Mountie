# Cloud share integration design

Status: researched roadmap item; no cloud integration is implemented yet.

## Decision

Cloud accounts should not be added to `PROTOCOLS`. Google Drive, OneDrive,
Dropbox, S3, and similar services are accounts/remotes with OAuth or API
credentials, provider-specific capabilities, and a process lifecycle. They are
not URI schemes like SMB or WebDAV.

Add a provider-neutral backend boundary first. Keep the current Gio/GVfs
implementation as one backend, then prototype an optional rclone engine.
Nextcloud and ownCloud should continue to use WebDAV initially, with friendly
presets for their well-known paths.

## What existing projects do

- [rclone](https://github.com/rclone/rclone) supplies the provider layer. Its
  [usage documentation](https://github.com/rclone/rclone/blob/master/docs/content/docs.md)
  describes named remotes, many cloud providers, browsing commands, and
  filesystem mounting.
- rclone separates remote configuration from active mounts. Its remote-control
  API has distinct
  [`config/listremotes` and provider/configuration calls](https://github.com/rclone/rclone/blob/master/fs/config/rc.go)
  and
  [`mount/mount`, `mount/unmount`, and `mount/listmounts` calls](https://github.com/rclone/rclone/blob/master/cmd/mountlib/rc.go).
- [Rclone UI](https://github.com/rclone-ui/rclone-ui) explicitly describes
  itself as a thin layer over rclone and separates its rclone adapter, host
  state, and UI. [RcloneBrowser](https://github.com/kapitainsky/RcloneBrowser)
  similarly separates remote browsing, mount controls, jobs, and its item
  model. Mountie should copy those boundaries, not either application's UI.
- GVfs already implements separate
  [Google Drive and OneDrive backends](https://github.com/GNOME/gvfs/tree/master/daemon).
  Mountie can surface accounts already exposed by the desktop when those
  backends and an account provider are available, but cannot assume they exist
  on every COSMIC installation.
- Nextcloud Team folders are available through a documented
  [WebDAV endpoint](https://github.com/nextcloud/groupfolders/blob/master/README.md),
  so a provider-specific engine is unnecessary for the first Nextcloud
  experience.

## Proposed Mountie architecture

Introduce this boundary before adding provider UI:

```text
mountie/
  backends/
    base.py        Backend capabilities and result models
    gvfs.py        Existing URI mounts and network discovery
    rclone.py      Optional rclone process/API adapter
  app/components/
    cloud.py       Account, browser, and cloud mount UI
```

The exact move of existing modules can happen incrementally. The important
boundary is that UI code asks a backend to list, browse, connect, disconnect,
and report status; it must not build rclone commands or provider URLs itself.

Keep two persisted concepts separate:

1. A **remote account** identifies a backend, provider, display name, and an
   opaque backend reference such as an rclone remote name.
2. A **saved mount** identifies a remote account, a selected remote path, a
   Mountie label, connection policy, and non-secret mount options.

OAuth tokens, refresh tokens, client secrets, and raw rclone configuration do
not belong in Mountie's JSON configuration. For the first rclone version,
rclone should own its configuration and authorization lifecycle. Mountie should
import the names and safe metadata of already-configured remotes, not parse or
copy their secret fields.

Engine packaging must respect the installation format:

- A native Mountie package can use a compatible system rclone and its normal
  configuration directory.
- The Flatpak should bundle a reviewed, pinned rclone build and keep its rclone
  configuration in Mountie's private app data. It should not escape the
  sandbox merely to invoke a host executable. A user may explicitly import an
  existing config into that private directory, but silent host-config access
  and runtime executable downloads are out of scope.

The rclone configuration must be treated as a secret even though Mountie does
not own its schema. Only rclone reads or writes it; Mountie consumes narrowly
scoped command or API results.

## Recommended delivery phases

1. Add a backend interface and fake backend tests while leaving current GVfs
   behavior unchanged.
2. Add an engine adapter, check its version/capabilities, and list existing
   remote names read-only. Use a system rclone for native packages and a pinned
   bundled build plus app-private configuration for Flatpak.
3. Add cancellable, asynchronous browsing of a selected remote and folder.
   Only after a successful browse can the user save a cloud mount.
4. Prototype both Linux mount paths below and choose using Flatpak tests.
5. Add create/edit account flows through rclone's non-interactive
   configuration API, including browser OAuth, only after the security model
   and cancellation behavior are tested.
6. Polish a small initial provider set: Google Drive, OneDrive, Dropbox, and
   S3-compatible storage. Add Nextcloud/ownCloud WebDAV presets separately.

## Flatpak mount spike

Two approaches need a real prototype:

### Direct rclone/FUSE mount

This provides normal filesystem semantics and uses rclone's supported mount
API. The cost is a larger sandbox boundary. rclone's own
[installation documentation](https://github.com/rclone/rclone/blob/master/docs/content/install.md)
shows that containerized FUSE mounts need special device and mount handling.
The Flathub package for rclone-manager uses a
[host-side fusermount wrapper](https://github.com/flathub/io.github.zarestia_dev.rclone-manager/blob/master/fusermount-wrapper.sh)
for this reason.

### Loopback WebDAV bridge

Run `rclone serve webdav` for one selected remote on an authenticated loopback
address, then mount it through Mountie's existing GVfs WebDAV path. This may
avoid direct FUSE access, but adds a local server, port/authentication,
readiness, crash recovery, and filesystem-semantics concerns. Treat it as an
experiment, not the default, until browse, edit, large-file, suspend, and crash
tests pass.

The spike must compare permissions, reliability, performance, cleanup, and
whether the resulting mount is visible to host applications. No new broad
Flatpak permission should be accepted merely to make the prototype work.

## Security and reliability gates

- Invoke rclone with an argument vector, never through a shell. Validate remote
  names, paths, mount points, and every supported option.
- Never place credentials or tokens in command arguments, Mountie logs, error
  dialogs, exported configurations, or analytics.
- Do not call rclone configuration dump/get endpoints in normal operation;
  request only the minimum safe metadata needed by the UI.
- If a persistent remote-control process is used, bind it only to loopback,
  require per-session authentication, restrict its lifetime, and never use a
  no-auth mode.
- Track every child process and mount explicitly. On startup, reconcile stale
  state; on disconnect or shutdown, terminate only processes owned by Mountie.
- Give the rclone engine a private, writable configuration directory because
  rclone refreshes OAuth tokens and may replace the configuration file
  atomically. Do not grant Mountie access to unrelated user files.
- Add fake-rclone tests for malformed JSON, timeouts, cancellation, process
  failure, token redaction, hostile names/paths, stale mounts, and version
  incompatibility. Real-provider tests remain opt-in and must use disposable
  test accounts.

## First implementation milestone

The first shippable cloud milestone is deliberately small: display existing
rclone remotes, browse one remote, select a folder, and save it as a disabled
Mountie entry. Mounting remains behind an experimental switch until the
Flatpak spike picks and validates a mount mechanism.
