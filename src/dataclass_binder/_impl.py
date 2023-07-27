"""Implementation module."""

from __future__ import annotations

import ast
import operator
import re
import sys
from collections.abc import (
    Callable,
    Collection,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
    MutableSequence,
    Sequence,
    Set,
)
from dataclasses import MISSING, Field, asdict, dataclass, fields, is_dataclass, replace
from datetime import date, datetime, time, timedelta
from functools import reduce
from importlib import import_module
from inspect import cleandoc, get_annotations, getmodule, getsource, isabstract
from pathlib import Path
from textwrap import dedent
from types import MappingProxyType, ModuleType, NoneType, UnionType
from typing import TYPE_CHECKING, Any, BinaryIO, ClassVar, Generic, TypeVar, Union, cast, get_args, get_origin, overload
from weakref import WeakKeyDictionary

if sys.version_info < (3, 11):
    import tomli as tomllib  # pragma: no cover
else:
    import tomllib  # pragma: no cover


def _collect_type(field_type: type, context: str) -> type | Binder[Any]:
    """
    Verify and streamline a type annotation.

    Streamlining means that when there are multiple ways to express the same typing,
    we pick one and convert the alternative forms to that.

    Raises TypeError if the annotation is not supported.
    """
    origin = get_origin(field_type)
    if origin is None:
        if field_type is Any:
            return object
        elif not isinstance(field_type, type):
            raise TypeError(f"Annotation for field '{context}' is not a type")
        elif issubclass(field_type, str | int | float | date | time | timedelta | ModuleType):
            return field_type
        elif field_type is type:
            # https://github.com/python/mypy/issues/13026
            return cast(type, type[Any])  # type: ignore[index]
        elif hasattr(field_type, "__class_getitem__"):
            raise TypeError(f"Field '{context}' needs type argument(s)")
        else:
            # Any type that we don't explicitly support is treated as a nested data class.
            return Binder(field_type)
    elif origin in (UnionType, Union):
        collected_types = [
            # Note that 'arg' cannot be a union itself, as Python automatically flattens nested union types.
            _collect_type(arg, context)
            for arg in get_args(field_type)
            # Optional fields are allowed, but None can only be the default, not the parsed value.
            if arg is not NoneType
        ]
        # Note that the list of collected types cannot be empty as Python either rejects (None | None) or
        # simplifies (Union[None] to NoneType) union annotations that contain nothing but None.
        if len(collected_types) == 1:
            return collected_types[0]
        else:
            return reduce(operator.__or__, collected_types)
    elif issubclass(origin, Mapping):
        type_args = get_args(field_type)
        try:
            key_type, value_type = type_args
        except ValueError:
            raise TypeError(f"Mapping '{context}' must have two type arguments") from None
        if key_type is not str:
            raise TypeError(f"Mapping '{context}' has key type '{key_type.__name__}', expected 'str'")
        return origin[(key_type, _collect_type(value_type, f"{context}[]"))]  # type: ignore[no-any-return]
    elif issubclass(origin, Iterable):
        args = get_args(field_type)
        arg_context = f"{context}[]"
        if issubclass(origin, tuple):
            if len(args) == 2 and args[-1] is ...:
                # Replace tuple[T, ...] by Sequence[T], so tuple[] is only used for heterogeneous tuples.
                origin = Sequence
            else:
                return origin[tuple(_collect_type(arg, arg_context) for arg in args)]  # type: ignore[no-any-return]
        # Use the convention that the first argument is the element type.
        return origin[_collect_type(args[0], arg_context)]  # type: ignore[no-any-return]
    elif origin is type:
        try:
            (arg,) = get_args(field_type)
        except ValueError:
            raise TypeError(f"type[...] annotation for '{context}' must have exactly one type argument") from None
        bases = get_args(arg) if get_origin(arg) in (UnionType, Union) else (arg,)
        if Any in bases:
            return cast(type, type[Any])  # type: ignore[index]
        # Convert 'type[A | B]' to 'type[A] | type[B]'.
        collected_types = []
        for base in bases:
            if not isinstance(base, type):
                raise TypeError(f"type[...] annotation for '{context}' must have a type as its argument")
            collected_types.append(type[base])  # type: ignore[index]
        return reduce(operator.__or__, collected_types)
    else:
        raise TypeError(f"Field '{context}' has unsupported generic type '{origin.__name__}'")


