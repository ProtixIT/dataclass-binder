from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from io import BytesIO
from pathlib import Path
from types import ModuleType, NoneType, UnionType
from typing import Any, TypeVar, Union, cast, get_args, get_origin

import pytest

from dataclass_binder import Binder, format_template
from dataclass_binder._impl import _iter_format_value, format_toml_pair, get_field_docstrings

from . import example

T = TypeVar("T")


def format_annotation(annotation: object) -> str:
    origin = get_origin(annotation)
    if origin is None:
        if annotation is NoneType:
            return "None"
        elif annotation is Any:
            return "Any"
        elif annotation is ModuleType:
            return "ModuleType"
        elif isinstance(annotation, type):
            return annotation.__name__
        else:
            raise AssertionError(annotation)
    elif origin is UnionType or origin is Union:
        return " | ".join(format_annotation(arg) for arg in get_args(annotation))
    else:
        return f"{origin.__name__}[{', '.join(format_annotation(arg) for arg in get_args(annotation))}]"


def single_value_dataclass(value_type: Any, *, optional: bool = False, string: bool = False) -> type[Any]:
    annotation = value_type | None if optional else value_type
    if string:
        annotation = format_annotation(annotation)

    @dataclass
    class DC:
        if optional:
            value: object = None
        else:
            value: object  # type: ignore[no-redef]
        __annotations__["value"] = annotation

    return DC


def parse_toml(dc: type[T], toml: str) -> T:
    binder = Binder(dc)

    with BytesIO(toml.encode()) as stream:
        return binder.parse_toml(stream)


def round_trip(obj: T) -> T:
    """
    Convert data in a dataclass to TOML and back.
    """

    toml = "\n".join(Binder(obj).format_toml_template())
    print(f"TOML <- {obj!r}")  # noqa: T201
    print(toml)  # noqa: T201
    return parse_toml(type(obj), toml)


def round_trip_value(value: T, dc: type[Any]) -> T:
    """
    Convert data in a dataclass to TOML and back.

    The dataclass must have a single field named "value".
    """

    obj = round_trip(dc(value=value))
    return cast(T, obj.value)


EXAMPLE_NATIVE_VALUES = (
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
)
"""Values that have a native representation in TOML."""

EXAMPLE_CONVERTED_VALUES = (
    timedelta(hours=12, minutes=34, seconds=56),
    timedelta(microseconds=99999999999),
    timedelta(milliseconds=99999999),
    timedelta(seconds=99999),
    timedelta(minutes=2000),
    timedelta(hours=83),
    timedelta(days=2),
    timedelta(weeks=3),
    example,
)
"""Values for which we have custom conversions."""


@pytest.mark.parametrize("value", EXAMPLE_NATIVE_VALUES + EXAMPLE_CONVERTED_VALUES)
@pytest.mark.parametrize("optional", (True, False))
@pytest.mark.parametrize("string", (True, False))
def test_format_value_round_trip_exact(*, value: object, optional: bool, string: bool) -> None:
    dc = single_value_dataclass(type(value), optional=optional, string=string)
    assert round_trip_value(value, dc) == value


@pytest.mark.parametrize("value", EXAMPLE_NATIVE_VALUES)
@pytest.mark.parametrize("optional", (True, False))
@pytest.mark.parametrize("string", (True, False))
def test_format_value_round_trip_any(*, value: object, optional: bool, string: bool) -> None:
    dc = single_value_dataclass(Any, optional=optional, string=string)
    assert round_trip_value(value, dc) == value


@pytest.mark.parametrize("optional", (True, False))
@pytest.mark.parametrize("string", (True, False))
def test_format_value_path(*, optional: bool, string: bool) -> None:
    value = Path("/var/log/lumberjack/")
    dc = single_value_dataclass(Path, optional=optional, string=string)
    assert round_trip_value(value, dc) == value


@pytest.mark.parametrize("optional", (True, False))
@pytest.mark.parametrize("string", (True, False))
def test_format_value_class(*, optional: bool, string: bool) -> None:
    dc = single_value_dataclass(type, optional=optional, string=string)
    assert round_trip_value(example.Config, dc) is example.Config


