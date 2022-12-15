from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from io import BytesIO
from types import ModuleType
from typing import Any

from pytest import fixture, mark, raises

from dataclass_binder import Binder, format_template
from dataclass_binder._impl import _iter_format_value, format_toml_pair, get_field_docstrings

from . import example


def single_value_dataclass(annotation: type[Any]) -> type[Any]:
    @dataclass
    class DC:
        value: object
        __annotations__["value"] = annotation

    return DC


def parse_toml(dc: type[Any], toml: str) -> Any:
    binder = Binder[dc]  # type: ignore[valid-type]

    with BytesIO(toml.encode()) as stream:
        obj = binder.parse_toml(stream)

    return getattr(obj, "value")


def round_trip(value: object, dc: type[Any]) -> Any:
    """
    Convert data in a dataclass to TOML and back.

    The dataclass must have a single field named "value".
    """

    toml = format_toml_pair("value", value)
    print(repr(value), "->", toml)
    return parse_toml(dc, toml)


@mark.parametrize(
    "value",
    (
        -1,
        0,
        12345,
        -1.0,
        0.0,
        3.1415927,
        1.23e30,
        1.23e-30,
        True,
        False,
        "",
        "simple",
        "single'quote",
        'double"quote',
        "\"both\" 'quotes'",
        "embedded\nnewline",
        r"back\slash",
        'I\'m a string. "You can quote me". Name\tJos\u00E9\nLocation\tSF.',
        "complex string with back\\slash, \"both\" 'quotes' and \u0000control\u007Fchars\u0007",
        "\U0001F44D",
        date(2022, 10, 5),
        datetime(2022, 10, 5, 19, 16, 29),
        time(19, 16, 29),
        timedelta(hours=12, minutes=34, seconds=56),
        timedelta(microseconds=99999999999),
        timedelta(milliseconds=99999999),
        timedelta(seconds=99999),
        timedelta(minutes=2000),
        timedelta(hours=83),
        timedelta(days=2),
        timedelta(weeks=3),
        example,
    ),
)
def test_format_value_round_trip(value: object) -> None:
    dc = single_value_dataclass(type(value))
    assert round_trip(value, dc) == value


def test_format_value_class() -> None:
    dc = single_value_dataclass(type)
    assert round_trip(example.Config, dc) is example.Config


def test_format_value_list_simple() -> None:
    """A sequence is formatted as a TOML array."""
    value = [1, 2, 3]
    dc = single_value_dataclass(list[int])
    assert round_trip(value, dc) == value


def test_format_value_list_suffix() -> None:
    """
    It is an error to use a value that requires a suffix in a sequence.

    TODO: Reconsider the design decision to use key suffixes, as it leads to this gap in expressiveness.
    """
    dc = single_value_dataclass(list[timedelta])
    assert round_trip([], dc) == []
    assert round_trip([timedelta(hours=2)], dc) == [timedelta(hours=2)]
    with raises(
        ValueError, match=r"^Value datetime\.timedelta\(days=2\) in array cannot be expressed without key suffix$"
    ):
        round_trip([timedelta(days=2)], dc)


def test_format_value_dict() -> None:
    """
    A mapping is formatted as a TOML inline table.

    Bare keys are used where possible, otherwise quoted keys.
    """
    dc = single_value_dataclass(dict[str, int])
    value = {"a": 1, "b": 2, "c": 3}
    assert format_toml_pair("value", value) == "value = {a = 1, b = 2, c = 3}"
    assert round_trip(value, dc) == value
    value["a space"] = 4
    value["a.dot"] = 5
    value[""] = 6
    assert format_toml_pair("value", value) == "value = {a = 1, b = 2, c = 3, 'a space' = 4, 'a.dot' = 5, '' = 6}"
    assert round_trip(value, dc) == value


def test_format_value_dict_suffix() -> None:
    """
    Values that require a suffix can be used in a mapping.

    TODO: Actually, in our current implementation they cannot.
          I don't want to spend time fixing this though if we might throw out the entire suffix mechanism;
          see test_format_value_list_suffix() for details.
    """
    dc = single_value_dataclass(dict[str, timedelta])
    assert round_trip({}, dc) == {}
    assert round_trip({"delay": timedelta(hours=2)}, dc) == {"delay": timedelta(hours=2)}
    # assert round_trip({"delay": timedelta(days=2)}, dc) == {"delay": timedelta(days=2)}
    assert format_toml_pair("value", {"delay": timedelta(days=2)}) == "value = {delay-days = 2}"


def test_format_value_nested_dataclass() -> None:
    @dataclass
    class Inner:
        key_containing_underscores: bool
        maybesuffix: timedelta

    dc = single_value_dataclass(Inner)
    value = Inner(True, timedelta(days=2))
    assert round_trip(value, dc) == value