def _find_object_by_name(name: str, context: str) -> object:
    """
    Look up a Python object by its fully-qualified name.

    Raises ValueError if the name could not be resolved.
    """
    parts = name.split(".")

    # Figure out how many parts form the module name.
    node = None
    idx = 0
    while idx < len(parts):
        module_name = ".".join(parts[: idx + 1])
        try:
            node = import_module(module_name)
        except ModuleNotFoundError:
            break
        idx += 1

    if node is None:
        raise ValueError(f"Python object for '{context}' not found: no top-level module named '{parts[0]}'")

    while idx < len(parts):
        try:
            node = getattr(node, parts[idx])
        except AttributeError:
            raise ValueError(
                f"Python object for '{context}' not found: "
                f"name '{parts[idx]}' does not exist in '{'.'.join(parts[:idx])}'"
            ) from None
        idx += 1

    return node


def _find_field(full_name: str, field_names: Collection[str]) -> tuple[str, str | None]:
    """
    Return the field name and optional suffix for the given full name.

    Raises KeyError if no such field exists.
    """
    if full_name in field_names:
        return full_name, None
    try:
        name, suffix = full_name.rsplit("_", 1)
    except ValueError:
        raise KeyError(full_name) from None
    if name in field_names:
        return name, suffix
    else:
        raise KeyError(full_name)


def _get_fields(cls: type) -> Iterator[tuple[Field, type]]:
    """
    Iterates through all the fields in a dataclass.

    This includes fields inherited from superclasses.
    """

    fields_by_name = {field.name: field for field in fields(cls)}

    for field_container in reversed(cls.__mro__):
        # Note: getmodule() can return None, but the end result is still fine.
        cls_globals = getattr(getmodule(field_container), "__dict__", {})
        cls_locals = vars(field_container)

        for name, annotation in get_annotations(field_container).items():
            field = fields_by_name[name]
            if not field.init:
                continue
            if isinstance(annotation, str):
                try:
                    annotation = eval(annotation, cls_globals, cls_locals)  # noqa: PGH001
                except NameError as ex:
                    raise TypeError(f"Failed to parse annotation of field '{cls.__name__}.{name}': {ex}") from None
            yield field, annotation


def _check_field(field: Field, field_type: type, context: str) -> None:
    """
    Perform some checks on the validity of a field definition.

    This does not do a full type check: there are better tools for that.
    Instead, it checks specific limitations that our Binder imposes on dataclasses.
    """
    if get_origin(field_type) is UnionType and NoneType in get_args(field_type) and field.default is not None:
        raise TypeError(f"Default for optional field '{context}' is not None")


_TIMEDELTA_SUFFIXES = {"days", "seconds", "microseconds", "milliseconds", "minutes", "hours", "weeks"}

T = TypeVar("T")


@dataclass(slots=True)
class _ClassInfo(Generic[T]):

    _cache: ClassVar[MutableMapping[type[Any], _ClassInfo[Any]]] = WeakKeyDictionary()

    dataclass: type[T]
    field_types: Mapping[str, type | Binder[Any]]
    _field_docstrings: Mapping[str, str] | None = None

    @classmethod
    def get(cls, dataclass: type[T]) -> _ClassInfo[T]:
        try:
            return cls._cache[dataclass]
        except KeyError:
            # Populate field_types *after* adding new instance to the cache to make sure
            # _collect_type() will find the given dataclass if it's accessed recursively.
            field_types: dict[str, type | Binder[Any]] = {}
            info = cls(dataclass, field_types)
            cls._cache[dataclass] = info
            for field, field_type in _get_fields(dataclass):
                field_name = field.name
                context = f"{dataclass.__name__}.{field_name}"
                field_types[field_name] = _collect_type(field_type, context)
                _check_field(field, field_type, context)
            return info

    @property
    def class_docstring(self) -> str | None:
        class_docstring = self.dataclass.__doc__
        if class_docstring is None:
            # No coverage because of the undocumented feature described below.
            return None  # pragma: no cover
        if class_docstring.startswith(f"{self.dataclass.__name__}("):
            # As an undocumented feature, the dataclass implementation will auto-generate docstrings.
            # Those only contain redundant information, so we don't want to use them.
            return None
        return cleandoc(class_docstring)

    @property
    def field_docstrings(self) -> Mapping[str, str]:
        field_docstrings = self._field_docstrings
        if field_docstrings is None:
            self._field_docstrings = field_docstrings = get_field_docstrings(self.dataclass)
        return field_docstrings


