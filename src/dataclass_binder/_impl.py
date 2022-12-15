"""
TODO: Ignore fields with init=False.
"""

from __future__ import annotations

import ast
import operator
import re
from collections.abc import Collection, Iterable, Iterator, Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import MISSING, Field, fields, is_dataclass
from datetime import date, datetime, time, timedelta
from functools import reduce
from importlib import import_module
from inspect import cleandoc, get_annotations, getmodule, getsource, isabstract
from pathlib import Path
from textwrap import dedent
from types import ModuleType, NoneType, UnionType
from typing import IO, Any, ClassVar, Generic, TypeVar, Union, cast, get_args, get_origin
from weakref import WeakKeyDictionary

try:
    import tomllib  # type: ignore[import]
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


def _collect_type(field_type: type, context: str) -> type:
    """
    Verify and streamline a type annotation.

    Streamlining means that when there are multiple ways to express the same typing,
    we pick one and convert the alternative forms to that.

    Raises TypeError if the annotation is not supported.
    """
    origin = get_origin(field_type)
    if origin is None:
        if not isinstance(field_type, type):
            raise TypeError(f"Annotation for field '{context}' is not a type")
        elif issubclass(field_type, (str, int, float, date, time, timedelta, ModuleType)):
            return field_type
        elif field_type is type:
            # https://github.com/python/mypy/issues/12392
            return cast(type, type[Any])  # type: ignore[index]
        elif hasattr(field_type, "__class_getitem__"):
            raise TypeError(f"Field '{context}' needs type argument(s)")
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
            raise TypeError(f"Mapping '{context}' must have two type arguments")
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

    # Any type that we don't explicitly support is treated as a nested data class.
    return Binder.__class_getitem__(field_type)


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
        raise KeyError(full_name)
    if name in field_names:
        return name, suffix
    else:
        raise KeyError(full_name)


def _get_fields(cls: type) -> Iterator[tuple[str, type]]:
    """
    Iterates through all the fields in a dataclass.

    This includes fields inherited from superclasses.
    """
    for field_container in reversed(cls.__mro__):
        yield from get_annotations(field_container, eval_str=True).items()


_TIMEDELTA_SUFFIXES = {"days", "seconds", "microseconds", "milliseconds", "minutes", "hours", "weeks"}

T = TypeVar("T")

_binder_cache: MutableMapping[type, type] = WeakKeyDictionary()