@pytest.mark.parametrize("optional", (True, False))
@pytest.mark.parametrize("string", (True, False))
def test_format_value_list_simple(*, optional: bool, string: bool) -> None:
    """A sequence is formatted as a TOML array."""
    value = [1, 2, 3]
    dc = single_value_dataclass(list[int], optional=optional, string=string)
    assert round_trip_value(value, dc) == value


@pytest.mark.parametrize("optional", (True, False))
@pytest.mark.parametrize("string", (True, False))
def test_format_value_list_suffix(*, optional: bool, string: bool) -> None:
    """
    It is an error to use a value that requires a suffix in a sequence.

    TODO: Reconsider the design decision to use key suffixes, as it leads to this gap in expressiveness.
    """
    dc = single_value_dataclass(list[timedelta], optional=optional, string=string)
    assert round_trip_value([], dc) == []
    assert round_trip_value([timedelta(hours=2)], dc) == [timedelta(hours=2)]
    with pytest.raises(
        ValueError, match=r"^Value datetime\.timedelta\(days=2\) in array cannot be expressed without key suffix$"
    ):
        round_trip_value([timedelta(days=2)], dc)


@pytest.mark.parametrize("optional", (True, False))
@pytest.mark.parametrize("string", (True, False))
def test_format_value_dict(*, optional: bool, string: bool) -> None:
    """
    A mapping is formatted as a TOML inline table.

    Bare keys are used where possible, otherwise quoted keys.
    """
    dc = single_value_dataclass(dict[str, int], optional=optional, string=string)
    value = {"a": 1, "b": 2, "c": 3}
    assert format_toml_pair("value", value) == "value = {a = 1, b = 2, c = 3}"
    assert round_trip_value(value, dc) == value
    value["a space"] = 4
    value["a.dot"] = 5
    value[""] = 6
    assert format_toml_pair("value", value) == "value = {a = 1, b = 2, c = 3, 'a space' = 4, 'a.dot' = 5, '' = 6}"
    assert round_trip_value(value, dc) == value


@pytest.mark.parametrize("optional", (True, False))
@pytest.mark.parametrize("string", (True, False))
def test_format_value_dict_suffix(*, optional: bool, string: bool) -> None:
    """
    Values that require a suffix can be used in a mapping.

    TODO: Actually, in our current implementation they cannot.
          I don't want to spend time fixing this though if we might throw out the entire suffix mechanism;
          see test_format_value_list_suffix() for details.
    """
    dc = single_value_dataclass(dict[str, timedelta], optional=optional, string=string)
    assert round_trip_value({}, dc) == {}
    assert round_trip_value({"delay": timedelta(hours=2)}, dc) == {"delay": timedelta(hours=2)}
    # assert round_trip({"delay": timedelta(days=2)}, dc) == {"delay": timedelta(days=2)}  # noqa: ERA001
    assert format_toml_pair("value", {"delay": timedelta(days=2)}) == "value = {delay-days = 2}"


def test_format_empty_dataclass() -> None:
    @dataclass
    class Empty:
        pass

    assert list(Binder(Empty).format_toml_template()) == []


@dataclass(kw_only=True)
class Inner:
    key_containing_underscores: bool
    maybesuffix: timedelta
    behind_the_curtain: str = field(init=False, default="wizard")


@pytest.mark.parametrize("optional", (True, False))
@pytest.mark.parametrize("string", (True, False))
def test_format_value_nested_dataclass(*, optional: bool, string: bool) -> None:
    dc = single_value_dataclass(Inner, optional=optional, string=string)
    value = Inner(key_containing_underscores=True, maybesuffix=timedelta(days=2))
    assert round_trip_value(value, dc) == value


def test_format_value_unsupported_type() -> None:
    with pytest.raises(TypeError, match="^NoneType$"):
        format_toml_pair("unsupported", None)
    with pytest.raises(TypeError, match="^NoneType$"):
        list(_iter_format_value(None))


def test_docstring_extraction_example() -> None:
    docstrings = get_field_docstrings(example.Config)
    assert docstrings == {
        "database_url": "The URL of the database to connect to.",
        "port": "TCP port on which to accept connections.",
    }


@pytest.mark.parametrize("optional", (True, False))
@pytest.mark.parametrize("string", (True, False))
def test_docstring_extraction_indented(*, optional: bool, string: bool) -> None:
    dc = single_value_dataclass(int, optional=optional, string=string)
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

    expiry: timedelta

    certificate: Path

    multi_type: str | int

    derived: int = field(init=False)
    """Excluded field."""

    def __post_init__(self) -> None:
        self.derived = (2 if self.flag else 3) * self.number


