from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum, IntEnum
from io import BytesIO
from pathlib import Path
from types import ModuleType
from typing import Any, BinaryIO, Generic, TypeVar

import pytest

from dataclass_binder import Binder
from dataclass_binder._impl import _find_object_by_name

from . import example

T = TypeVar("T")


@contextmanager
def stream_text(text: str) -> Iterator[BinaryIO]:
    stream = BytesIO(text.encode())
    try:
        yield stream
    finally:
        stream.close()


def test_find_object_by_name_module() -> None:
    """Modules can be found by their fully qualified name."""

    assert _find_object_by_name("tests.example", "CONTEXT") is example


def test_find_object_by_name_member() -> None:
    """Module members can be found by their fully qualified name."""

    assert _find_object_by_name("tests.example.TEMPLATE", "CONTEXT") is example.TEMPLATE


class Outer:
    class Inner:
        pass


def test_find_object_by_name_nested_class() -> None:
    """Module members inside other module members can be found."""

    assert _find_object_by_name("tests.test_parsing.Outer.Inner", "CONTEXT") is Outer.Inner


def test_find_object_by_name_missing() -> None:
    """ValueError is raised if the given name does not exist."""

    with pytest.raises(
        ValueError,
        match=r"^Python object for 'Config.bad_module' not found: no top-level module named 'no-such-module'$",
    ):
        _find_object_by_name("no-such-module", "Config.bad_module")

    with pytest.raises(
        ValueError,
        match=r"^Python object for 'Config.bad_class' not found: "
        r"name 'no-such-name' does not exist in 'dataclass_binder'$",
    ):
        _find_object_by_name("dataclass_binder.no-such-name", "Config.bad_class")


@dataclass(frozen=True)
class Config:
    rest_api_port: int
    feed_job_prefixes: Iterable[str] = ()
    import_max_nr_hours: int = 24


@dataclass(frozen=True)
class OptionalConfig:
    trend_identifier: str | None = None


def test_bind_simple() -> None:
    """Values from a TOML stream are parsed into the Config object."""
    with stream_text(
        """
        rest-api-port = 6000
        feed-job-prefixes = ["MIX1:", "MIX2:", "MIX3:"]
        """
    ) as stream:
        config = Binder(Config).parse_toml(stream)

    assert config.rest_api_port == 6000
    assert config.feed_job_prefixes == ("MIX1:", "MIX2:", "MIX3:")
    assert config.import_max_nr_hours == 24


def test_binder_specialization() -> None:
    """The deprecated `Binder[DT]` syntax is still supported."""
    with stream_text(
        """
        rest-api-port = 6000
        feed-job-prefixes = ["MIX1:", "MIX2:", "MIX3:"]
        """
    ) as stream:
        config = Binder[Config].parse_toml(stream)

    assert config.rest_api_port == 6000
    assert config.feed_job_prefixes == ("MIX1:", "MIX2:", "MIX3:")
    assert config.import_max_nr_hours == 24


def test_bind_file(tmp_path: Path) -> None:
    """Values from a TOML file are parsed into the Config object."""
    file = tmp_path / "config.toml"
    with file.open("w") as out:
        print(
            """
            rest-api-port = 6000
            feed-job-prefixes = ["MIX1:", "MIX2:", "MIX3:"]
            """,
            file=out,
        )
    config = Binder(Config).parse_toml(file)

    assert config.rest_api_port == 6000
    assert config.feed_job_prefixes == ("MIX1:", "MIX2:", "MIX3:")
    assert config.import_max_nr_hours == 24


def test_bind_inheritance() -> None:
    """A dataclass inheriting from another dataclass accepts fields from both the base and the subclass."""

    @dataclass(frozen=True)
    class ExtendedConfig(example.Config):
        """Inheriting from a class in another module complicates the annotation evaluation."""

        dry_run: bool = False

    with stream_text(
        """
        database-url = "postgresql://smaug:gold@mountain/hoard"
        dry-run = true
        """
    ) as stream:
        config = Binder(ExtendedConfig).parse_toml(stream)

    assert config.database_url == "postgresql://smaug:gold@mountain/hoard"
    assert config.port == 12345
    assert config.dry_run is True


