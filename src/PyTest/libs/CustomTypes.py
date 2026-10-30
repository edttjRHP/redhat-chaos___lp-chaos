'''Defines custom types.'''
from abc import ABCMeta
from collections import defaultdict
import os
from typing import Any, Union, Optional, Type


class NamedDict(dict):
    '''
    A `dict` with dot access.

    The attribute/item assignment is blocked. Use `___update___()` instead.

    Deep merge (`|`):
        For a key present in both `base` and `override`:
          - If `base[key]` is `None`, set to `override[key]`.
          - If `override[key]` is `None`, set to it.
          - Otherwise, `base[key]` and `override[key]` must have exactly the
            same type.
          - Matching `dict` or `NamedDict` values recurse.
          - Matching `list` concatenate into a new `list`, retaining order,
            duplicates and item identities without mutating either input
            `list`.
          - Other matching values replace.
        Use `NamedDict._TypeReplace(value)` to replace the value of a node.
            If the node's current value is not None and `value` is not None,
            their types must match.

    New keys retain their supplied values.
    '''
    class _TypeReplace:
        '''
        Marker to indicate replacing value (instead of merging) is requested
        during deep-merge.
        '''
        def __init__(self, value: Any) -> None:
            '''
            :param value: Replacement value.
            '''
            self.value = value

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        '''
        Initialize from dictionary inputs with valid identifier keys.

        :param args:        Positional inputs accepted by `dict`.
        :param kwargs:      Keyword entries.

        :raises KeyError:   A key is not a valid Python identifier.
        '''
        data = dict(*args, **kwargs)
        self.____check_keys____(data)
        super().__init__(data)

    def __getattribute__(self, key: str) -> Any:
        '''
        Resolve stored keys, escaped dictionary methods or normal attributes.

        :param key:             Attribute or key name.

        :return:                Resolved value.

        :raises AttributeError: Neither the key nor the attribute exists.
        '''
        if (key in (
                '___update___',
        )):
            return super().__getattribute__(key)
        if (key in (
                '___get___', '___items___', '___keys___', '___values___',
                '___copy___', '___fromkeys___',
        )):
            return super().__getattribute__(key[3:-3])
        if (key.startswith('__') and key.endswith('__')):
            return super().__getattribute__(key)
        if super().__contains__(key):
            return super().__getitem__(key)
        try:
            return super().__getattribute__(key)
        except AttributeError as e:
            raise AttributeError(
                'Can not get attribute/key on '
                f"{super().__getattribute__('__class__')!r} instance: "
                f'{key!r}.',
            ) from e

    def __setattr__(self, key: Any, value: Any) -> None:
        '''
        Reject direct attribute or item assignment.

        :param key:             Requested attribute or key.
        :param value:           Requested value.

        :raises AttributeError: Direct assignment is not supported.
        '''
        raise AttributeError(
            'Can not set attribute/key on '
            f"{super().__getattribute__('__class__')!r} instance.",
        )

    __setitem__ = __setattr__

    def ____check_keys____(self, *args: Any, **kwargs: Any) -> None:
        '''
        Check that input keys are Python identifiers.

        :param args:        Mapping or iterable of entries to inspect.
        :param kwargs:      Additional keys to inspect.

        :raises KeyError:   A key is not a valid Python identifier.
        '''
        keys = ()
        if args:
            if hasattr(args[0], 'keys'):
                keys += tuple(args[0].keys())
            elif hasattr(args[0], '__iter__'):
                # pylint: disable=consider-using-generator
                keys += tuple([
                    elem[0] for elem in args[0]
                    if (hasattr(elem, '__iter__') and elem)
                ])
        keys += tuple(kwargs.keys())
        for key in keys:
            if not (isinstance(key, str) and key.isidentifier()):
                raise KeyError(
                    f'Attribute ({repr(key)}) on '
                    f"{repr(super().__getattribute__('__class__'))} instance "
                    'must be a valid python identifier.',
                )

    def ___update___(self, *args: Any, **kwargs: Any) -> None:
        '''
        Update entries after checking identifier keys.

        :param args:        Positional inputs accepted by `dict`.
        :param kwargs:      Keyword entries.
        '''
        data = dict(*args, **kwargs)
        self.____check_keys____(data)
        super().update(data)

    # Shallow Merge (+).
    def __add__(self, other: Any) -> 'NamedDict':
        '''
        Shallow-merge this mapping with a right-hand dictionary.

        :param other:   Other operand.

        :return:
            Merged mapping, or `NotImplemented` for other operand types.
        '''
        if not isinstance(other, dict): return NotImplemented
        return NamedDict({**self, **other})

    __iadd__ = __add__

    def __radd__(self, other: Any) -> 'NamedDict':
        '''
        Shallow-merge a left-hand dictionary with this mapping.

        :param other:   Other operand.

        :return:
            Merged mapping, or `NotImplemented` for other operand types.
        '''
        if not isinstance(other, dict): return NotImplemented
        return NamedDict({**other, **self})

    @staticmethod
    def _DeepMerge(
            base: Union['NamedDict', dict],
            override: Union['NamedDict', dict],
            keyPath: str = '',
    ) -> Union['NamedDict', dict]:
        '''Replace nullable values; otherwise deep-merge exact types.'''
        merged = dict(base)
        for (k, v) in dict.items(override):
            replace = isinstance(v, NamedDict._TypeReplace)
            if replace: v = v.value
            if (
                (k in merged) and
                (merged[k] is not None) and
                (v is not None)
            ):
                if (type(merged[k]) is not type(v)):
                    raise TypeError('NamedDict ' + (
                        'replacement via `NamedDict._TypeReplace` ' \
                            if replace else 'deep-merge '
                    ) +  (
                        f'at `{keyPath}[{k!r}]`: '
                        f'expected `{type(merged[k]).__name__}`, received '
                        f'`{type(v).__name__}`.'
                    ))
                if not replace:
                    if (type(v) in (NamedDict, dict)):
                        merged[k] = NamedDict._DeepMerge(
                            merged[k], v, f'{keyPath}[{k!r}]',
                        )
                        continue
                    if (type(v) is list):
                        # The `list.extend()` also modify the `base`.
                        merged[k] = merged[k] + v
                        continue
            merged[k] = v
        return (merged if (type(base) is dict) else NamedDict(merged))

    # Deep Merge (|).
    def __or__(self, other: Any) -> 'NamedDict':
        '''
        Deep-merge this mapping with another `NamedDict`.

        :param other:       Right-hand mapping.

        :return:            Merged `NamedDict`.

        :raises TypeError:  Operand or matching non-null value types differ.
        '''
        if not isinstance(other, NamedDict):
            raise TypeError(
                'NamedDict deep-merge requires a `NamedDict` right operand, '
                f'not `{type(other).__name__}`.'
            )
        return NamedDict(NamedDict._DeepMerge(self, other))

    __ior__ = __or__

    def __ror__(self, other: Any) -> 'NamedDict':
        '''
        Deep-merge a left-hand dictionary with this mapping.

        :param other:   Other operand.

        :return:
            Merged mapping, or `NotImplemented` for other operand types.
        '''
        if not isinstance(other, dict): return NotImplemented
        return NamedDict(NamedDict._DeepMerge(other, self))