def test_format_template_full() -> None:
    """The template generated for the TemplateConfig class matches our golden output."""
    template = "\n".join(Binder(TemplateConfig).format_toml_template())
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
expiry = 00:00:00

# Mandatory.
certificate = '/path/to/dir_or_file'

# Mandatory.
multi-type = '???' | 0
""".strip()
    )


def test_format_template_old() -> None:
    """The deprecated `format_template()` function  is still supported."""
    template_old = "\n".join(format_template(TemplateConfig))
    template_new = "\n".join(Binder(TemplateConfig).format_toml_template())
    assert template_old == template_new


@pytest.mark.parametrize("optional", (True, False))
@pytest.mark.parametrize("string", (True, False))
def test_format_dataclass_inline(*, optional: bool, string: bool) -> None:
    """
    A nested dataclass can be formatted as a TOML inline table.

    We prefer to format dataclasses as full (non-inline) tables, but sometimes we must format them inline,
    for example when they share an array with non-table values.
    """
    value = TemplateConfig(
        happiness="easy",
        flag=True,
        module=example,
        expiry=timedelta(days=3),
        certificate=Path("secrets/copper.key"),
        multi_type=-1,
    )
    formatted = format_toml_pair("value", value)
    assert formatted == (
        "value = {"
        "happiness = 'easy', "
        "flag = true, "
        "module = 'tests.example', "
        "number = 123, "
        "another-number = 0.5, "
        "expiry-days = 3, "
        "certificate = 'secrets/copper.key', "
        "multi-type = -1}"
    )
    dc = single_value_dataclass(TemplateConfig, optional=optional, string=string)
    assert parse_toml(dc, formatted).value == value


@dataclass
class NestedConfig:
    """This table is bound to a nested dataclass."""

    inner_int: int
    inner_str: str
    optional: str | None = None
    with_default: str = "n/a"


def _expected_formatting_of_nested_dataclass(
    name: str, *, inner_int: int = 0, inner_str: str = "???", optional_table: bool = False
) -> str:
    optional_table_str = "# Optional table.\n" if optional_table else ""
    return f"""
# This table is bound to a nested dataclass.
# This is the docstring for the nested field.
{optional_table_str}\
[{name}]

# Mandatory.
inner-int = {inner_int}

# Mandatory.
inner-str = '{inner_str}'

# Optional.
# optional = '???'

# Default:
# with-default = 'n/a'
""".strip()


def test_format_template_optional_nested() -> None:
    @dataclass
    class Config:
        nested: NestedConfig | None = None
        """This is the docstring for the nested field."""

    template = "\n".join(Binder(Config).format_toml_template())
    assert template == _expected_formatting_of_nested_dataclass("nested", optional_table=True)


def test_format_template_mapping_nested_class() -> None:
    @dataclass
    class Config:
        nested: dict[str, NestedConfig]
        """This is the docstring for the nested field."""

    template = "\n".join(Binder(Config).format_toml_template())
    assert template == _expected_formatting_of_nested_dataclass("nested.<name>")


def test_format_template_mapping_nested_value() -> None:
    @dataclass
    class Config:
        nested: dict[str, NestedConfig]
        """This is the docstring for the nested field."""

    config = Config(
        nested={
            "first": NestedConfig(inner_int=1, inner_str="one"),
            "second": NestedConfig(inner_int=2, inner_str="two"),
        }
    )
    template = "\n".join(Binder(config).format_toml_template())
    assert template == "\n".join(
        (
            _expected_formatting_of_nested_dataclass("nested.first", inner_int=1, inner_str="one"),
            "",
            _expected_formatting_of_nested_dataclass("nested.second", inner_int=2, inner_str="two"),
        )
    )


def test_format_template_mapping_untyped_class() -> None:
    @dataclass
    class Config:
        untyped: dict[str, Any] | None = None
        """This is the docstring for the untyped field."""

    template = "\n".join(Binder(Config).format_toml_template())
    assert template == (
        """
# This is the docstring for the untyped field.
# Optional table.
[untyped]
""".strip()
    )


def test_format_template_mapping_untyped_value() -> None:
    @dataclass
    class Config:
        untyped: dict[str, Any]
        """This is the docstring for the untyped field."""

    config = Config(untyped={"one": 1, "two": 2.0})
    template = "\n".join(Binder(config).format_toml_template())
    assert template == (
        """