def test_bind_immutable() -> None:
    """When using a frozen dataclass and abstract annotatoins, the Config object is immutable."""
    with stream_text(
        """
        rest-api-port = 6000
        feed-job-prefixes = ["MIX1:", "MIX2:", "MIX3:"]
        """
    ) as stream:
        config = Binder(Config).parse_toml(stream)

    with pytest.raises(FrozenInstanceError):
        config.rest_api_port = 1234  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        config.new_field = True  # type: ignore[attr-defined]

    assert isinstance(config.feed_job_prefixes, tuple)


def test_bind_mutable() -> None:
    """When using `dict` and `list` as annotations, the Config object is mutable."""

    @dataclass
    class MutableConfig:
        tags: list[str]
        limits: dict[str, int]
        verbose: bool = False

    with stream_text(
        """
        tags = ["production", "development"]
        limits = {ram-gb = 1, disk-gb = 100}
        """
    ) as stream:
        config = Binder(MutableConfig).parse_toml(stream)

    config.verbose = True
    assert config.verbose is True

    config.tags.append("staging")
    assert config.tags == ["production", "development", "staging"]

    config.limits["processes"] = 4
    assert config.limits == {"ram-gb": 1, "disk-gb": 100, "processes": 4}


def test_bind_optional() -> None:
    """Dataclass fields can have a default value of None."""

    with stream_text("") as stream:
        config_absent = Binder(OptionalConfig).parse_toml(stream)
    assert config_absent.trend_identifier is None

    with stream_text("trend-identifier = 'fly'") as stream:
        config_present = Binder(OptionalConfig).parse_toml(stream)
    assert config_present.trend_identifier == "fly"


def test_specialize_optional_default() -> None:
    """Optional fields must have a default of None, as we can't express None in TOML."""

    @dataclass(frozen=True)
    class BadConfig1:
        name: str | None

    with pytest.raises(TypeError, match=r"^Default for optional field 'BadConfig1.name' is not None"):
        Binder(BadConfig1)

    @dataclass(frozen=True)
    class BadConfig2:
        name: str | None = "Bob"

    with pytest.raises(TypeError, match=r"^Default for optional field 'BadConfig2.name' is not None"):
        Binder(BadConfig2)


def test_bind_union() -> None:
    """Fields with a union type accept all of the options and reject values of other types."""

    @dataclass(frozen=True)
    class UnionConfig:
        favorite: int | str

    with stream_text("favorite = 'fly'") as stream:
        config_str = Binder(UnionConfig).parse_toml(stream)
    with stream_text("favorite = 3") as stream:
        config_int = Binder(UnionConfig).parse_toml(stream)

    assert config_str.favorite == "fly"
    assert config_int.favorite == 3

    with (
        stream_text("favorite = false") as stream,
        pytest.raises(TypeError, match=r"^Value for 'UnionConfig.favorite' has type 'bool', expected 'int | str$"),
    ):
        Binder(UnionConfig).parse_toml(stream)


def test_bind_key_underscore() -> None:
    """ValueError is raised when a TOML key contains an underscore."""

    with (
        stream_text("trend_identifier = 'fly'") as stream,
        pytest.raises(ValueError, match=r"^Underscore found in TOML key 'trend_identifier'$"),
    ):
        Binder(OptionalConfig).parse_toml(stream)


def test_bind_key_does_not_exist() -> None:
    """ValueError is raised when a TOML key does not match any dataclass field."""

    with (
        stream_text("nosuchfield = true") as stream,
        pytest.raises(ValueError, match=r"^Field 'OptionalConfig.nosuchfield' does not exist$"),
    ):
        Binder(OptionalConfig).parse_toml(stream)

    with (
        stream_text("no-such-field = true") as stream,
        pytest.raises(ValueError, match=r"^Field 'OptionalConfig.no_such_field' does not exist$"),
    ):
        Binder(OptionalConfig).parse_toml(stream)


