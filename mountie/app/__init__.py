"""Mountie's desktop application package."""


def main():
    """Preserve the ``mountie.app:main`` console entry point lazily."""
    from mountie.app.main import main as run

    return run()


__all__ = ["main"]
