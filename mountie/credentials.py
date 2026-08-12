import gi

gi.require_version("Secret", "1")
from gi.repository import GLib, Secret

from mountie.settings import APP_ID, CREDENTIAL_PERMANENT, CREDENTIAL_SESSION


SECRET_SCHEMA = Secret.Schema.new(
    APP_ID,
    Secret.SchemaFlags.NONE,
    {"share_id": Secret.SchemaAttributeType.STRING},
)


class CredentialError(RuntimeError):
    pass


def get_password(share_id):
    try:
        return Secret.password_lookup_sync(SECRET_SCHEMA, {"share_id": share_id}, None)
    except GLib.Error as error:
        raise CredentialError(f"Could not read the saved password: {error.message}") from error


def set_password(share_id, password, policy=CREDENTIAL_PERMANENT):
    if policy not in (CREDENTIAL_SESSION, CREDENTIAL_PERMANENT):
        raise ValueError("Passwords can only be stored for the session or permanently.")
    collection = (
        Secret.COLLECTION_SESSION
        if policy == CREDENTIAL_SESSION
        else Secret.COLLECTION_DEFAULT
    )
    try:
        # Prevent a policy change from leaving both session and permanent
        # entries with identical attributes and an ambiguous lookup result.
        Secret.password_clear_sync(SECRET_SCHEMA, {"share_id": share_id}, None)
        Secret.password_store_sync(
            SECRET_SCHEMA,
            {"share_id": share_id},
            collection,
            "Mountie credentials",
            password,
            None,
        )
    except GLib.Error as error:
        raise CredentialError(f"Could not save the password: {error.message}") from error


def clear_password(share_id):
    try:
        Secret.password_clear_sync(SECRET_SCHEMA, {"share_id": share_id}, None)
    except GLib.Error as error:
        raise CredentialError(f"Could not remove the saved password: {error.message}") from error