def test_specialize_nontype_annotation() -> None:
    """Type annotations must be concrete types."""

    @dataclass(frozen=True)
    class BadConfig1:
        thing: 0  # type: ignore[valid-type]

    with pytest.raises(TypeError, match=r"^Annotation for field 'BadConfig1.thing' is not a type$"):
        Binder(BadConfig1)

    @dataclass(frozen=True)
    class BadConfig2:
        thing: T  # type: ignore[valid-type]

    with pytest.raises(TypeError, match=r"^Annotation for field 'BadConfig2.thing' is not a type$"):
        Binder(BadConfig2)

    @dataclass(frozen=True)
    class BadConfig3:
        things: Iterable[T]  # type: ignore[valid-type]

    with pytest.raises(TypeError, match=r"^Annotation for field 'BadConfig3.things\[\]' is not a type$"):
        Binder(BadConfig3)


def test_specialize_missing_typeargs() -> None:
    """Sequence and mapping types in annotations must include type arguments."""

    @dataclass(frozen=True)
    class BadConfig1:
        things: tuple

    with pytest.raises(TypeError, match=r"^Field 'BadConfig1.things' needs type argument\(s\)$"):
        Binder(BadConfig1)

    @dataclass(frozen=True)
    class BadConfig2:
        things: Sequence

    with pytest.raises(TypeError, match=r"^Field 'BadConfig2.things' needs type argument\(s\)$"):
        Binder(BadConfig2)

    @dataclass(frozen=True)
    class BadConfig3:
        things: Mapping

    with pytest.raises(TypeError, match=r"^Field 'BadConfig3.things' needs type argument\(s\)$"):
        Binder(BadConfig3)


def test_specialize_mapping_num_type_args() -> None:
    """TOML table mapping must have exactly two type arguments: key and value type."""

    @dataclass(frozen=True)
    class BadConfig:
        things: Mapping[str]  # type: ignore[type-arg]

    with pytest.raises(TypeError, match=r"^Mapping 'BadConfig.things' must have two type arguments$"):
        Binder(BadConfig)


def test_specialize_mapping_bad_key_type() -> None:
    """TOML table mapping must use strings as keys."""

    @dataclass(frozen=True)
    class BadConfig:
        magic_numbers: Mapping[int, int]

    with pytest.raises(TypeError, match=r"^Mapping 'BadConfig.magic_numbers' has key type 'int', expected 'str'$"):
        Binder(BadConfig)


def test_specialize_bad_type_args() -> None:
    """Annotations of the form type[...] must have exactly one argument, which must be a type."""

    @dataclass(frozen=True)
    class BadConfig1:
        things: type[int, str]  # type: ignore[valid-type]

    with pytest.raises(
        TypeError, match=r"^type\[...\] annotation for 'BadConfig1.things' must have exactly one type argument$"
    ):
        Binder(BadConfig1)

    @dataclass(frozen=True)
    class BadConfig2:
        things: type[0]  # type: ignore[valid-type]

    with pytest.raises(
        TypeError, match=r"^type\[...\] annotation for 'BadConfig2.things' must have a type as its argument$"
    ):
        Binder(BadConfig2)


def test_bind_sequence_nonarray() -> None:
    """TypeError is raised when the value for a sequence field is not an array."""

    with (
        stream_text(
            """
            rest-api-port = 6000
            feed-job-prefixes = "MIX"
            """
        ) as stream,
        pytest.raises(TypeError, match=r"^Value for 'Config.feed_job_prefixes' has type 'str', expected array$"),
    ):
        Binder(Config).parse_toml(stream)


def test_bind_int_nonint() -> None:
    """TypeError is raised when the TOML value type does not match the type annotation in the dataclass."""

    @dataclass(frozen=True)
    class MiniConfig:
        rest_api_port: int

    with (
        stream_text(
            """
            rest-api-port = "6000"
            """
        ) as stream,
        pytest.raises(TypeError, match=r"^Value for 'MiniConfig.rest_api_port' has type 'str', expected 'int'$"),
    ):
        Binder(MiniConfig).parse_toml(stream)

    with (
        stream_text(
            """
            rest-api-port = [6000]
            """
        ) as stream,
        pytest.raises(TypeError, match=r"^Value for 'MiniConfig.rest_api_port' has type 'list', expected 'int'$"),
    ):
        Binder(MiniConfig).parse_toml(stream)

    # The 'bool' type is a subtype of 'int', but in configurations we consider them incompatible.
    with (
        stream_text(
            """
            rest-api-port = true
            """
        ) as stream,
        pytest.raises(TypeError, match=r"^Value for 'MiniConfig.rest_api_port' has type 'bool', expected 'int'$"),
    ):
        Binder(MiniConfig).parse_toml(stream)