# This is the docstring for the untyped field.
[untyped]
one = 1
two = 2.0
""".strip()
    )


def test_format_template_default_factory() -> None:
    """Fields with default factories also have defaults."""

    @dataclass
    class Config:
        words: list[str] = field(default_factory=list)
        numbers: Sequence[int] = field(default_factory=lambda: tuple(range(10)))

    class_template = "\n".join(Binder(Config).format_toml_template())
    assert class_template == (
        """
# Default:
# words = []

# Optional.
# numbers = []
""".strip()
    )

    instance_template = "\n".join(Binder(Config()).format_toml_template())
    assert instance_template == (
        """
# Default:
# words = []

# Optional.
numbers = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
""".strip()
    )


def test_format_template_sequence_default() -> None:
    """
    When formatting, a sequence value is considered equal to the default if it would produce identical TOML,
    even if the Python type is different.
    """

    @dataclass
    class Config:
        things: Sequence[str] = ("this", "that")

    config = Config(things=["this", "that"])

    template = "\n".join(Binder(config).format_toml_template())
    assert template == (
        """
# Default:
# things = ['this', 'that']
""".strip()
    )


def test_format_template_sequence_nested_class() -> None:
    @dataclass
    class Config:
        nested: list[NestedConfig]
        """This is the docstring for the nested field."""

    template = "\n".join(Binder(Config).format_toml_template())
    assert template == _expected_formatting_of_nested_dataclass("[nested]")


def test_format_template_sequence_nested_value() -> None:
    @dataclass
    class Config:
        nested: list[NestedConfig]
        """This is the docstring for the nested field."""

    config = Config(
        nested=[
            NestedConfig(inner_int=1, inner_str="one"),
            NestedConfig(inner_int=2, inner_str="two"),
        ]
    )
    template = "\n".join(Binder(config).format_toml_template())
    assert template == "\n".join(
        (
            _expected_formatting_of_nested_dataclass("[nested]", inner_int=1, inner_str="one"),
            "",
            _expected_formatting_of_nested_dataclass("[nested]", inner_int=2, inner_str="two"),
        )
    )


def test_format_template_sequence_untyped_class() -> None:
    @dataclass
    class Config:
        untyped: list[Any] | None = None
        """This is the docstring for the untyped field."""

    template = "\n".join(Binder(Config).format_toml_template())
    assert template == (
        """
# This is the docstring for the untyped field.
# Optional table.
[[untyped]]
""".strip()
    )


def test_format_template_sequence_untyped_value_table() -> None:
    """Untyped sequences are formatted as tables, if possible."""

    @dataclass
    class Config:
        untyped: list[Any]
        """This is the docstring for the untyped field."""

    config = Config([{"one": 1, "two": 2.0}, Config(["three", False])])
    template = "\n".join(Binder(config).format_toml_template())
    assert template == (
        """
# This is the docstring for the untyped field.
[[untyped]]
one = 1
two = 2.0

# This is the docstring for the untyped field.
[[untyped]]
untyped = ['three', false]
""".strip()
    )


def test_format_template_sequence_untyped_value_inline() -> None:
    """Untyped sequences with mixed content must be formatted inline."""

    @dataclass
    class Config:
        untyped: list[Any]
        """This is the docstring for the untyped field."""

    config = Config([1, 2.0, Config(["three", False])])
    template = "\n".join(Binder(config).format_toml_template())
    assert template == (
        """
