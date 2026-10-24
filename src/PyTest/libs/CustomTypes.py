#!/usr/bin/env python3
'''Defines custom types.'''


from abc import ABCMeta
from collections import defaultdict
from typing import Any, Union


class NamedDict(dict):
    '''
    A `dict` with dot access.

    The attribute/item assignment is blocked. Use `___update___()` instead.

    Deep merge (`|`) requires exact concrete types for existing keys,
    including nested keys:
      - Matching `dict` or `NamedDict` values recurse.
      - Matching `list` concatenate into a new `list`, retaining order,
        duplicates and item identities without mutating either input `list`.
      - Other matching values replace.

    New keys retain their supplied values.
    '''
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        data = dict(*args, **kwargs)
        self.____check_keys____(data)
        super().__init__(data)

    def __getattribute__(self, key: str) -> Any:
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
        raise AttributeError(
            'Can not set attribute/key on '
            f"{super().__getattribute__('__class__')!r} instance.",
        )

    __setitem__ = __setattr__

    def ____check_keys____(self, *args: Any, **kwargs: Any) -> None:
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
        data = dict(*args, **kwargs)
        self.____check_keys____(data)
        super().update(data)

    # Shallow Merge (+).
    def __add__(self, other: Any) -> 'NamedDict':
        if not isinstance(other, dict): return NotImplemented
        return NamedDict({**self, **other})

    __iadd__ = __add__

    def __radd__(self, other: Any) -> 'NamedDict':
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
            if (
                (k in merged) and
                (merged[k] is not None) and
                (v is not None)
            ):
                if (type(merged[k]) is not type(v)):
                    raise TypeError(
                        f'NamedDict deep-merge at `{keyPath}[{k!r}]`: '
                        f'expected `{type(merged[k]).__name__}`, received '
                        f'`{type(v).__name__}`.'
                    )
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
        if not isinstance(other, NamedDict):
            raise TypeError(
                'NamedDict deep-merge requires a `NamedDict` right operand, '
                f'not `{type(other).__name__}`.'
            )
        return NamedDict(NamedDict._DeepMerge(self, other))

    __ior__ = __or__

    def __ror__(self, other: Any) -> 'NamedDict':
        if not isinstance(other, dict): return NotImplemented
        return NamedDict(NamedDict._DeepMerge(other, self))


class NestedDefDict(defaultdict):
    '''Nested `dict`.'''
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if (args and (args[0] is NestedDefDict)): args = args[1:]
        super().__init__(NestedDefDict, *args, **kwargs)

    def __repr__(self) -> str:
        return repr(dict(self))


class SingletonMeta(ABCMeta):
    '''MetaClass to make a derived class as singleton.'''
    __instances = {}

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        if (cls not in cls.__instances):
            cls.__instances[cls] = super().__call__(*args, **kwargs)
        return cls.__instances[cls]