def test_bind_sequence_homogenous_tuple_syntax() -> None:
    """The tuple[T, ...] syntax is supported."""

    @dataclass(frozen=True)
    class AnimalConfig:
        sounds: tuple[str, ...]

    with stream_text(
        """
        sounds = ["bah", "moo", "meow"]
        """
    ) as stream:
        config = Binder(AnimalConfig).parse_toml(stream)

    assert config.sounds == ("bah", "moo", "meow")

    with (
        stream_text(
            """
            sounds = ["bark", false]
            """
        ) as stream,
        pytest.raises(TypeError, match=r"^Value for 'AnimalConfig.sounds\[1\]' has type 'bool', expected 'str'$"),
    ):
        Binder(AnimalConfig).parse_toml(stream)


def test_bind_sequence_homogenous_badelement() -> None:
    """
    TypeError is raised when the TOML value type of a sequence element does not match the homogenous sequence
    type annotation in the dataclass.
    """

    with (
        stream_text(
            """
            rest-api-port = 6000
            feed-job-prefixes = ["MIX1:", 2, "MIX3:"]
            """
        ) as stream,
        pytest.raises(TypeError, match=r"^Value for 'Config.feed_job_prefixes\[1\]' has type 'int', expected 'str'$"),
    ):
        Binder(Config).parse_toml(stream)


def test_bind_sequence_heterogenous_badelement() -> None:
    """
    TypeError is raised when the TOML value type of a sequence element does not match the heterogenous sequence
    type annotation in the dataclass.
    """

    @dataclass(frozen=True)
    class MiniConfig:
        params: tuple[str, int, bool, str]

    with (
        stream_text(
            """
            params = ["abc", 2, true, false]
            """
        ) as stream,
        pytest.raises(TypeError, match=r"^Value for 'MiniConfig.params\[3\]' has type 'bool', expected 'str'$"),
    ):
        Binder(MiniConfig).parse_toml(stream)


def test_bind_sequence_heterogenous_badsize() -> None:
    """
    TypeError is raised when the TOML array matching a heterogenous sequence type annotation in the dataclass
    does not have the right number of elements.
    """

    @dataclass(frozen=True)
    class MiniConfig:
        params: tuple[str, int, bool, str]

    with (
        stream_text(
            """
            params = ["abc", 2, true]
            """
        ) as stream,
        pytest.raises(TypeError, match=r"^Expected 4 elements for 'MiniConfig.params', got 3$"),
    ):
        Binder(MiniConfig).parse_toml(stream)


def test_bind_nested_tuple() -> None:
    """Tuples can be nested within other tuples."""

    @dataclass(frozen=True)
    class MiniConfig:
        nested: tuple[tuple[str, int], tuple[bool, str]]

    with stream_text(
        """
        nested = [["abc", 2], [true, "def"]]
        """
    ) as stream:
        config = Binder(MiniConfig).parse_toml(stream)

    assert config.nested == (("abc", 2), (True, "def"))


@dataclass(frozen=True)
class MappingConfig:
    magic_numbers: Mapping[str, int]


def test_bind_mapping_ok() -> None:
    """TOML tables are bound to a mapping."""
    with stream_text(
        """
        magic-numbers = {the-answer = 42, "the-beast" = 666, haxor = 1337}
        """
    ) as stream:
        config = Binder(MappingConfig).parse_toml(stream)

    assert dict(config.magic_numbers) == {"the-answer": 42, "the-beast": 666, "haxor": 1337}


def test_bind_mapping_access() -> None:
    """Bound Mappings support read-only access."""
    with stream_text(
        """
        magic-numbers = {the-answer = 42, "the-beast" = 666, haxor = 1337}
        """
    ) as stream:
        config = Binder(MappingConfig).parse_toml(stream)

    assert isinstance(config.magic_numbers, Mapping)
    assert config.magic_numbers.get("the-answer") == 42
    assert config.magic_numbers["the-beast"] == 666
    assert config.magic_numbers.get("missingno") is None

    with pytest.raises(TypeError):
        config.magic_numbers["suitcase"] = 12345  # type: ignore[index]


