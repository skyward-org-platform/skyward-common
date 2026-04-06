"""Test that skyward.functions can be imported without IPython installed."""


def test_functions_importable_without_ipython():
    """functions.py must not fail at import time when IPython is absent."""
    import importlib
    import sys
    # Remove IPython from sys.modules to simulate it not being installed
    ipython_mods = [k for k in sys.modules if k.startswith("IPython")]
    saved = {k: sys.modules.pop(k) for k in ipython_mods}
    try:
        # Force reimport
        if "skyward.functions" in sys.modules:
            del sys.modules["skyward.functions"]
        import skyward.functions  # noqa: F401
    finally:
        sys.modules.update(saved)
