# Dataclass Binder

Library to bind TOML data to Python dataclasses in a type-safe way.


## Usage

The `dataclass_binder` module contains the `Binder` class which makes it easy to bind TOML data, such as a configuration file, to Python [dataclasses](https://docs.python.org/3/library/dataclasses.html).

The binding is a two-step process:
- specialize the `Binder` class by using your top-level dataclass as a type argument
- call the `parse_toml()` method, providing an I/O stream for the configuration file as its argument

Put together, the code looks like this:

```py
import logging
import sys
from pathlib import Path

from dataclass_binder import Binder


logger = logging.getLogger(__name__)

if __name__ == "__main__":
    config_file = Path("config.toml")
    try:
        config = Binder[Config].parse_toml(config_file)
    except Exception as ex:
        logger.critical("Error reading configuration file '%s': %s", config_file, ex)
        sys.exit(1)
```

### Basic Types

Dataclass fields correspond to TOML keys. In the dataclass, underscores are used as word separators, while dashes are used in the TOML file. For example, the following TOML fragment:

```toml
database-url = 'postgresql://user:password@host/db'
port = 5432
```

can be bound to the following dataclass:

```py
from dataclasses import dataclass

@dataclass
class Config:
    database_url: str
    port: int
```

To keep these examples short, from now on `import` statements will only be included the first time a particular imported name is used.

Fields can be made optional by assigning a default value. Using `None` as a default value is allowed too:

```py
@dataclass
class Config:
    verbose: bool = False
    webhook_url: str | None = None
```

The `float` type can be used to bind floating point numbers.
Support for `Decimal` is not there at the moment but would be relatively easy to add, as `tomllib`/`tomli` has an option for that.

### Dates and Times

TOML handles dates and timestamps as first-class values.
Date, time and date+time TOML values are bound to `datetime.date`, `datetime.time` and `datetime.datetime` Python objects respectively.

There is also support for time intervals using `datetime.timedelta`:

```py
from datetime import timedelta

@dataclass
class Config:
    retry_after: timedelta
    delete_after: timedelta
```

Intervals shorter than a day can be specified using a TOML time value.
Longer intervals are supported by adding an `-hours`, `-days`, or `-weeks` suffix.
Other supported suffixes are `-minutes`, `-seconds`, `-milliseconds` and `-microseconds`, but these are there for completeness sake and less likely to be useful.
Here is an example TOML fragment corresponding to the dataclass above:

```toml
retry-after = 00:02:30
delete-after-days = 30
```

### Collections

Lists and dictionaries can be used to bind TOML arrays and tables.
If you want to make a `list` or `dict` optional, you need to provide a default value via the `default_factory` mechanism as usual, see the [dataclasses documentation](https://docs.python.org/3/library/dataclasses.html#mutable-default-values) for details.

```py
from dataclasses import dataclass, field

@dataclass
class Config:
    tags: list[str] = field(default_factory=list)
    limits: dict[str, int]
```

The dataclass above can be used to bind the following TOML fragment:

```toml
tags = ["production", "development"]
limits = {ram-gb = 1, disk-gb = 100}
```

An alternative to `default_factory` is to use a homogeneous (single element type) tuple:

```py
@dataclass
class Config:
    tags: tuple[str, ...] = ()
    limits: dict[str, int]
```

Heterogeneous tuples are supported too: for example `tuple[str, bool]` binds a TOML array that must always have a string as its first element and a Boolean as its second and last element.
It is generally clearer though to define a separate dataclass when you need more than one value to configure something:

```py
@dataclass
class Webhook:
    url: str
    token: str

@dataclass
class Config:
    webhooks: tuple[Webhook, ...] = ()
```

The extra keys (`url` and `token` in this example) provide the clarity:

```
webhooks = [
    {url = "https://host1/notify", token = "12345"},
    {url = "https://host2/hook", token = "frperg"}
]
```

TOML's array-of-tables syntax can make this configuration a bit easier on the eyes:

```
[[webhooks]]
url = "https://host1/notify"
token = "12345"

[[webhooks]]
url = "https://host2/hook"
token = "frperg"
```

Always define additional dataclasses at the module level in your Python code: if the class is for example defined inside a function, the `Binder` specialization will not be able to find it.

### Plugins

To select plugins to activate, you can bind Python classes or modules using `type[BaseClass]` and `types.ModuleType` annotations respectively:

```py
from dataclasses import dataclass, field
from types import ModuleType

from supertool.plugins import PostProcessor


@dataclass
class PluginConfig:
    postprocessors = tuple[type[PostProcessor], ...] = ()
    modules: dict[str, ModuleType] = field(default_factory=dict)
```

In the TOML, you specify Python classes or modules using their fully qualified names:

```toml
postprocessors = ["supertool_addons.reporters.JSONReport"]
modules = {lint = "supertool_addons.linter"}
```

There is no mechanism yet to add configuration to be used by the plugins.

### Immutable

If you prefer immutable configuration objects, you can achieve that using the `frozen` argument of the `dataclass` decorator and using abstract collection types in the annotations. For example, the following dataclass will be instantiated with a `tuple` object for `tags` and an immutable dictionary view for `limits`:

```py
from collections.abc import Mapping, Sequence


@dataclass(frozen=True)
class Config:
    tags: Sequence[str] = ()
    limits: Mapping[str, int]
```

### Troubleshooting

Finally, a troubleshooting tip: instead of the full `Binder[Config].parse_toml()`, first try to execute only `Binder[Config]`.
If that fails, the problem is in the dataclass definitions.
If that succeeds, but the `parse_toml()` call fails, the problem is that the TOML file does not match the format defined in the dataclasses.


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