def test_bind_mapping_nontable() -> None:
    """TypeError is raised when the value for a mapping field is not a table."""

    with (
        stream_text(
            """
            magic-numbers = 83
            """
        ) as stream,
        pytest.raises(TypeError, match=r"^Value for 'MappingConfig.magic_numbers' has type 'int', expected table$"),
    ):
        Binder(MappingConfig).parse_toml(stream)


def test_bind_mapping_badvalue() -> None:
    """TypeError is raised when the value inside a mapping does not match the annotation."""

    with (
        stream_text(
            """
            magic-numbers = {the-answer = true}
            """
        ) as stream,
        pytest.raises(
            TypeError,
            match=r"^Value for 'MappingConfig.magic_numbers\[\"the-answer\"\]' has type 'bool', expected 'int'$",
        ),
    ):
        Binder(MappingConfig).parse_toml(stream)


def test_bind_mapping_any() -> None:
    """A field annotated with `Any` accepts any parsed TOML data."""

    @dataclass
    class Config:
        options: dict[str, Any]

    with stream_text(
        """
        options = {the-answer = 42, 'the-question' = false, alphabet = ["a", "b", "c"]}
        """
    ) as stream:
        config = Binder(Config).parse_toml(stream)

    assert config.options == {"the-answer": 42, "the-question": False, "alphabet": ["a", "b", "c"]}


def test_bind_datetime() -> None:
    """Dates and times are parsed to classes from the `datetime` module."""

    @dataclass(frozen=True)
    class DateTimeConfig:
        offset_date_time: datetime
        local_date_time: datetime
        local_date: date
        local_time: time

    with stream_text(
        """
        offset-date-time = 1979-05-27 00:32:00-07:00
        local-date-time = 1979-05-27 07:32:00
        local-date = 1979-05-27
        local-time = 07:32:00
        """
    ) as stream:
        config = Binder(DateTimeConfig).parse_toml(stream)

    assert config.offset_date_time.isoformat() == "1979-05-27T00:32:00-07:00"
    assert config.local_date_time.isoformat() == "1979-05-27T07:32:00"
    assert config.local_date.isoformat() == "1979-05-27"
    assert config.local_time.isoformat() == "07:32:00"


@dataclass(frozen=True)
class TimeDeltaConfig:
    duration: timedelta


def test_bind_timedelta_direct() -> None:
    """Duration is parsed as a local time and converted to `datetime.timedelta`."""

    with stream_text(
        """
        duration = 12:34:56.789
        """
    ) as stream:
        config = Binder(TimeDeltaConfig).parse_toml(stream)

    assert config.duration == timedelta(hours=12, minutes=34, seconds=56, milliseconds=789)

    with (
        stream_text(
            """
            duration = false
            """
        ) as stream,
        pytest.raises(TypeError, match="^Value for 'TimeDeltaConfig.duration' has type 'bool', expected time$"),
    ):
        Binder(TimeDeltaConfig).parse_toml(stream)


def test_bind_timedelta_suffix() -> None:
    """The key suffix indicates the unit for the duration."""

    with stream_text(
        """
        duration-days = 5
        """
    ) as stream:
        config = Binder(TimeDeltaConfig).parse_toml(stream)

    assert config.duration == timedelta(days=5)

    with stream_text(
        """
        duration-hours = 7.5
        """
    ) as stream:
        config = Binder(TimeDeltaConfig).parse_toml(stream)

    assert config.duration == timedelta(hours=7.5)

    with (
        stream_text(
            """
            duration-weeks = false
            """
        ) as stream,
        pytest.raises(
            TypeError,
            match="^Value for 'TimeDeltaConfig.duration' with suffix 'weeks' has type 'bool', expected number$",
        ),
    ):
        Binder(TimeDeltaConfig).parse_toml(stream)


