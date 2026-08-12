import logging
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler

from mountie import __version__


STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
LOG_DIR = STATE_HOME / "mountie"
LOG_PATH = LOG_DIR / "mountie.log"


def configure_logging():
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            LOG_PATH, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
        )
    except OSError:
        logging.basicConfig(level=logging.INFO)
        logging.getLogger(__name__).exception("Could not create the log file")
        return
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    logging.getLogger(__name__).info("Mountie %s starting", __version__)


def read_log():
    try:
        return LOG_PATH.read_text(encoding="utf-8")
    except OSError as error:
        return f"The log could not be read: {error}"
