# Dataclass Binder

Library to bind TOML data to Python dataclasses in a type-safe way.


## Development Environment

[Poetry](https://python-poetry.org/) is used to set up a virtual environment with all the dependencies and development tools that you need:

    $ cd dataclass-binder
    $ poetry install

You can activate a shell which contains the development tools in its search path:

    $ poetry shell

We recommend setting up pre-commit hooks for Git in the `dataclass-binder` work area.
These hooks automatically run a few simple checks and cleanups when you create a new commit.
After you first set up your virtual environment with Poetry, run this command to install the pre-commit hooks:

    $ pre-commit install
