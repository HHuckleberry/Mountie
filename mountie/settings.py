import json
import logging
import os
import tempfile
from pathlib import Path


APP_ID = "io.github.HHuckleberry.Mountie"

THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEMES = [
    (THEME_SYSTEM, "System"),
    (THEME_LIGHT, "Light"),
    (THEME_DARK, "Dark"),
]

PROTOCOLS = [
    ("smb", "SMB / CIFS (Windows, Samba, Synology, etc.)"),
    ("afp", "AFP (older macOS file sharing)"),
    ("nfs", "NFS"),
    ("sftp", "SFTP / SSH"),
    ("ftp", "FTP"),
    ("ftps", "FTPS"),
    ("dav", "WebDAV"),
    ("davs", "WebDAV (secure)"),
]
DEFAULT_PROTOCOL = "smb"
DEFAULT_LINK_DIR = "~/Shares"
DISCONNECT_OPTIONS = (
    (0, "Never"),
    (30, "After 30 minutes"),
    (60, "After 1 hour"),
    (240, "After 4 hours"),
    (480, "After 8 hours"),
)

# Starting points for the "Add Share" dialog. Each entry's "initial" dict
# is passed straight through as ShareDialog's `initial=` argument, so its
# keys must be a subset of what ShareDialog reads from `source.get(...)`.
# host/share/domain/username are deliberately omitted so those fields stay
# blank for the user to fill in.
SHARE_PRESETS = (
    {
        "key": "media",
        "menu_label": "Media Library",
        "description": "Stays connected for streaming (Plex, Jellyfin, etc.)",
        "initial": {
            "protocol": "smb",
            "label": "Media Library",
            "disconnect_after_minutes": 0,
            "disconnect_on_lock": False,
            "disconnect_on_suspend": False,
        },
    },
    {
        "key": "backup",
        "menu_label": "Backup Target",
        "description": "Disconnects automatically before your laptop sleeps",
        "initial": {
            "protocol": "smb",
            "label": "Backup",
            "disconnect_after_minutes": 0,
            "disconnect_on_lock": False,
            "disconnect_on_suspend": True,
        },
    },
    {
        "key": "nas",
        "menu_label": "NAS Folder",
        "description": "General-purpose network folder",
        "initial": {
            "protocol": "smb",
            "label": "NAS",
            "disconnect_after_minutes": 0,
            "disconnect_on_lock": False,
            "disconnect_on_suspend": False,
        },
    },
)

CREDENTIAL_ASK = "ask"
CREDENTIAL_SESSION = "session"
CREDENTIAL_PERMANENT = "permanent"
CREDENTIAL_USE_GLOBAL = "global"
CREDENTIAL_POLICIES = (
    (CREDENTIAL_ASK, "Ask every time"),
    (CREDENTIAL_SESSION, "Remember until logout"),
    (CREDENTIAL_PERMANENT, "Save in system keyring"),
)
CONFIG_VERSION = 2

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG_DIR = CONFIG_HOME / "mountie"
CONFIG_PATH = CONFIG_DIR / "config.json"
BACKUP_PATH = CONFIG_DIR / "config.json.backup"
LEGACY_CONFIG_PATH = Path.home() / ".config" / "mountie" / "config.json"

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    pass


def default_config():
    return {
        "config_version": CONFIG_VERSION,
        "shares": [],
        "credential_profiles": [],
        "theme": THEME_SYSTEM,
        "link_dir": DEFAULT_LINK_DIR,
        "links_enabled": True,
        # Security-first for new installs. Existing configurations are
        # migrated below without silently changing their behavior.
        "credential_policy": CREDENTIAL_ASK,
        "check_for_updates": True,
    }