class Binder(Generic[T]):
    """
    Binds TOML data to a specific dataclass.
    """

    __slots__ = ("_dataclass", "_instance", "_class_info")
    _dataclass: type[T]
    _instance: T | None
    _class_info: _ClassInfo[T]

    def __class_getitem__(cls: type[Binder[T]], dataclass: type[T]) -> Binder[T]:
        """Deprecated: use `Binder(MyDataClass)` instead."""
        return cls(dataclass)

    @overload
    def __init__(self, class_or_instance: type[T]) -> None:
        ...

    @overload
    def __init__(self, class_or_instance: T) -> None:
        ...

    def __init__(self, class_or_instance: type[T] | T) -> None:
        if isinstance(class_or_instance, type):
            self._dataclass = dataclass = class_or_instance
            self._instance = None
        else:
            self._dataclass = dataclass = class_or_instance.__class__
            self._instance = class_or_instance
        self._class_info = _ClassInfo.get(dataclass)

    def _bind_to_single_type(self, value: object, field_type: type, context: str) -> object:
        """
        Convert a TOML value to a singular (non-union) field type.

        Raises TypeError if the TOML value's type doesn't match the field type.
        """
        origin = get_origin(field_type)
        if origin is None:
            if field_type is ModuleType:
                if not isinstance(value, str):
                    raise TypeError(
                        f"Expected TOML string for Python reference '{context}', got '{type(value).__name__}'"
                    )
                module = _find_object_by_name(value, context)
                if not isinstance(module, ModuleType):
                    raise TypeError(f"Value for '{context}' has type '{type(module).__name__}', expected module")
                return module
            elif field_type is timedelta:
                if isinstance(value, timedelta):
                    return value
                elif isinstance(value, time):
                    return timedelta(
                        hours=value.hour, minutes=value.minute, seconds=value.second, microseconds=value.microsecond
                    )
                else:
                    raise TypeError(f"Value for '{context}' has type '{type(value).__name__}', expected time")
            elif isinstance(value, field_type) and (
                type(value) is not bool or field_type is bool or field_type is object
            ):
                return value
        elif issubclass(origin, Mapping):
            if not isinstance(value, dict):
                raise TypeError(f"Value for '{context}' has type '{type(value).__name__}', expected table")
            key_type, elem_type = get_args(field_type)
            mapping = {
                key: self._bind_to_field(elem, elem_type, None, f'{context}["{key}"]') for key, elem in value.items()
            }
            return (
                (mapping if isinstance(origin, MutableMapping) else MappingProxyType(mapping))
                if isabstract(origin)
                else field_type(mapping)
            )
        elif issubclass(origin, Iterable):
            if not isinstance(value, list):
                raise TypeError(f"Value for '{context}' has type '{type(value).__name__}', expected array")
            type_args = get_args(field_type)
            if issubclass(origin, tuple):
                if len(type_args) == len(value):
                    return tuple(
                        self._bind_to_field(elem, elem_type, None, f"{context}[{index}]")
                        for index, (elem, elem_type) in enumerate(zip(value, type_args, strict=True))
                    )
                else:
                    raise TypeError(f"Expected {len(type_args)} elements for '{context}', got {len(value)}")
            (elem_type,) = type_args
            container_class = (
                (list if isinstance(origin, MutableSequence) else tuple) if isabstract(origin) else field_type
            )
            return container_class(
                self._bind_to_field(elem, elem_type, None, f"{context}[{index}]") for index, elem in enumerate(value)
            )
        elif origin is type:
            if not isinstance(value, str):
                raise TypeError(f"Expected TOML string for Python reference '{context}', got '{type(value).__name__}'")
            obj = _find_object_by_name(value, context)
            if not isinstance(obj, type):
                raise TypeError(f"Value for '{context}' has type '{type(obj).__name__}', expected class")
            # Note that _collect_type() already verified the type args.
            (expected_type,) = get_args(field_type)
            if expected_type is Any or issubclass(obj, expected_type):
                return obj
            else:
                raise TypeError(
                    f"Resolved '{context}' to class '{obj.__name__}', expected subclass of '{expected_type.__name__}'"
                )
        else:
            # This is currently unreachable because we reject unsupported generic types in _collect_type().
            raise AssertionError(origin)

        raise TypeError(f"Value for '{context}' has type '{type(value).__name__}', expected '{field_type.__name__}'")

    def _bind_to_field(self, value: object, field_type: type | Binder[Any], instance: T | None, context: str) -> object:
        """
        Convert a TOML value to a field type which is possibly a union type.

        Raises TypeError if the TOML value's type doesn't match the field type.
        """
        target_types = get_args(field_type) if get_origin(field_type) is UnionType else (field_type,)
        for target_type in target_types:
            try:
                if isinstance(field_type, Binder):
                    if not isinstance(value, dict):
                        raise TypeError(f"Value for '{context}' has type '{type(value).__name__}', expected table")
                    return field_type._bind_to_class(value, instance, context)
                else:
                    return self._bind_to_single_type(value, target_type, context)
            except TypeError:
                if len(target_types) == 1:
                    raise
                # TODO: This is inefficient: we format and then discard the error string.
                #       Union types are not used a lot though, so it's fine for now.
                # TODO: When the union contains multiple custom classes, we pick the first that succeeds.
                #       It would be cleaner to limit custom classes to one at collection time.
        raise TypeError(f"Value for '{context}' has type '{type(value).__name__}', expected '{field_type}'")

    def _bind_to_class(self, toml_dict: Mapping[str, Any], instance: T | None, context: str) -> T:
        field_types = self._class_info.field_types
        parsed = {}
        for key, value in toml_dict.items():
            if "_" in key:
                raise ValueError(f"Underscore found in TOML key '{key}'")
            field_name = key.replace("-", "_")
            try:
                field_name, suffix = _find_field(field_name, field_types)
            except KeyError:
                raise ValueError(f"Field '{context}.{field_name}' does not exist") from None

            field_type = field_types[field_name]
            if suffix is not None:
                if field_type is timedelta and suffix in _TIMEDELTA_SUFFIXES:
                    if isinstance(value, int | float) and not isinstance(value, bool):
                        value = timedelta(**{suffix: value})
                    else:
                        raise TypeError(
                            f"Value for '{context}.{field_name}' with suffix '{suffix}' "
                            f"has type '{type(value).__name__}', expected number"
                        )
                else:
                    type_name = (field_type._dataclass if isinstance(field_type, Binder) else field_type).__name__
                    raise ValueError(
                        f"Field '{context}.{field_name}' has type '{type_name}', "
                        f"which does not support suffix '{suffix}'"
                    )

            parsed[field_name] = self._bind_to_field(
                value,
                field_type,
                None if instance is None else getattr(instance, field_name),
                f"{context}.{field_name}",
            )

        if instance is None:
            return self._dataclass(**parsed)
        else:
            return replace(instance, **parsed)  # type: ignore[type-var]

    def format_toml_template(self) -> Iterator[str]:
        """
        Yield lines of TOML text as a template for populating the dataclass or object that we are binding to.

        A template in this case means it is suitable as a starting point for a user to fill in;
        the template is not guaranteed to be parseable as-is.

        If we are binding to an object, values from that object will be used to populate the template.
        If we are binding to a class, example values will be derived from the field types.
        """

        table = Table(self, "", self._instance, None)
        lines = table.format_table(set())
        for line in lines:
            if line:
                yield line
                break
        yield from lines

    def _format_toml_table(self, instance: T | None, defer: Callable[[Table[Any]], None]) -> Iterator[str]:
        dataclass = self._dataclass
        field_types = self._class_info.field_types
        docstrings = self._class_info.field_docstrings

        for field in fields(dataclass):  # type: ignore[arg-type]
            if not field.init:
                continue

            key = field.name.replace("_", "-")
            # Most Python names are valid as bare keys, but not if they contain non-ASCII characters.
            key_fmt = "".join(_iter_format_key(key))
            value = None if instance is None else getattr(instance, field.name)
            docstring = docstrings.get(field.name)

            default = field.default
            if default is MISSING:
                default_factory = field.default_factory
                if default_factory is not MISSING:
                    # We don't call the factory:
                    # - to avoid listing a dynamic value as a default, like the current date
                    # - to not trigger any unwanted side effects
                    default = {list: [], dict: {}}.get(default_factory)  # type: ignore[call-overload]
            optional = default is not MISSING

            field_type = field_types[field.name]
            if isinstance(field_type, Binder):
                defer(Table(field_type, key_fmt, value, docstring, optional))
                continue
            origin = get_origin(field_type)
            if origin is not None:
                if issubclass(origin, Mapping):
                    key_type, value_type = get_args(field_type)
                    if isinstance(value_type, Binder):
                        if value is None:
                            nested_map = {f"{key_fmt}.<name>": None}
                        else:
                            nested_map = {
                                f"{key_fmt}.{''.join(_iter_format_key(nested_key))}": nested_value
                                for nested_key, nested_value in value.items()
                            }
                        for nested_key_fmt, nested_value in nested_map.items():
                            defer(Table(value_type, nested_key_fmt, nested_value, docstring))
                        continue
                    if value_type is object:  # Any
                        defer(Table(None, key_fmt, value, docstring, optional))
                        continue
                elif issubclass(origin, Sequence):
                    (value_type,) = get_args(field_type)
                    binder = value_type if isinstance(value_type, Binder) else None
                    if binder is not None or (
                        value_type is object  # Any
                        and (value is None or all(isinstance(item, Mapping) or is_dataclass(item) for item in value))
                    ):
                        nested_key_fmt = f"[{key_fmt}]"
                        for nested_value in [None] if value is None else value:
                            defer(Table(binder, nested_key_fmt, nested_value, docstring, optional))
                        continue

            yield ""

            comments = [docstring]
            if not optional or default is None:
                fmt_default = None
                comments.append("Optional." if default is None else "Mandatory.")
            else:
                fmt_default = format_toml_pair(key, default)
                comments.append(f"Default:\n{fmt_default}")
            yield from _format_comments(*comments)

            if value is None:
                if not optional or default is None:
                    comment = "# " if default is None else ""
                    key_fmt = "".join(_iter_format_key(key))
                    value_fmt = _format_value_for_type(field_type)
                    yield f"{comment}{key_fmt} = {value_fmt}"
            else:
                fmt_value = format_toml_pair(key, value)
                if fmt_value != fmt_default:
                    yield fmt_value

    if TYPE_CHECKING:
        # These definitions exist to support the deprecated `Binder[DC]` syntax in mypy.

        @classmethod
        def bind(cls, data: Mapping[str, Any]) -> T:
            ...

        @classmethod
        def parse_toml(cls, file: BinaryIO | str | Path) -> T:
            ...

    else:

        def bind(self, data: Mapping[str, Any]) -> T:
            return self._bind_to_class(data, self._instance, self._dataclass.__name__)

        def parse_toml(self, file: BinaryIO | str | Path) -> T:
            match file:
                case Path() | str():
                    with open(file, "rb") as stream:
                        data = tomllib.load(stream)
                case _:
                    data = tomllib.load(file)
            return self.bind(data)