class NestedDefDict(defaultdict):
    '''Nested `dict`.'''
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        '''
        Initialize a dictionary whose missing entries are nested dictionaries.

        :param args:
            Dictionary inputs. An initial `NestedDefDict` is skipped.
        :param kwargs:  Keyword entries.
        '''
        if (args and (args[0] is NestedDefDict)): args = args[1:]
        super().__init__(NestedDefDict, *args, **kwargs)

    def __repr__(self) -> str:
        '''
        Represent entries as an ordinary dictionary.

        :return:    Dictionary representation.
        '''
        return repr(dict(self))


class SingletonMeta(ABCMeta):
    '''MetaClass to make a derived class as singleton.'''
    __instances = {}

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        '''
        Return the cached instance, constructing it on first use.

        :param args:    Positional constructor arguments.
        :param kwargs:  Keyword constructor arguments.

        :return:        The class' singleton instance.
        '''
        if (cls not in cls.__instances):
            cls.__instances[cls] = super().__call__(*args, **kwargs)
        return cls.__instances[cls]


class TempEnv:
    '''
    Context Manager providing an env. where any alteration of Env. Variables
    are only temporary and will be automatically reverted back.
    '''
    def __enter__(self) -> 'TempEnv':
        '''
        Snapshot process Env. Variables.

        :return: This context manager.
        '''
        self.origEnv = dict(os.environ)
        return self

    def __exit__(
        self,
        excType: Optional[Type[BaseException]],
        excValue: Optional[BaseException],
        traceback: object,
    ) -> bool:
        '''
        Restore the saved Env. Variables.

        :param excType:     Exception type, if any.
        :param excValue:    Exception instance, if any.
        :param traceback:   Exception traceback, if any.

        :return:            Set to `False` to propagate any exception.
        '''
        origKeys = self.origEnv.keys()
        currentKeys = set(os.environ)

        # Remove Env. Variables added while this context was active.
        for key in (currentKeys - origKeys):
            os.environ.pop(key, None)

        # Restore Env. Variables that were unset and/or altered.
        for (key, origValue) in self.origEnv.items():
            if (os.environ.get(key) != origValue):
                os.environ[key] = origValue

        return False