class Binder(Generic[T]):
    """
    Binds TOML data to a specific dataclass.
    """

    __slots__ = ()
    _field_types: ClassVar[Mapping[str, type]]

    @classmethod
    def _get_config_class(cls) -> type[T]:
        config_class: type[T]
        (config_class,) = cls.__orig_bases__[0].__args__  # type: ignore[attr-defined]
        if isinstance(config_class, TypeVar):
            raise TypeError("Binder must be specialized before use, for example Binder[MyDataClass]")
        return config_class

    def __class_getitem__(cls, config_class: type[T]) -> type[Binder[T]]:
        try:
            return _binder_cache[config_class]
        except KeyError:
            pass

        field_types = {
            field_name: _collect_type(field_type, f"{config_class.__name__}.{field_name}")
            for field_name, field_type in _get_fields(config_class)
        }

        class SpecializedBinder(super().__class_getitem__(config_class)):  # type: ignore[misc]
            _config_class = config_class
            _field_types = field_types

        _binder_cache[config_class] = SpecializedBinder
        return SpecializedBinder

    @classmethod
    def _bind_to_single_type(cls, value: object, field_type: type, context: str) -> object:
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
            elif issubclass(field_type, Binder):
                if not isinstance(value, dict):
                    raise TypeError(f"Value for '{context}' has type '{type(value).__name__}', expected table")
                return field_type._bind_to_class(value, context)
            elif isinstance(value, field_type) and (type(value) is not bool or field_type is bool):
                return value
        elif issubclass(origin, Mapping):
            if not isinstance(value, dict):
                raise TypeError(f"Value for '{context}' has type '{type(value).__name__}', expected table")
            key_type, elem_type = get_args(field_type)
            mapping = {key: cls._bind_to_field(elem, elem_type, f'{context}["{key}"]') for key, elem in value.items()}
            return (
                (mapping if isinstance(origin, MutableMapping) else mapping.items())
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
                        cls._bind_to_field(elem, elem_type, f"{context}[{index}]")
                        for index, (elem, elem_type) in enumerate(zip(value, type_args))
                    )
                else:
                    raise TypeError(f"Expected {len(type_args)} elements for '{context}', got {len(value)}")
            (elem_type,) = type_args
            container_class = (
                (list if isinstance(origin, MutableSequence) else tuple) if isabstract(origin) else field_type
            )
            return container_class(
                cls._bind_to_field(elem, elem_type, f"{context}[{index}]") for index, elem in enumerate(value)
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
            assert False

        raise TypeError(f"Value for '{context}' has type '{type(value).__name__}', expected '{field_type.__name__}'")

    @classmethod
    def _bind_to_field(cls, value: object, field_type: type, context: str) -> object:
        """
        Convert a TOML value to a field type which is possibly a union type.

        Raises TypeError if the TOML value's type doesn't match the field type.
        """
        if get_origin(field_type) is UnionType:
            for arg in get_args(field_type):
                try:
                    return cls._bind_to_single_type(value, arg, context)
                except TypeError:
                    # TODO: This is inefficient: we format and then discard the error string.
                    #       Union types are not used a lot though, so it's fine for now.
                    # TODO: When the union contains multiple custom classes, we pick the first that succeeds.
                    #       It would be cleaner to limit custom classes to one at collection time.
                    pass
            raise TypeError(f"Value for '{context}' has type '{type(value).__name__}', expected '{field_type}'")
        else:
            return cls._bind_to_single_type(value, field_type, context)

    @classmethod
    def _bind_to_class(cls: type[Binder[T]], toml_dict: dict[str, Any], context: str) -> T:
        field_types = cls._field_types
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
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        value = timedelta(**{suffix: value})
                    else:
                        raise TypeError(
                            f"Value for '{context}.{field_name}' with suffix '{suffix}' "
                            f"has type '{type(value).__name__}', expected number"
                        )
                else:
                    raise ValueError(
                        f"Field '{context}.{field_name}' has type '{field_type.__name__}', "
                        f"which does not support suffix '{suffix}'"
                    )

            parsed[field_name] = cls._bind_to_field(value, field_type, f"{context}.{field_name}")

        return cls._get_config_class()(**parsed)

    @classmethod
    def bind(cls, data: dict[str, Any]) -> T:
        return cls._bind_to_class(data, cls._get_config_class().__name__)

    @classmethod
    def parse_toml(cls, file: IO[bytes] | str | Path) -> T:
        match file:
            case Path() | str():
                with open(file, "rb") as stream:
                    data = tomllib.load(stream)
            case _:
                data = tomllib.load(file)
        return cls.bind(data)


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


def get_field_docstrings(config_class: type[Any]) -> Mapping[str, str]:
    """
    Return a mapping of field name to the docstring for that field.

    Attribute docstrings are not supported by the Python runtime, therefore we must read them from the source code.
    If the source code cannot be found, an empty mapping is returned.
    """

    try:
        source = getsource(config_class)
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
    """
    Yield lines of TOML text as a template for configuring the given data class or instance.

    If an instance is provided, values from that instance will be used to populate the template.
    If a class is provided, values will be derived from the field types.
    """

    if isinstance(class_or_instance, type):
        config_class = class_or_instance
        instance = None
    else:
        config_class = class_or_instance.__class__
        instance = class_or_instance

    docstrings = get_field_docstrings(config_class)

    first = True
    for field in fields(config_class):
        if first:
            first = False
        else:
            yield ""

        docstring = docstrings.get(field.name)
        lines = docstring.split("\n") if docstring else []
        # End with an empty line if the docstring contains multiple paragraphs.
        if "" in lines:
            lines.append("")

        for line in lines:
            yield f"# {line}".rstrip()

        key = field.name.replace("_", "-")
        value = None if instance is None else getattr(instance, field.name)
        default = field.default
        if value == default:
            value = None
        if default is MISSING or default is None:
            if default is None:
                yield "# Optional."
            else:
                yield "# Mandatory."
            if value is None:
                comment = "# " if default is None else ""
                key_fmt = "".join(_iter_format_key(key))
                value_fmt = _format_value_for_field(config_class, field)
                yield f"{comment}{key_fmt} = {value_fmt}"
        else:
            yield "# Default:"
            yield f"# {format_toml_pair(key, default)}"
        if value is not None:
            yield f"{format_toml_pair(key, value)}"


def _format_value_for_field(config_class: type[Any], field: Field) -> str:
    """Format an example value or placeholder for a value depending on the given field's type."""

    field_type: type[Any] | str | None = field.type
    if isinstance(field_type, str):
        module_locals = {}
        module = getmodule(config_class)
        if module is not None:
            for name in dir(module):
                module_locals[name] = getattr(module, name)
        try:
            evaluated_type = eval(field_type, globals(), module_locals)
        except Exception:
            field_type = None
        else:
            field_type = _collect_type(evaluated_type, f"{config_class.__name__}.{field.name}")

    if field_type is None:
        return "???"
    else:
        return _format_value_for_type(field_type)


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
        elif is_dataclass(field_type):
            return "".join(_format_fields_inline(field_type))
        else:
            # We have handled all the non-generic types supported by _collect_type().
            assert False
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
        assert False


def _format_fields_inline(config_class: type[Any]) -> Iterable[str]:
    yield "{"
    first = True
    for field in fields(config_class):
        if first:
            first = False
        else:
            yield ", "
        yield from _iter_format_key(field.name.replace("_", "-"))
        yield " = "
        yield _format_value_for_field(config_class, field)
    yield "}"