def test_bind_unknown_suffix() -> None:
    """Non-existing suffixes are rejected."""

    # Type 'int' does support any suffixes.
    with (
        stream_text(
            """
            rest-api-port-thingy = true
            """
        ) as stream,
        pytest.raises(
            ValueError, match=r"^Field 'Config.rest_api_port' has type 'int', which does not support suffix 'thingy'$"
        ),
    ):
        Binder(Config).parse_toml(stream)

    # Type 'timedelta' does support suffixes, but not this one.
    with (
        stream_text(
            """
            duration-centuries = true
            """
        ) as stream,
        pytest.raises(
            ValueError,
            match=r"^Field 'TimeDeltaConfig.duration' has type 'timedelta', which does not support suffix 'centuries'$",
        ),
    ):
        Binder(TimeDeltaConfig).parse_toml(stream)


def test_bind_path_nonstring() -> None:
    """TypeError is raised when a path value is not a TOML string."""

    @dataclass
    class Config:
        path: Path

    with (
        stream_text("path = 8.3") as stream,
        pytest.raises(TypeError, match=r"^Expected TOML string for path 'Config.path', got 'float'$"),
    ):
        Binder(Config).parse_toml(stream)


@dataclass(frozen=True)
class ClassRefConfig:
    first_class: type
    second_class: type[Any]


def test_bind_classref_ok() -> None:
    """A Python class can be specified using its fully qualified name."""

    with stream_text(
        """
        first-class = 'logging.FileHandler'
        second-class = 'tests.example.Config'
        """
    ) as stream:
        config = Binder(ClassRefConfig).parse_toml(stream)

    assert config.first_class is logging.FileHandler
    assert config.second_class is example.Config


def test_bind_classref_nonstring() -> None:
    """TypeError is raised when a reference to a Python class is not a TOML string."""

    with (
        stream_text(
            """
            first-class = 'logging.FileHandler'
            second-class = 123
            """
        ) as stream,
        pytest.raises(
            TypeError, match=r"^Expected TOML string for Python reference 'ClassRefConfig.second_class', got 'int'$"
        ),
    ):
        Binder(ClassRefConfig).parse_toml(stream)


def test_bind_classref_nonclass() -> None:
    """TypeError is raised if a class reference doesn't resolve to a class."""

    with (
        stream_text(
            """
            first-class = 'logging.FileHandler'
            second-class = 'tests.example.Config.port'
            """
        ) as stream,
        pytest.raises(TypeError, match=r"^Value for 'ClassRefConfig.second_class' has type 'int', expected class$"),
    ):
        Binder(ClassRefConfig).parse_toml(stream)


class DummyBase:
    pass


class DummySub(DummyBase):
    pass


def test_bind_classref_notsubclass() -> None:
    """TypeError is raised if a class reference doesn't resolve to an expected class."""

    @dataclass(frozen=True)
    class SubclassConfig:
        base: type[DummyBase]
        sub: type[DummySub]

    with (
        stream_text(
            """
            base = 'tests.test_parsing.DummySub'
            sub = 'tests.test_parsing.DummyBase'
            """
        ) as stream,
        pytest.raises(
            TypeError, match=r"^Resolved 'SubclassConfig.sub' to class 'DummyBase', expected subclass of 'DummySub'$"
        ),
    ):
        Binder(SubclassConfig).parse_toml(stream)


def test_bind_classref_union_in_sequence() -> None:
    """Annotations of the form type[A | B] are suppported, also inside for example sequences."""

    @dataclass(frozen=True)
    class MultiClassConfig:
        models: Sequence[type[logging.Handler | logging.Formatter]]

    with stream_text(
        """
        models = ['logging.FileHandler', 'logging.Formatter']
        """
    ) as stream:
        config = Binder(MultiClassConfig).parse_toml(stream)

    assert config.models == (logging.FileHandler, logging.Formatter)

    with (
        stream_text(
            """
            models = ['logging.FileHandler', 'logging.Formatter', 12:34:56]
            """
        ) as stream,
        pytest.raises(
            TypeError,
            match=r"Value for 'MultiClassConfig.models\[2\]' has type 'time', "
            r"expected 'type\[logging.Handler\] | type\[logging\.Formatter\]'$",
        ),
    ):
        Binder(MultiClassConfig).parse_toml(stream)


@dataclass(frozen=True)
class ModuleConfig:
    plugin_module: ModuleType


