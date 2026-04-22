"""pytest configuration for the shared spec fixtures.

Keeps the fixture tree side-effect-free on import — each test imports the
fixture's own `builder.py` module directly.
"""