# This is the docstring for the untyped field.
# Mandatory.
untyped = [1, 2.0, {untyped = ['three', false]}]
""".strip()
    )


@pytest.mark.parametrize(
    "field_type", (str, int, float, datetime, date, time, timedelta, list[str], dict[str, int], NestedConfig)
)
@pytest.mark.parametrize("optional", (True, False))
@pytest.mark.parametrize("string", (True, False))
def test_format_template_valid_value(*, field_type: type[Any], optional: bool, string: bool) -> None:
    """
    The template generated for the given field type is valid TOML and the value has the right type.

    Not all templates values are valid TOML, but the selected parameters are.
    """
    dc = single_value_dataclass(field_type, optional=optional, string=string)
    toml = "\n".join(Binder(dc).format_toml_template())
    print(field_type, "->", toml)  # noqa: T201
    parse_toml(dc, toml)


@dataclass
class MiddleConfig:
    """This docstring will remain invisible, as its table is empty."""

    deepest: NestedConfig | None = None


@dataclass(kw_only=True)
class PopulatedConfig:
    source_database_connection_url: str
    """Database to read the data from."""

    destination_database_connection_url: str = "sqlite://"
    """Database to write the data to."""

    middle: MiddleConfig
    webhook_urls: tuple[str, ...] = ()


def test_format_template_populated() -> None:
    config = PopulatedConfig(
        source_database_connection_url="postgresql://<username>:<password>@<hostname>/<database name>",
        destination_database_connection_url="sqlite://",
        middle=MiddleConfig(NestedConfig(5, "foo")),
        webhook_urls=("https://host1/refresh", "https://host2/refresh"),
    )
    template = "\n".join(Binder(config).format_toml_template())
    assert template == (
        """
# Database to read the data from.
# Mandatory.
source-database-connection-url = 'postgresql://<username>:<password>@<hostname>/<database name>'

# Database to write the data to.
# Default:
# destination-database-connection-url = 'sqlite://'

# Default:
# webhook-urls = []
webhook-urls = ['https://host1/refresh', 'https://host2/refresh']

# This table is bound to a nested dataclass.
# Optional table.
[middle.deepest]

# Mandatory.
inner-int = 5

# Mandatory.
inner-str = 'foo'

# Optional.
# optional = '???'

# Default:
# with-default = 'n/a'
""".strip()
    )


def test_format_populated() -> None:
    config = PopulatedConfig(
        source_database_connection_url="postgresql://<username>:<password>@<hostname>/<database name>",
        destination_database_connection_url="sqlite://",
        middle=MiddleConfig(NestedConfig(5, "foo")),
        webhook_urls=("https://host1/refresh", "https://host2/refresh"),
    )
    template = "\n".join(Binder(config).format_toml())
    assert template == (
        """
# Database to read the data from.
source-database-connection-url = 'postgresql://<username>:<password>@<hostname>/<database name>'
webhook-urls = ['https://host1/refresh', 'https://host2/refresh']

# This table is bound to a nested dataclass.
[middle.deepest]
inner-int = 5
inner-str = 'foo'
""".strip()
    )


def test_format_optional_tables() -> None:
    """Optional tables must be omitted during non-template formatting."""

    @dataclass
    class Config:
        untyped_mandatory: dict[str, Any]
        """This is the docstring for the mandatory untyped field."""

        untyped_optional: dict[str, Any] = field(default_factory=dict)
        """This is the docstring for the optional untyped field."""

        nested: NestedConfig | None = None
        """Optional nested dataclass."""

    expected = """
# This is the docstring for the mandatory untyped field.
[untyped-mandatory]
""".strip()

    assert "\n".join(Binder(Config).format_toml()) == expected

    config = Config(untyped_mandatory={})
    assert "\n".join(Binder(config).format_toml()) == expected


@dataclass
class BinaryTree:
    value: int
    left: BinaryTree | None = None
    right: BinaryTree | None = None


def test_format_template_depth_first() -> None:
    """Tables are formatted depth-first: children before siblings."""

    tree = BinaryTree(
        value=1,
        left=BinaryTree(value=2, left=BinaryTree(value=3), right=BinaryTree(value=4)),
        right=BinaryTree(value=5, left=BinaryTree(value=6), right=BinaryTree(value=7)),
    )
    template = "\n".join(Binder(tree).format_toml_template())
    assert template == (
        """
# Mandatory.
value = 1

# Optional table.
[left]

# Mandatory.
value = 2

# Optional table.
[left.left]

# Mandatory.
value = 3

# Optional table.
[left.right]

# Mandatory.
value = 4

# Optional table.
[right]

# Mandatory.
value = 5

# Optional table.
[right.left]

# Mandatory.
value = 6

# Optional table.
[right.right]

# Mandatory.
value = 7
""".strip()
    )


@pytest.fixture()
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
    C: type[Any] = defs["C"]  # noqa: N806
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
    template = "\n".join(Binder(sourceless_class).format_toml_template())
    assert template == (
        """
# Mandatory.
value = 0
""".strip()
    )