def _read_config(path):
    try:
        with path.open() as config_file:
            config = json.load(config_file)
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"Could not read {path}: {error}") from error
    if not isinstance(config, dict):
        raise ConfigError(f"{path} must contain a JSON object.")
    config.setdefault("shares", [])
    config.setdefault("credential_profiles", [])
    config.setdefault("theme", THEME_SYSTEM)
    config.setdefault("link_dir", DEFAULT_LINK_DIR)
    config.setdefault("links_enabled", True)
    config.setdefault("check_for_updates", True)
    legacy_policy = config.pop("never_save_credentials", None)
    if "credential_policy" not in config:
        # v1 saved passwords unless its opt-out was enabled. Preserve that
        # behavior during upgrade; only fresh installs default to Ask.
        config["credential_policy"] = (
            CREDENTIAL_ASK if legacy_policy else CREDENTIAL_PERMANENT
        )
    config["config_version"] = CONFIG_VERSION
    if not isinstance(config["shares"], list):
        raise ConfigError(f"The shares field in {path} must be a list.")
    if not isinstance(config["credential_profiles"], list):
        raise ConfigError(f"The credential_profiles field in {path} must be a list.")
    if config["theme"] not in dict(THEMES):
        raise ConfigError(f"The theme field in {path} is invalid.")
    required_share_fields = ("id", "label", "host", "share")
    profile_ids = set()
    profile_labels = set()
    for index, profile in enumerate(config["credential_profiles"], start=1):
        if not isinstance(profile, dict):
            raise ConfigError(f"Credential profile {index} in {path} is invalid.")
        profile.setdefault("username", "")
        profile.setdefault("domain", "")
        profile.setdefault("credential_policy", CREDENTIAL_USE_GLOBAL)
        fields = ("id", "label", "username", "domain", "credential_policy")
        if any(field not in profile or not isinstance(profile[field], str) for field in fields):
            raise ConfigError(f"Credential profile {index} in {path} is incomplete.")
        if not profile["id"] or profile["id"] in profile_ids:
            raise ConfigError(f"Credential profile {index} in {path} has a duplicate or empty ID.")
        normalized_label = profile["label"].strip().casefold()
        if not normalized_label or normalized_label in profile_labels:
            raise ConfigError(
                f"Credential profile {index} in {path} has a duplicate or empty name."
            )
        if profile["credential_policy"] not in {
            CREDENTIAL_USE_GLOBAL, *(key for key, _label in CREDENTIAL_POLICIES)
        }:
            raise ConfigError(f"Credential profile {index} in {path} has an invalid policy.")
        profile_ids.add(profile["id"])
        profile_labels.add(normalized_label)
    share_ids = set()
    for index, share in enumerate(config["shares"], start=1):
        if not isinstance(share, dict) or any(field not in share for field in required_share_fields):
            raise ConfigError(f"Share {index} in {path} is incomplete or invalid.")
        # Credentials added after the original config format are optional;
        # normalizing them here keeps old saved shares fully compatible.
        share.setdefault("protocol", DEFAULT_PROTOCOL)
        share.setdefault("domain", "")
        share.setdefault("username", "")
        share.setdefault("credential_policy", CREDENTIAL_USE_GLOBAL)
        share.setdefault("credential_profile_id", "")
        share.setdefault("disconnect_after_minutes", 0)
        share.setdefault("disconnect_on_lock", False)
        share.setdefault("disconnect_on_suspend", False)
        string_fields = (
            "id", "protocol", "label", "host", "share", "domain", "username",
            "credential_policy",
            "credential_profile_id",
        )
        if any(not isinstance(share[field], str) for field in string_fields):
            raise ConfigError(f"Share {index} in {path} contains a non-text field.")
        if not share["id"]:
            raise ConfigError(f"Share {index} in {path} has an empty ID.")
        if share["id"] in share_ids:
            raise ConfigError(f"Share {index} in {path} reuses another share ID.")
        share_ids.add(share["id"])
        if share["protocol"] not in dict(PROTOCOLS):
            raise ConfigError(f"Share {index} in {path} uses an unsupported protocol.")
        if share["credential_policy"] not in {
            CREDENTIAL_USE_GLOBAL, *(key for key, _label in CREDENTIAL_POLICIES)
        }:
            raise ConfigError(f"Share {index} in {path} has an invalid credential policy.")
        if share["credential_profile_id"] and share["credential_profile_id"] not in profile_ids:
            raise ConfigError(f"Share {index} in {path} references a missing credential profile.")
        if (
            not isinstance(share["disconnect_after_minutes"], int)
            or isinstance(share["disconnect_after_minutes"], bool)
            or share["disconnect_after_minutes"] < 0
            or share["disconnect_after_minutes"] > 7 * 24 * 60
        ):
            raise ConfigError(f"Share {index} in {path} has an invalid disconnect timer.")
        if not isinstance(share["disconnect_on_lock"], bool):
            raise ConfigError(f"Share {index} in {path} has an invalid lock policy.")
        if not isinstance(share["disconnect_on_suspend"], bool):
            raise ConfigError(f"Share {index} in {path} has an invalid suspend policy.")
    if not isinstance(config["link_dir"], str):
        raise ConfigError(f"The link_dir field in {path} must be a string.")
    if not isinstance(config["links_enabled"], bool):
        raise ConfigError(f"The links_enabled field in {path} must be true or false.")
    if not isinstance(config["check_for_updates"], bool):
        raise ConfigError(f"The check_for_updates field in {path} must be true or false.")
    if config["credential_policy"] not in dict(CREDENTIAL_POLICIES):
        raise ConfigError(f"The credential_policy field in {path} is invalid.")
    return config