@dataclass
class Table(Generic[T]):
    """The information to format a TOML table."""

    binder: Binder[T] | None
    key_fmt: str
    value: T | None
    field_docstring: str | None
    optional: bool = False

    @property
    def class_docstring(self) -> str | None:
        binder = self.binder
        return None if binder is None else binder._class_info.class_docstring

    def prefix_context(self, context: str) -> Table[T]:
        return replace(self, key_fmt=f"{context}.{self.key_fmt}" if context else self.key_fmt)

    def format_table(self, inside: Set[type]) -> Iterator[str]:
        """
        The `inside` parameter keeps track of which dataclasses we are currently outputting,
        to prevent infinite recursion.
        """

        child_tables: list[Table[Any]] = []
        context = self.key_fmt
        value = self.value

        if (binder := self.binder) is None:
            match value:
                case Mapping() as mapping:
                    content = [format_toml_pair(k, v) for k, v in mapping.items()]
                case dc if is_dataclass(dc):
                    content = [format_toml_pair(k, v) for k, v in asdict(dc).items()]  # type: ignore[arg-type]
                case _:
                    content = []
        elif value is None and binder._dataclass in inside:
            content = None
        else:
            inside |= {binder._dataclass}
            content = list(binder._format_toml_table(value, child_tables.append))
            if not content:
                content = None

        if content is not None:
            if context:
                yield from self.format_header()

            yield from content

        for table in child_tables:
            yield from table.prefix_context(context).format_table(inside)

    def format_header(self) -> Iterator[str]:
        yield ""
        yield from _format_comments(
            self.class_docstring,
            self.field_docstring,
            "Optional table." if self.optional else None,
        )
        yield f"[{self.key_fmt}]"


