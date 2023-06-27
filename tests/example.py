from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

URL: TypeAlias = str


@dataclass(frozen=True)
class Config:
    """Configuration for an example service."""

    database_url: URL
    """The URL of the database to connect to."""

    port: int = 12345
    """TCP port on which to accept connections."""

    def dummy(self) -> None:
        """This method only exists to test whether the docstring parsing code ignores it."""


TEMPLATE = Config(
    port=8080,
    database_url="postgresql://<username>:<password>@<hostname>/<database name>",
)