def effective_credential_policy(config, share):
    profile = credential_profile(config, share)
    if profile is not None:
        policy = profile["credential_policy"]
        return config["credential_policy"] if policy == CREDENTIAL_USE_GLOBAL else policy
    policy = share.get("credential_policy", CREDENTIAL_USE_GLOBAL)
    return config["credential_policy"] if policy == CREDENTIAL_USE_GLOBAL else policy


def credential_profile(config, share):
    profile_id = share.get("credential_profile_id", "")
    return next(
        (profile for profile in config.get("credential_profiles", [])
         if profile["id"] == profile_id),
        None,
    )


def credential_key(config, share):
    profile = credential_profile(config, share)
    return profile["id"] if profile is not None else share["id"]


def share_with_credentials(config, share):
    resolved = share.copy()
    profile = credential_profile(config, share)
    if profile is not None:
        resolved["username"] = profile["username"]
        resolved["domain"] = profile["domain"]
    return resolved


def load_config():
    if CONFIG_PATH.exists():
        try:
            return _read_config(CONFIG_PATH)
        except ConfigError as primary_error:
            if BACKUP_PATH.exists():
                try:
                    config = _read_config(BACKUP_PATH)
                    logger.warning("Recovered configuration from %s", BACKUP_PATH)
                    return config
                except ConfigError:
                    pass
            raise primary_error

    # Native installs historically used ~/.config directly. Flatpak exposes
    # that directory read-only so the first sandboxed run can copy existing
    # shares into its persistent XDG_CONFIG_HOME.
    if LEGACY_CONFIG_PATH != CONFIG_PATH and LEGACY_CONFIG_PATH.exists():
        config = _read_config(LEGACY_CONFIG_PATH)
        save_config(config)
        logger.info("Migrated configuration from %s", LEGACY_CONFIG_PATH)
        return config
    return default_config()


def load_config_file(path):
    """Validate a user-selected configuration before it replaces anything."""
    return _read_config(Path(path))


def export_config(config, path):
    """Export settings and share identities; secrets are never in the config."""
    destination = Path(path)
    try:
        _write_json_private(config, destination)
    except OSError as error:
        raise ConfigError(f"Could not export configuration to {destination}: {error}") from error


def _write_json_private(config, destination):
    """Atomically replace JSON without following a destination symlink."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as config_file:
            json.dump(config, config_file, indent=2)
            config_file.flush()
            os.fsync(config_file.fileno())
        temporary_path.chmod(0o600)
        temporary_path.replace(destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def save_config(config):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _write_json_private(config, CONFIG_PATH)
        _write_json_private(config, BACKUP_PATH)
    except (OSError, TypeError, ValueError) as error:
        raise ConfigError(f"Could not save {CONFIG_PATH}: {error}") from error
