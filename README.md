# Mountie

A small GUI for mounting and managing network shares (SMB/CIFS, AFP, NFS,
SFTP, WebDAV) via GVfs.

It runs on any desktop with GVfs and a secret service, and follows the
desktop's own light/dark preference and accent color through the XDG
settings portal — so it looks at home on [COSMIC][cosmic], GNOME or KDE
alike.

- **Add / edit / remove shares** for any host and protocol GVfs supports.
- **One-click toggle** to mount/unmount each share (no `sudo`/`pkexec` needed
  — mounts land in the per-user GVfs mount namespace via `gio`).
- **Security-first credential policies**: new installations ask every time by
  default. You can instead remember passwords until logout or save them in the
  desktop's system keyring. Passwords are never written to Mountie's config or
  logs.
- **Reusable credential profiles** for people who use one account across
  several shares, while still allowing every share to use a different account
  or no account at all.
- **Connection controls for larger setups**: search, Connect All, Disconnect
  All, live GVfs status updates, and import of eligible mounts created outside
  Mountie.
- **Automatic disconnect policies** per share: after a fixed connected time,
  when the screen locks, or when the system begins suspending.
- **System / Light / Dark themes**, selectable from the appearance button
  and remembered between runs. The default, System, follows the desktop's
  light/dark preference live via the XDG settings portal — the same source
  COSMIC itself drives — and picks up your COSMIC accent color. Because it
  reads the portal rather than the Qt palette, it stays correct without
  `qt5ct`/`qt6ct` and inside a Flatpak sandbox.

[cosmic]: https://github.com/pop-os/cosmic-epoch

## Requirements

- Python 3.9+
- PyQt5
- PyGObject, with the `Gio` and `Secret` (libsecret) typelibs available
- A running secret service (GNOME Keyring, KWallet via a Secret Service
  bridge, etc.)
- GVfs, with backends installed for whichever protocols you use (e.g.
  `gvfs-backends` for SMB/AFP/NFS on Debian/Ubuntu-based distros)

## Install

```sh
./install.sh
```

This installs into a virtualenv at `~/.local/share/mountie/venv` and
registers a launcher entry under `~/.local/share/applications/` pointed at
it, so **Mountie** shows up in your app launcher right away.

A venv is used because most distributions now mark the system Python as
externally managed (PEP 668), which makes `pip install --user` fail. It is
created with `--system-site-packages` so it reuses your distribution's PyQt5
and PyGObject rather than building PyGObject from source.

To install without a launcher entry, or to package it yourself:

```sh
pip install .
```

This installs a `mountie` console script. To add a launcher entry
manually:

```sh
cp data/io.github.HHuckleberry.Mountie.desktop ~/.local/share/applications/
```

(Adjust `Exec=` in that file if `mountie` isn't on your `PATH`, e.g.
when installed into a venv.)

## Where your shares end up

GVfs mounts land at `/run/user/<uid>/gvfs/smb-share:server=…,share=…`, which
is precise and unusable. So each mounted share also gets a symlink in one
predictable directory — `~/Shares/<label>` by default:

```
~/Shares/home -> /run/user/1000/gvfs/smb-share:server=192.168.2.8,share=home
```

Links appear on mount and are removed on unmount, and stale ones are cleaned
up at startup. Anything that isn't a symlink is left strictly alone, so
pointing `link_dir` at a directory holding real files can't destroy them.

To change the location, set `link_dir` in
`~/.config/mountie/config.json`. Using `/mnt` needs a one-time setup,
because `/mnt` is root-owned and this app never asks for root:

```sh
sudo mkdir -p /mnt/shares && sudo chown "$USER" /mnt/shares
```

Then set `"link_dir": "/mnt/shares"`. Set `"links_enabled": false` to turn
the feature off.

Mountie follows the XDG directory specification. Native installations store
settings at `~/.config/mountie/config.json`; Flatpak keeps them in its
persistent application-data directory. On its first Flatpak launch, Mountie
imports an existing native configuration automatically. It also maintains a
`config.json.backup` file and recovers from it if the primary file is damaged.
The actions menu can also export or import a validated JSON configuration.
Exports include share locations, usernames, domains, and profile names, but
never passwords. Exported identity information can still be sensitive, so the
file is created with user-only permissions.

## Credential security and convenience

The default policy for a fresh installation is **Ask every time**. Existing
installations retain their previous storage behavior during migration so an
update does not silently break configured shares. Change the global default in
About and diagnostics; enabling permanent storage requires an explicit warning
confirmation. A share or reusable credential profile can override the default.

| Policy | Convenience | Security tradeoff |
|---|---|---|
| Ask every time | Password is entered for every connection | Mountie stores no password |
| Remember until logout | Reconnect without prompting during this login | Password lives in the keyring's temporary session collection |
| Save in system keyring | Reconnect across logins | Protection depends on the desktop keyring being configured and locked correctly |

