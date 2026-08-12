import json
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

CONFIG_DIR = Path.home() / ".config" / "mountie"
CONFIG_PATH = CONFIG_DIR / "config.json"


class ConfigError(RuntimeError):
    pass


def default_config():
    return {
        "shares": [],
        "theme": THEME_SYSTEM,
        "link_dir": DEFAULT_LINK_DIR,
        "links_enabled": True,
    }


def load_config():
    if not CONFIG_PATH.exists():
        return default_config()
    try:
        with CONFIG_PATH.open() as config_file:
            config = json.load(config_file)
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"Could not read {CONFIG_PATH}: {error}") from error
    if not isinstance(config, dict):
        raise ConfigError(f"{CONFIG_PATH} must contain a JSON object.")
    config.setdefault("shares", [])
    config.setdefault("theme", THEME_SYSTEM)
    config.setdefault("link_dir", DEFAULT_LINK_DIR)
    config.setdefault("links_enabled", True)
    if not isinstance(config["shares"], list):
        raise ConfigError(f"The shares field in {CONFIG_PATH} must be a list.")
    required_share_fields = ("id", "label", "host", "share")
    for index, share in enumerate(config["shares"], start=1):
        if not isinstance(share, dict) or any(field not in share for field in required_share_fields):
            raise ConfigError(f"Share {index} in {CONFIG_PATH} is incomplete or invalid.")
    if not isinstance(config["link_dir"], str):
        raise ConfigError(f"The link_dir field in {CONFIG_PATH} must be a string.")
    if not isinstance(config["links_enabled"], bool):
        raise ConfigError(f"The links_enabled field in {CONFIG_PATH} must be true or false.")
    return config


def save_config(config):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        temporary_path = CONFIG_PATH.with_suffix(".tmp")
        with temporary_path.open("w") as config_file:
            json.dump(config, config_file, indent=2)
        temporary_path.replace(CONFIG_PATH)
        CONFIG_PATH.chmod(0o600)
    except (OSError, TypeError, ValueError) as error:
        raise ConfigError(f"Could not save {CONFIG_PATH}: {error}") from error
