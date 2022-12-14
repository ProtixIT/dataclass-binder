"""
Library to bind TOML data to Python dataclasses in a type-safe way.
"""

from ._impl import Binder, format_template

__all__ = [
    "Binder",
    "format_template",
]
