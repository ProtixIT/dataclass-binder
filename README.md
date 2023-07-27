# Dataclass Binder

Library to bind TOML data to Python dataclasses in a type-safe way.


## Features

Currently it has the following properties that might set it apart from other data binding libraries:

- requires Python 3.10+
- relies only on dataclasses from the Python standard library
- detailed error messages which mention location, expected data and actual data
- strict parsing which considers unknown keys to be errors
- support for durations (`timedelta`)
- support for immutable (frozen) dataclasses
- can bind data from files, I/O streams or pre-parsed dictionaries
- can generate configuration templates from dataclass definitions

This library was originally designed for parsing configuration files.
As TOML's data model is very similar to JSON's, adding support for JSON in the future would be an option and would make the library useful for binding HTTP API requests.


## Maturity

This library is fully type-checked, has unit tests which provide 100% branch coverage and is used in production, so it should be reliable.

The API might still change in incompatible ways until the 1.0 release.
In particular the following aspects are subject to change:

- use of key suffixes for `timedelta`: this mechanism doesn't work for arrays
- the handling of separators in keys: currently `-` in TOML is mapped to `_` in Python and `_` is forbidden in TOML; most applications seem to accept both `-` and `_` in TOML instead


## Why Dataclasses?

A typical TOML, JSON or YAML parser returns the parse results as a nested dictionary.
You might wonder why you would want to use a data binding library rather than just getting the values directly from that dictionary.

Let's take the following example code for a service that connects to a database using a connection URL configured in a TOML file:

```py
import tomllib  # or 'tomli' on Python <3.11


def read_config() -> dict:
    with open("config.toml", "rb") as f:
        config = tomllib.load(f)
    return config

def handle_request(config: dict) -> None:
    url = config["database-url"]
    print("connect to database:", url)

config = read_config()
...
handle_request(config)
```

If the configuration is missing a `database-url` key or its value is not a string, this service would start up without complaints and then fail when the first requests comes in.
It would be better to instead check the configuration on startup, so let's add code for that:

```py
def read_config():
    with open("config.toml", "rb") as f:
        config = tomllib.load(f)

    url = config["database-url"]
    if not isinstance(url, str):
        raise TypeError(
            f"Value for 'database-url' has type '{type(url).__name__}', expected 'str'"
        )

    return config
```

Imagine you have 20 different configurable options: you'd need this code 20 times.

Now let's assume that you use a type checker like `mypy`.
Inside `read_config()`, the type checker will know that `url` is a `str`, but if you fetch the same value elsewhere in the code, that information is lost:

```py
def handle_request(config: dict) -> None:
    url = config["database-url"]
    reveal_type(url)
    print("connect to database:", url)
```

When you run `mypy` on this code, it will output 'Revealed type is "Any"'.
Falling back to `Any` means type checking will not be able to find type mismatches and autocomplete in an IDE will not work well either.

Declaring the desired type in a dataclass solves both these issues:
- the type can be verified at runtime before instantiating the dataclass
- tooling knows the type when you read the value from the dataclass

Having the dataclass as a central and formal place for defining the configuration format is also an advantage.
For example, it enables automatic generation of a documented configuration file template.


## Usage

