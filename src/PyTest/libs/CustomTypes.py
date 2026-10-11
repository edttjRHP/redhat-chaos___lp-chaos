#!/usr/bin/env python3
'''Defines custom types.'''


from abc import ABCMeta
from collections import defaultdict
from typing import Any, Union


class NamedDict(dict):
    '''Immutable `dict` with `dot` access.'''
    def __init__(self, *args, **kwargs) -> None:
        self.____check_keys____(*args, **kwargs)
        super().__init__(*args, **kwargs)

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

    def ____check_keys____(self, *args, **kwargs) -> None:
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
                    f"Attribute ({repr(key)}) on "
                    f"{repr(super().__getattribute__('__class__'))} instance "
                    "must be a valid python identifier.",
                )

    def ___update___(self, *args, **kwargs) -> None:
        self.____check_keys____(*args, **kwargs)
        super().update(*args, **kwargs)

    # Shallow Merge (+).
    def __add__(self, other: Any) -> 'NamedDict':
        if not isinstance(other, dict): return NotImplemented
        return NamedDict({**self, **other})
    def __radd__(self, other: Any) -> 'NamedDict':
        if not isinstance(other, dict): return NotImplemented
        return NamedDict({**other, **self})

    @staticmethod
    def _DeepMerge(
            base: Union['NamedDict', dict],
            override: Union['NamedDict', dict],
    ) -> Union['NamedDict', dict]:
        merged = dict(base)
        for (k, v) in dict.items(override):
            if (
                (k in merged) and
                (type(v) in (NamedDict, dict)) and
                (type(merged[k]) is type(v))
            ):
                merged[k] = NamedDict._DeepMerge(merged[k], v)
            else:
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

    def __ror__(self, other: Any) -> 'NamedDict':
        if not isinstance(other, dict): return NotImplemented
        return NamedDict(NamedDict._DeepMerge(other, self))


class NestedDict(defaultdict):
    '''Nested `dict`.'''
    def __init__(self, *args, **kwargs):
        super().__init__(NestedDict, *args, **kwargs)

    def __repr__(self):
        return repr(dict(self))


class SingletonMeta(ABCMeta):
    '''MetaClass to make a derived class as singleton.'''
    __instances = {}

    def __call__(cls, *args, **kwargs):
        if (cls not in cls.__instances):
            cls.__instances[cls] = super().__call__(*args, **kwargs)
        return cls.__instances[cls]