def test_format_value_unsupported_type() -> None:
    with raises(TypeError, match="^NoneType$"):
        format_toml_pair("unsupported", None)
    with raises(TypeError, match="^NoneType$"):
        list(_iter_format_value(None))


def test_docstring_extraction_example() -> None:
    docstrings = get_field_docstrings(example.Config)
    assert docstrings == {
        "database_url": "The URL of the database to connect to.",
        "port": "TCP port on which to accept connections.",
    }


def test_docstring_extraction_indented() -> None:
    dc = single_value_dataclass(int)
    docstrings = get_field_docstrings(dc)
    assert docstrings == {}


@dataclass(kw_only=True)
class TemplateConfig:
    happiness: str
    """Field without default."""

    flag: bool
    # Field without docstring.

    module: ModuleType
    """
    Multi-line docstring.
    """

    custom_class: type[Any] | None = None
    """Optional field."""

    number: int = 123
    """Field with default value."""

    another_number: float = 0.5
    """
    This docstring...

    ...consists of multiple paragraphs.
    """

    multi_type: str | int

    bad_annotation: NoSuchType  # type: ignore[name-defined]  # noqa


def test_format_template_full() -> None:
    """The template generated for the TemplateConfig class matches our golden output."""
    template = "\n".join(format_template(TemplateConfig))
    assert template == (
        """
# Field without default.
# Mandatory.
happiness = '???'

# Mandatory.
flag = true | false

# Multi-line docstring.
# Mandatory.
module = 'fully.qualified.module.name'

# Optional field.
# Optional.
# custom-class = 'fully.qualified.class.name'

# Field with default value.
# Default:
# number = 123

# This docstring...
#
# ...consists of multiple paragraphs.
#
# Default:
# another-number = 0.5

# Mandatory.
multi-type = '???' | 0

# Mandatory.
bad-annotation = ???
""".strip()
    )


@dataclass
class NestedConfig:
    inner_int: int
    inner_str: str
    optional: str | None = None
    with_default: str = "n/a"


@mark.parametrize(
    "field_type", (str, int, float, datetime, date, time, timedelta, list[str], dict[str, int], NestedConfig)
)
def test_format_template_valid_value(field_type: type[Any]) -> None:
    """
    The template generated for the given field type is valid TOML and the value has the right type.

    Not all templates values are valid TOML, but the selected parameters are.
    """
    dc = single_value_dataclass(field_type)
    toml = "\n".join(format_template(dc))
    print(field_type, "->", toml)
    parse_toml(dc, toml)


@dataclass(kw_only=True)
class PopulatedConfig:
    source_database_connection_url: str
    destination_database_connection_url: str = "sqlite://"
    nested: NestedConfig
    webhook_urls: tuple[str, ...] = ()


def test_format_template_populated() -> None:
    config = PopulatedConfig(
        source_database_connection_url="postgresql://<username>:<password>@<hostname>/<database name>",
        destination_database_connection_url="sqlite://",
        nested=NestedConfig(5, "foo"),
        webhook_urls=("https://host1/refresh", "https://host2/refresh"),
    )
    template = "\n".join(format_template(config))
    assert template == (
        """
# Mandatory.
source-database-connection-url = 'postgresql://<username>:<password>@<hostname>/<database name>'

# Default:
# destination-database-connection-url = 'sqlite://'

# Mandatory.
nested = {inner-int = 5, inner-str = 'foo', with-default = 'n/a'}

# Default:
# webhook-urls = []
webhook-urls = ['https://host1/refresh', 'https://host2/refresh']
""".strip()
    )


@fixture
def sourceless_class() -> type[Any]:
    """A class for which no source code is available."""

    defs: dict[str, Any] = {}
    exec(
        """
@dataclass
class C:
    value: int
    "This docstring cannot be extracted."
""",
        {"dataclass": dataclass},
        defs,
    )
    C: type[Any] = defs["C"]
    return C


def test_docstring_extraction_no_source(sourceless_class: type[Any]) -> None:
    """If no source code is available, we can't extract docstrings; fail gracefully."""
    docstrings = get_field_docstrings(sourceless_class)
    assert docstrings == {}


def test_format_template_no_module(sourceless_class: type[Any]) -> None:
    """If we can't find the module, builtin types are still available."""
    # We set __module__ to a non-existing name to force inspect.getmodule() to return None.
    # This might not be necessary anymore if the standard library gets fixed.
    #   https://github.com/python/cpython/issues/98239
    sourceless_class.__module__ = "<no source>"
    template = "\n".join(format_template(sourceless_class))
    assert template == (
        """
# Mandatory.
value = 0
""".strip()
    )