The `dataclass_binder` module contains the `Binder` class which makes it easy to bind TOML data, such as a configuration file, to Python [dataclasses](https://docs.python.org/3/library/dataclasses.html).

The binding is a two-step process:
- instantiate the `Binder` class by passing your top-level dataclass as an argument
- call the `parse_toml()` method, providing the path of the configuration file as its argument

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
        config = Binder(Config).parse_toml(config_file)
    except Exception as ex:
        logger.critical("Error reading configuration file '%s': %s", config_file, ex)
        sys.exit(1)
```

### Binding a Pre-parsed Dictionary

If you don't want to bind the contents of a full file, there is also the option to bind a pre-parsed dictionary instead.
For this, you can use the `bind()` method on the `Binder` object.

For example, the following service is configured by one table within a larger TOML configuration file:

```py
import tomllib  # or 'tomli' on Python <3.11
from dataclass_binder import Binder


with open("config.toml", "rb") as f:
    config = tomllib.load(f)
service_config = Binder(ServiceConfig).bind(config["service"])
```

To keep these examples short, from now on `import` statements will only be included the first time a particular imported name is used.

### Basic Types

Dataclass fields correspond to TOML keys. In the dataclass, underscores are used as word separators, while dashes are used in the TOML file. Let's configure a service that listens on a TCP port for requests and stores its data in a database, using the following TOML fragment:

```toml
database-url = 'postgresql://user:password@host/db'
port = 8080
```

This configuration can be bound to the following dataclass:

```py
from dataclasses import dataclass

@dataclass
class Config:
    database_url: str
    port: int
    verbose: bool
```

The `float` type can be used to bind floating point numbers.
Support for `Decimal` is not there at the moment but would be relatively easy to add, as `tomllib`/`tomli` has an option for that.

### Defaults

Fields can be made optional by assigning a default value. Using `None` as a default value is allowed too:

```py
@dataclass
class Config:
    verbose: bool = False
    webhook_url: str | None = None
```

If you want to mix fields with and without defaults in any order, mark the fields as keyword-only:

```py
@dataclass(kw_only=True)
class Config:
    database_url: str
    verbose: bool = False
    port: int
```

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

```toml
webhooks = [
    {url = "https://host1/notify", token = "12345"},
    {url = "https://host2/hook", token = "frperg"}
]
```

TOML's array-of-tables syntax can make this configuration a bit easier on the eyes:

```toml
[[webhooks]]
url = "https://host1/notify"
token = "12345"

[[webhooks]]
url = "https://host2/hook"
token = "frperg"
```

Always define additional dataclasses at the module level in your Python code: if the class is for example defined inside a function, the `Binder` constructor will not be able to find it.

### Untyped Data

Sometimes the full structure of the data you want to bind is either too complex or too much in flux to be worth fully annotating.
In such a situation, you can use `typing.Any` as the annotation to simply capture the output of Python's TOML parser without type-checking it.

In the following example, a service uses the Python standard library logging implementation, configured using the [configuration dictionary schema](https://docs.python.org/3/library/logging.config.html#logging-config-dictschema):

```py
import logging.config
from dataclasses import dataclass
from typing import Any

from dataclass_binder import Binder


@dataclass
class Config:
    database_url: str
    logging: Any


def run(url: str) -> None:
    logging.info("Service starting")


if __name__ == "__main__":
    config = Binder[Config].parse_toml("service.toml")
    logging.config.dictConfig(config.logging)
    run(config.database_url)
```

The `service.toml` configuration file for this service could look like this:

```toml
database-url = 'postgresql://user:password@host/db'

[logging]
version = 1

[logging.root]
level = 'INFO'
handlers = ['file']

[logging.handlers.file]
class = 'logging.handlers.RotatingFileHandler'
filename = 'service.log'
formatter = 'simple'

[logging.formatters.simple]
format = '%(asctime)s %(name)s %(levelname)s %(message)s'
```

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

### Layered Binding

`Binder` can be instantiated from a dataclass object rather than the dataclass itself.
The dataclass object will provide new default values when binding data to it.
This can be used to implement a layered configuration parsing mechanism, where there is a default configuration that can be customized using a system-wide configuration file and/or a per-user configuration file:

```py
config = Config()
if system_config_path.exists():
    config = Binder(config).parse_toml(system_config_path)
if user_config_path.exists():
    config = Binder(config).parse_toml(user_config_path)
```

Later layers can override individual fields in nested dataclasses, allowing fine-grained configuration merging, but collections are replaced whole instead of merged.

### Generating a Configuration Template

To provide users with a starting point for configuring your application/service, you can automatically generate a configuration template from the information in the dataclass.

For example, when the following dataclass defines your configuration:

```py
@dataclass
class Config:
    database_url: str
    """The URL of the database to connect to."""

    port: int = 12345
    """TCP port on which to accept connections."""
```

You can generate a template configuration file using:

```py
from dataclass_binder import Binder


for line in Binder(Config).format_toml_template():
    print(line)
```

Which will print:

```toml
# The URL of the database to connect to.
# Mandatory.
database-url = '???'

# TCP port on which to accept connections.
# Default:
# port = 12345
```

It is also possible to provide placeholder values by passing a dataclass instance rather than the dataclass itself to `format_toml_template()`:

```py
TEMPLATE = Config(
    database_url="postgresql://<username>:<password>@<hostname>/<database name>",
    port=8080,
)

for line in Binder(TEMPLATE).format_toml_template():
    print(line)
```

Which will print:

```toml
# The URL of the database to connect to.
# Mandatory.
database-url = 'postgresql://<username>:<password>@<hostname>/<database name>'

# TCP port on which to accept connections.
# Default:
# port = 12345
port = 8080
```

### Troubleshooting

Finally, a troubleshooting tip: instead of the full `Binder(Config).parse_toml()`, first try to execute only `Binder(Config)`.
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


## Release Procedure

- Verify that CI passes on the branch that you want to release (typically `main`)
- Create a release on the GitHub web interface; name the tag `v<major>.<minor>.<patchlevel>`
- After publishing the release on GitHub, the package will be built and published on PyPI automatically via Actions


## Deprecations

### Binder Specialization

Prior to version 0.2.0, the `Binder` class was specialized using a type argument (`Binder[Config]`) rather than instantiation (`Binder(config)`). The old syntax is still supported for now, but the backwards compatibility might be removed in a minor release prior to 1.0 if it becomes a maintenance burden, so please update your code.

### Template Formatting

In version 0.3.0, the function `format_template()` has been replaced by the method `Binder.format_toml_template()`. The old function is still available for now.

## Changelog

### 0.1.0 - 2023-02-21:

- First open source release; thanks to my employer [Protix](https://protix.eu/) for making this possible

### 0.1.1 - 2023-02-22:

- Relax `Binder.bind()` argument type to `Mapping` ([#3](https://github.com/ProtixIT/dataclass-binder/issues/3))

### 0.1.2 - 2023-03-03:

- Fix `get()` and `[]` on object bound to read-only mapping ([#6](https://github.com/ProtixIT/dataclass-binder/issues/6))

### 0.1.3 - 2023-03-05:

- Ignore dataclass fields with `init=False` ([#2](https://github.com/ProtixIT/dataclass-binder/issues/2))

### 0.2.0 - 2023-06-26:

- Instantiate `Binder` instead of specializing it ([#14](https://github.com/ProtixIT/dataclass-binder/pull/14))
- Support `typing.Any` as a field annotation ([#10](https://github.com/ProtixIT/dataclass-binder/issues/10))
- Fix crash in `format_template()` on optional fields with non-string annotations ([#16](https://github.com/ProtixIT/dataclass-binder/pull/16))

### 0.3.0 - 2023-07-13:

- Replace `format_template()` function by `Binder.format_toml_template()` method ([#23](https://github.com/ProtixIT/dataclass-binder/pull/23))
- Format nested dataclasses as TOML tables ([#25](https://github.com/ProtixIT/dataclass-binder/pull/25))
- Format untyped mappings and sequences as TOML tables ([#27](https://github.com/ProtixIT/dataclass-binder/pull/27))
- Fix formatting of `init=False` field in nested dataclasses ([#22](https://github.com/ProtixIT/dataclass-binder/pull/22))
- Fix annotation evaluation on inherited dataclasses ([#21](https://github.com/ProtixIT/dataclass-binder/pull/21))

### 0.3.1 - 2023-07-17:

- Generate template in depth-first order ([#28](https://github.com/ProtixIT/dataclass-binder/pull/28))
- Fix binder creation and formatting for recursive dataclasses ([#28](https://github.com/ProtixIT/dataclass-binder/pull/28))

### 0.3.2 - 2023-07-27:

- Document fields with a `default_factory` as optional in template ([#35](https://github.com/ProtixIT/dataclass-binder/pull/35))
- Omit values that are formatted equally to the default ([#36](https://github.com/ProtixIT/dataclass-binder/pull/36))
- Require optional fields to have `None` as their default ([#37](https://github.com/ProtixIT/dataclass-binder/pull/37))