def format_toml_pair(key: str, value: object) -> str:
    """Format a key/value pair as TOML text."""
    suffix, data = _to_toml_pair(value)
    if suffix is not None:
        key += suffix
    return "".join(_iter_format_key_value(key, data))


def _to_toml_pair(value: object) -> tuple[str | None, Any]:
    """Return a TOML-compatible suffix and value pair with the data from the given rich value object."""
    match value:
        case str() | int() | float() | date() | time():  # note: 'bool' is a subclass of 'int'
            return None, value
        case timedelta():
            if value.days == 0:
                # Format as local time.
                sec = value.seconds
                loc = time(hour=sec // 3600, minute=(sec // 60) % 60, second=sec % 60, microsecond=value.microseconds)
                return None, loc
            elif value.microseconds != 0:
                sec = value.days * 24 * 3600 + value.seconds
                usec = sec * 1000000 + value.microseconds
                if usec % 1000 == 0:
                    return "-milliseconds", usec // 1000
                else:
                    return "-microseconds", usec
            elif value.seconds != 0:
                sec = value.days * 24 * 3600 + value.seconds
                if sec % 3600 == 0:
                    return "-hours", sec // 3600
                elif sec % 60 == 0:
                    return "-minutes", sec // 60
                else:
                    return "-seconds", sec
            else:
                days = value.days
                if days % 7 == 0:
                    return "-weeks", days // 7
                else:
                    return "-days", days
        case ModuleType():
            return None, value.__name__
        case Mapping():
            table = {}
            for key, item_val in value.items():
                suffix, data = _to_toml_pair(item_val)
                if suffix is not None:
                    key += suffix
                table[key] = data
            return None, table
        case Iterable():
            array = []
            for item in value:
                dd_key, dd_val = _to_toml_pair(item)
                if dd_key is not None:
                    raise ValueError(f"Value {item!r} in array cannot be expressed without key suffix")
                array.append(dd_val)
            return None, array
        case type():
            return None, f"{value.__module__}.{value.__name__}"
        case _ if is_dataclass(value):
            table = {}
            for field in fields(value):
                if not field.init:
                    continue
                name = field.name
                sub_value = getattr(value, name)
                if sub_value is None:
                    assert field.default is None
                    continue
                key = name.replace("_", "-")
                suffix, data = _to_toml_pair(sub_value)
                if suffix is not None:
                    key += suffix
                table[key] = data
            return None, table
    raise TypeError(type(value).__name__)


_TOML_ESCAPES = {"\b": r"\b", "\t": r"\t", "\n": r"\n", "\f": r"\f", "\r": r"\r", '"': r"\"", "\\": r"\\"}
_TOML_BARE_KEY = re.compile(r"^[A-Za-z0-9_\-]+$")


def _iter_format_key_value(key: str, value: object) -> Iterator[str]:
    yield from _iter_format_key(key)
    yield " = "
    yield from _iter_format_value(value)


def _iter_format_key(key: str) -> Iterator[str]:
    if _TOML_BARE_KEY.match(key):
        yield key
    else:
        yield from _iter_format_value(key)


def _iter_format_value(value: object) -> Iterator[str]:
    match value:
        case bool():
            yield str(value).lower()
        case int() | float():
            yield str(value)
        case str():
            # Ideally we could assume that every tool along the way defaults to UTF-8 and just output that,
            # but I don't think we live in that world yet, so escape non-ASCII characters.
            if value.isprintable() and value.isascii() and "'" not in value:
                # Use a literal string if possible.
                yield "'"
                yield value
                yield "'"
            else:
                # Use basic string otherwise.
                yield '"'
                for ch in value:
                    if ch.isascii():
                        try:
                            yield _TOML_ESCAPES[ch]
                        except KeyError:
                            if ch.isprintable():
                                yield ch
                            else:
                                yield f"\\u{ord(ch):04X}"
                    elif ord(ch) < 0x10000:
                        yield f"\\u{ord(ch):04X}"
                    else:
                        yield f"\\U{ord(ch):08X}"
                yield '"'
        case date() | time():
            yield value.isoformat()
        case Mapping():
            first = True
            yield "{"
            for key, elem in value.items():
                if first:
                    first = False
                else:
                    yield ", "
                yield from _iter_format_key_value(key, elem)
            yield "}"
        case Iterable():
            first = True
            yield "["
            for elem in value:
                if first:
                    first = False
                else:
                    yield ", "
                yield from _iter_format_value(elem)
            yield "]"
        case _:
            raise TypeError(type(value).__name__)


def _format_comments(*comments: str | None) -> Iterator[str]:
    separator = False
    for comment in comments:
        if comment:
            contains_empty = False
            for line in comment.split("\n"):
                if separator:
                    yield "#"
                    separator = False
                yield f"# {line}".rstrip()
                contains_empty |= not line
            separator = contains_empty


def get_field_docstrings(dataclass: type[Any]) -> Mapping[str, str]:
    """
    Return a mapping of field name to the docstring for that field.

    Attribute docstrings are not supported by the Python runtime, therefore we must read them from the source code.
    If the source code cannot be found, an empty mapping is returned.
    """

    try:
        source = getsource(dataclass)
    except (OSError, TypeError):
        # According to the documentation only OSError can be raised, but Python 3.10 raises TypeError for
        # sourceless dataclasses.
        #   https://github.com/python/cpython/issues/98239
        return {}

    module_def = ast.parse(dedent(source), "<string>")
    class_def = module_def.body[0]
    assert isinstance(class_def, ast.ClassDef)

    docstrings = {}
    scope = None
    for node in class_def.body:
        match node:
            case ast.AnnAssign(target=ast.Name(id=name)):
                scope = name
            case ast.Expr(value=ast.Constant(value=docstring)):
                if scope is None:
                    # When using 'scope is not None', Coverage 6.4.4 will consider the 'is None' branch uncovered.
                    pass
                else:
                    docstrings[scope] = cleandoc(docstring)
    return docstrings


def format_template(class_or_instance: Any) -> Iterator[str]:
    """Deprecated: use `Binder.format_toml_template()` instead."""
    yield from Binder(class_or_instance).format_toml_template()


def _format_value_for_type(field_type: type[Any]) -> str:
    origin = get_origin(field_type)
    if origin is None:
        if field_type is str:
            return "'???'"
        elif field_type is bool:
            return "true | false"
        elif field_type is int:
            return "0"
        elif field_type is float:
            return "0.0"
        elif field_type is ModuleType:
            return "'fully.qualified.module.name'"
        elif field_type is datetime:
            return "2020-01-01 00:00:00+01:00"
        elif field_type is date:
            return "2020-01-01"
        elif field_type is time or field_type is timedelta:
            return "00:00:00"
        else:
            # We have handled all the non-generic types supported by _collect_type().
            raise AssertionError(field_type)
    elif origin in (UnionType, Union):
        return " | ".join(_format_value_for_type(arg) for arg in get_args(field_type))
    elif issubclass(origin, Mapping):
        return "{}"
    elif issubclass(origin, Iterable):
        return "[]"
    elif origin is type:
        return "'fully.qualified.class.name'"
    else:
        # This is currently unreachable because we reject unsupported generic types in _collect_type().
        raise AssertionError(origin)