def test_bind_module_ok() -> None:
    """A Python module can be specified using its fully qualified name."""

    with stream_text("plugin-module = 'tests.example'") as stream:
        config = Binder(ModuleConfig).parse_toml(stream)

    assert config.plugin_module is example


def test_bind_module_nonstring() -> None:
    """TypeError is raised when a reference to a Python class is not a TOML string."""

    with (
        stream_text("plugin-module = 123") as stream,
        pytest.raises(
            TypeError, match=r"^Expected TOML string for Python reference 'ModuleConfig.plugin_module', got 'int'$"
        ),
    ):
        Binder(ModuleConfig).parse_toml(stream)


def test_bind_module_nonmodule() -> None:
    """TypeError is raised if a module reference doesn't resolve to a module."""

    with (
        stream_text("plugin-module = 'tests.example.TEMPLATE'") as stream,
        pytest.raises(TypeError, match=r"^Value for 'ModuleConfig.plugin_module' has type 'Config', expected module$"),
    ):
        Binder(ModuleConfig).parse_toml(stream)


def test_bind_dataclass_as_field() -> None:
    """A dataclass can be used as a field inside another dataclass."""

    @dataclass(frozen=True)
    class OuterConfig:
        trend: OptionalConfig

    with stream_text(
        """
        trend = {trend-identifier = "uprising risk"}
        """
    ) as stream:
        config = Binder(OuterConfig).parse_toml(stream)

    assert config.trend.trend_identifier == "uprising risk"

    with (
        stream_text(
            """
            trend = {trend-identifier = 1}
            """
        ) as stream,
        pytest.raises(
            TypeError, match=r"^Value for 'OuterConfig.trend.trend_identifier' has type 'int', expected 'str'$"
        ),
    ):
        Binder(OuterConfig).parse_toml(stream)

    with (
        stream_text(
            """
            trend = false
            """
        ) as stream,
        pytest.raises(TypeError, match=r"^Value for 'OuterConfig.trend' has type 'bool', expected table$"),
    ):
        Binder(OuterConfig).parse_toml(stream)


@dataclass(frozen=True)
class GenericConfig(Generic[T]):
    value: T


def test_bind_dataclass_specialization() -> None:
    """An unknown generic class cannot be used as a field inside another dataclass."""

    @dataclass(frozen=True)
    class BadConfig:
        thing: GenericConfig

    with pytest.raises(TypeError, match=r"^Field 'BadConfig.thing' needs type argument\(s\)$"):
        Binder(BadConfig)

    @dataclass(frozen=True)
    class OuterConfig:
        thing: GenericConfig[str]

    # TODO: It would be nice to support this in the future.
    with pytest.raises(TypeError, match=r"^Field 'OuterConfig.thing' has unsupported generic type 'GenericConfig'$"):
        Binder(OuterConfig)


def test_bind_dataclass_in_sequence() -> None:
    """A dataclasses can be used as a value type in a sequence."""

    @dataclass(frozen=True)
    class OuterConfig:
        trends: Sequence[OptionalConfig]

    with stream_text(
        """
        [[trends]]
        trend-identifier = "uprising risk"

        [[trends]]
        trend-identifier = "uprising attempts"
        """
    ) as stream:
        config = Binder(OuterConfig).parse_toml(stream)

    assert len(config.trends) == 2
    assert config.trends[0].trend_identifier == "uprising risk"
    assert config.trends[1].trend_identifier == "uprising attempts"


def test_specialize_annotation_nested_scope() -> None:
    """
    Handle an annotation using a name from a nested scope gracefully.

    Python does not record nested scopes for class definitions.
    This means we have no way of resolving names from nested scopes used in annotations.
    All we can do is report the problem field.
    """

    @dataclass
    class Hidden:
        pass

    @dataclass
    class Config:
        hidden: Hidden

    with pytest.raises(TypeError, match=r"^Failed to parse annotation of field 'Config\.hidden': "):
        Binder(Config)


def test_specialize_excluded_from_init() -> None:
    """Fields with `init=False` are ignored at specialization."""

    class CustomType:
        pass

    @dataclass
    class Config:
        unsupported: CustomType = field(init=False)

    Binder(Config)