Anonymous and passwordless shares work with every policy. Domains/workgroups
are optional and are stored separately from usernames, so both domain accounts
and standalone server accounts work. The credential profile manager in the
actions menu can rename profiles, replace their passwords, and show which
shares use them. A profile cannot be removed while a share still references it.

## Usage

Run `mountie`, then **Add Share** with a label, host, share/path, and
optional credentials. Flip the toggle to mount or unmount. Status badges
show connected/disconnected/error state; use the refresh button to
re-check all shares against what's actually mounted. Refresh also shows
network connections created by another application as read-only **External**
rows. Use **Import** to adopt an eligible connection as a normal Mountie share.
Mountie can recover the target and sometimes its username, but another
application's password is never exposed, so you may need to enter it again.

Use the connection-actions menu for Connect All, Disconnect All, credential
profiles, and configuration import/export. Bulk operations run one at a time so
credential prompts and errors remain attributable to the correct share.

Auto-disconnect is a **fixed connected-duration timer**, not an idle-activity
timer. Other applications read GVfs mounts directly, so Mountie cannot reliably
observe all file activity. Timers and lock/suspend handling require Mountie to
remain running. Lock and suspend notifications are best effort because desktop
environments expose them differently; the ordinary duration timer remains the
most predictable option.

For SMB shares joined to Active Directory or a Windows workgroup, enter the
domain/workgroup separately from the username. Leave the domain/workgroup
blank for standalone servers, local server accounts, and protocols that do
not use one.

The About and diagnostics button in the header shows the installed version,
repository and issue links, configuration locations, and a viewer for the
rotating application log.

## Testing

Run the complete test suite with:

```sh
python3 -m unittest discover -s tests -v
```

The isolated abuse-case suite uses no real network shares or keyring entries:

```sh
python3 -m unittest tests.test_abuse_cases -v
```

To check one already-configured share against its real server and saved
keyring entry without exposing credentials or directory names:

```sh
python3 scripts/test_configured_share.py "Share label"
```

The check preserves the share's original mount state.

## Flatpak size

Flatpak reports an application payload separately from its shared KDE runtime.
The PyQt BaseApp also includes optional Qt WebEngine components unless its
provided cleanup is enabled. Mountie does not use WebEngine, so the manifest
removes it and the Python build toolchain after building. In the current local
test build this reduced Mountie's installed payload from about 256 MB to 35 MB
and its single-file bundle from 65 MB to about 5.4 MB. The KDE runtime remains a
shared dependency and is downloaded once for all compatible applications.

## TODO

Completed reliability and security work:

- [x] Allow passwordless and anonymous shares to mount without requiring a
  saved keyring password.
- [x] After a failed mount or unmount, query the real mount state and keep the
  toggle, status badge, and symlink synchronized with it.
- [x] When deleting a mounted share, wait for unmounting to finish and handle
  failures before removing its configuration, keyring entry, and symlink.
- [x] Safely handle edits to mounted shares, including unmounting the old
  target and removing or renaming its previous symlink.
- [x] Prevent normalized share labels from producing duplicate symlink names,
  or include a stable unique suffix in each generated name.
- [x] Catch and report configuration, filesystem, and secret-service errors
  instead of terminating the application.
- [x] Validate host and share/path input and construct properly encoded URIs,
  including correct handling for IPv6 addresses and reserved characters.
- [x] Default new installs to no credential storage, with explicit session and
  permanent keyring policies and a warning before permanent storage.
- [x] Support reusable credential profiles without putting passwords in the
  configuration.
- [x] Monitor GVfs mount changes, show external connections, and provide
  sequential Connect All / Disconnect All controls.
- [x] Add fixed-duration, screen-lock, and suspend-triggered disconnect options.
- [x] Export and validate configurations without exporting passwords.
- [x] Remove unused Qt WebEngine and build-time files from the Flatpak payload.

Possible follow-up work:

- [ ] Evaluate desktop-specific idle/activity APIs before offering an
  activity-based disconnect mode; do not label a fixed timer as idle detection.
- [ ] Evaluate an opt-in background/autostart mode through the desktop portal so
  timers can continue without keeping the main window open.
- [ ] Evaluate trusted-network auto-connect only where the desktop can provide a
  reliable network identity without broadening Flatpak permissions excessively.

## Before publishing a fork

`APP_ID` in `mountie/settings.py` is the single source of truth for the
libsecret schema, the desktop entry, and the icon name. If you fork this,
change it to a reverse-DNS ID you control — for a GitHub-hosted fork that's
`io.github.<account>.<repo>`, which must resolve to a reachable repository —
so your build doesn't collide with anyone else's over stored secrets.

Renaming strands any credentials already saved under the old ID. To carry
them over:

```sh
python3 scripts/migrate_keyring.py io.github.old.AppId
```

That copies rather than moves, so the old entries stay put and re-running is
harmless.

## License

GPL-3.0-only. See [LICENSE](LICENSE).