def test_bind_excluded_from_init() -> None:
    """Fields with `init=False` are ignored during binding."""

    @dataclass(frozen=True)
    class SumConfig:
        values: Sequence[int] = ()
        total: int = field(init=False)

        def __post_init__(self) -> None:
            super().__setattr__("total", sum(self.values))

    with stream_text("values = [1, 2, 3, 4]") as stream:
        config = Binder(SumConfig).parse_toml(stream)

    assert config.total == 10

    with (
        stream_text("total = 9001") as stream,
        # TODO: Refine error message: the field does exist, but it's excluded.
        pytest.raises(ValueError, match=r"^Field 'SumConfig\.total' does not exist$"),
    ):
        Binder(SumConfig).parse_toml(stream)


@dataclass
class Nested:
    value: str


@dataclass(kw_only=True)
class TopLevel:
    priority: int
    flag: bool = False
    nested1: Nested
    nested2: Nested


def test_bind_merge() -> None:
    """
    A binder constructed from a dataclass instance uses that instance as defaults,
    for the top level and any nested dataclasses (unless they are part of collections).
    """

    with stream_text(
        """
        priority = 99

        [nested1]
        value = "sun"

        [nested2]
        value = "moon"
        """
    ) as stream:
        system_config = Binder(TopLevel).parse_toml(stream)

    with stream_text(
        """
        flag = true

        [nested2]
        value = "cheese"
        """
    ) as stream:
        merged_config = Binder(system_config).parse_toml(stream)

    assert merged_config.priority == 99
    assert merged_config.flag is True
    assert merged_config.nested1.value == "sun"
    assert merged_config.nested2.value == "cheese"


class Color(Enum):
    RED = "#FF0000"
    GREEN = "#00FF00"
    BLUE = "#0000FF"


class Number(IntEnum):
    ONE = 1
    TWO = 2
    THREE = 3


@dataclass
class EnumEntry:
    name: str
    color: Color
    number: Number


def test_enums() -> None:
    @dataclass
    class Config:
        best_colors: list[Color]
        best_numbers: list[Number]
        entries: list[EnumEntry]

    with stream_text(
        """
        best-colors = ["red", "green", "blue"]
        best-numbers = [1, 2, 3]

        [[entries]]
        name = "Entry 1"
        color = "blue"
        number = 2

        [[entries]]
        name = "Entry 2"
        color = "red"
        number = 1
        """
    ) as stream:
        config = Binder(Config).parse_toml(stream)

    assert len(config.best_colors) == 3
    assert len(config.best_numbers) == 3
    assert config.best_colors.index(Color.RED) == 0
    assert config.best_colors.index(Color.GREEN) == 1
    assert config.best_colors.index(Color.BLUE) == 2
    assert all(num in config.best_numbers for num in Number)
    assert len(config.entries) == 2
    assert config.entries[0].color is Color.BLUE
    assert config.entries[0].number is Number.TWO
    assert config.entries[1].color is Color.RED
    assert config.entries[1].number is Number.ONE


def test_enum_with_invalid_value() -> None:
    @dataclass
    class UserFavorites:
        favorite_number: Number
        favorite_color: Color

    with stream_text(
        """
        favorite-number = "one"
        favorite-color = "red"
        """
    ) as stream, pytest.raises(ValueError):  # noqa: PT011
        Binder(UserFavorites).parse_toml(stream)


def test_enum_keys_being_case_insensitive() -> None:
    @dataclass
    class Theme:
        primary: Color
        secondary: Color
        accent: Color

    with stream_text(
        """
        primary = "RED"
        secondary = "green"
        accent = "blUE"
        """
    ) as stream:
        theme = Binder(Theme).parse_toml(stream)

    assert theme.primary is Color.RED
    assert theme.secondary is Color.GREEN
    assert theme.accent is Color.BLUE


def test_key_based_enum_while_using_value_ident() -> None:
    @dataclass
    class UserColorPreference:
        primary: Color
        secondary: Color

    with stream_text(
        """
        primary = "#FF0000"
        seconadry = "blue"
        """
    ) as stream, pytest.raises(TypeError):
        Binder(UserColorPreference).parse_toml(stream)
