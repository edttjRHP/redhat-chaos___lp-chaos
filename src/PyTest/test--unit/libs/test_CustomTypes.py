from collections import UserList
from collections.abc import Callable, Iterator
from copy import copy, deepcopy

import pytest

import libs.CustomTypes as MUT


@pytest.mark.parametrize('operator', ['deep', 'shallow'])
@pytest.mark.parametrize(
    ('baseValue', 'overrideValue'),
    [(None, 'resolved'), ('resolved', None), (None, None),
     ([1, 1], [2, 1]),
     (MUT.NamedDict(left=1), MUT.NamedDict(right=2)),
     ({'left': 1}, {'right': 2})],
)
def test_AugmentedOperators_RebindAndPreserveOperands(
    operator: str, baseValue: object, overrideValue: object,
) -> None:
    original = MUT.NamedDict(value=baseValue, retained=1)
    alias = original
    override = MUT.NamedDict(value=overrideValue)
    originalBefore = repr(original)
    overrideBefore = repr(override)
    value = original
    if (operator == 'deep'):
        expected = original | override
        value |= override
    else:
        expected = original + override
        value += override
    assert type(value) is MUT.NamedDict
    assert value == expected and value is not original
    assert alias is original and repr(alias) == originalBefore
    assert repr(override) == overrideBefore
    assert original.value is baseValue
    assert override.value is overrideValue
    if (isinstance(baseValue, list) and operator == 'deep'):
        assert value.value is not baseValue
        assert value.value is not overrideValue
    if (operator == 'shallow'): assert value.value is overrideValue


@pytest.mark.parametrize('operator', ['deep', 'shallow'])
@pytest.mark.parametrize('override', [None, [], 1, {'not-valid': 1}])
def test_AugmentedOperators_PreserveErrorsAndBinding(
    operator: str, override: object,
) -> None:
    original = MUT.NamedDict(value=MUT.NamedDict(number=1))
    value = original
    with pytest.raises((TypeError, KeyError)) as expected:
        if (operator == 'deep'): original | override
        else: original + override
    with pytest.raises(type(expected.value)) as raised:
        if (operator == 'deep'): value |= override
        else: value += override
    assert str(raised.value) == str(expected.value).replace(
        "unsupported operand type(s) for +:",
        "unsupported operand type(s) for +=:",
    )
    assert value is original
    assert original == {'value': {'number': 1}}


@pytest.mark.parametrize(
    ('baseValue', 'overrideValue'),
    [(1, True), (1, 1.0), (MUT.NamedDict(number=1), {'number': 2}),
     ([1], (2,)),
     (MUT.NamedDict(number=1), MUT.NamedDict(number='bad'))],
)
def test_AugmentedOr_RejectsExactTypeConflictsWithoutMutation(
    baseValue: object, overrideValue: object,
) -> None:
    original = MUT.NamedDict(value=baseValue)
    alias = original
    override = MUT.NamedDict(value=overrideValue)
    before = repr(original)
    overrideBefore = repr(override)
    with pytest.raises(TypeError) as expected: original | override
    with pytest.raises(TypeError) as raised: original |= override
    assert str(raised.value) == str(expected.value)
    assert str(raised.value).startswith('NamedDict deep-merge')
    assert original is alias and repr(original) == before
    assert repr(override) == overrideBefore


def test_Init_PreservesGeneratorEntries() -> None:
    source = (pair for pair in [('first', 1), ('second', 2)])

    assert MUT.NamedDict(source) == {'first': 1, 'second': 2}
    assert list(source) == []


def test_Update_PreservesGeneratorEntriesAndKeywordOverrides() -> None:
    value = MUT.NamedDict(preserved=0, first=-1)
    source = (pair for pair in [('first', 1), ('second', 2)])

    value.___update___(source, second=3)

    assert value == {'preserved': 0, 'first': 1, 'second': 3}
    assert list(source) == []


@pytest.mark.parametrize('method', ['__init__', '___update___'])
def test_InitAndUpdate_IterateInputOnce(method: str) -> None:
    iterations = []

    class Pairs:
        def __iter__(self) -> Iterator[tuple[str, int]]:
            iterations.append('iterated')
            yield from [('first', 1), ('second', 2)]

    value = MUT.NamedDict()
    getattr(value, method)(Pairs())

    assert value == {'first': 1, 'second': 2}
    assert iterations == ['iterated']


@pytest.mark.parametrize('method', ['__init__', '___update___'])
@pytest.mark.parametrize('invalidKey', ['not-valid', 1])
def test_InitAndUpdate_RejectInvalidKeysBeforeMutation(
    method: str,
    invalidKey: object,
) -> None:
    value = MUT.NamedDict(preserved=0)
    source = (
        pair for pair in
        [('new', 1), ('___update___', 2), (invalidKey, 3)]
    )

    with pytest.raises(KeyError, match='valid python identifier'):
        getattr(value, method)(source)

    assert value == {'preserved': 0}


@pytest.mark.parametrize('source', [{'first': 1}, [('first', 1)]])
def test_InitAndUpdate_PreserveInputsAndKeywordPrecedence(
    source: object,
) -> None:
    value = MUT.NamedDict(source, first=2, second=3)
    assert value == {'first': 2, 'second': 3}

    value.___update___(source, first=4)
    assert value == {'first': 4, 'second': 3}


def test_InitAndUpdate_PreserveReservedIdentifierAcceptance() -> None:
    value = MUT.NamedDict([('___update___', 1)])
    value.___update___([('__class__', 2)])

    assert value == {'___update___': 1, '__class__': 2}


@pytest.mark.parametrize('method', ['__init__', '___update___'])
@pytest.mark.parametrize(
    ('source', 'error'),
    [(None, TypeError), ([('valid', 1), ('broken',)], ValueError)],
)
def test_InitAndUpdate_PreserveNativeInputErrors(
    method: str,
    source: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        getattr(MUT.NamedDict(), method)(source)


def test_GetAttribute_AttributeAccessUpdateAndKnownFromkeysLimitation(
) -> None:
    '''Characterize the current fromkeys/plain-method name limitation.'''
    value = MUT.NamedDict(answer=42)

    assert value.answer == 42
    assert value.get('answer') == 42
    assert list(value.___items___()) == [('answer', 42)]
    assert list(value.___keys___()) == ['answer']
    assert list(value.___values___()) == [42]
    assert value.___copy___() == {'answer': 42}
    with pytest.raises(AttributeError, match='Can not set'):
        value.___fromkeys___(['new'], 3)
    assert isinstance(value.__class__, type)
    assert isinstance(value.__dict__, dict)
    with pytest.raises(AttributeError, match='Can not get'):
        _ = value.missing

    value.___update___({'second': 2})
    assert value.second == 2
    with pytest.raises(AttributeError, match='Can not set'):
        value.second = 3
    with pytest.raises(AttributeError, match='Can not set'):
        value['second'] = 3


@pytest.mark.parametrize('source', [{'not-valid': 1}, [(1, 'value')]])
def test_CheckKeys_RejectsInvalidIdentifiers(source: object) -> None:
    with pytest.raises(KeyError, match='valid python identifier'):
        MUT.NamedDict(source)


def test_CheckKeys_RejectsInvalidKeywordIdentifiers() -> None:
    with pytest.raises(KeyError, match='valid python identifier'):
        MUT.NamedDict().____check_keys____(**{'not-valid': 1})


def test_CheckKeys_AcceptsMappingsIterablesAndKeywords() -> None:
    assert MUT.NamedDict({'valid': 1}) == {'valid': 1}
    candidate = MUT.NamedDict()
    candidate.____check_keys____([('first', 1), (), 3])
    candidate.____check_keys____(3)
    assert MUT.NamedDict(valid_name=1) == {'valid_name': 1}
    with pytest.raises(KeyError, match='valid python identifier'):
        MUT.NamedDict().___update___({'also-invalid': 1})


def test_OperatorAddAndOperatorReverseAdd_ShallowMergePreservesNamedDictType(
) -> None:
    base = MUT.NamedDict(
        nested=MUT.NamedDict(left=1),
        scalar=1,
        preserved='default',
    )

    forward = base + {'right': 2}
    reflected = {'first': 0} + base

    assert type(forward) is MUT.NamedDict
    assert type(forward.nested) is MUT.NamedDict
    assert forward == {
        'nested': MUT.NamedDict(left=1),
        'scalar': 1,
        'preserved': 'default',
        'right': 2,
    }
    assert type(reflected) is MUT.NamedDict
    assert reflected == {'first': 0, **base}
    assert MUT.NamedDict.__add__(base, []) is NotImplemented
    assert MUT.NamedDict.__radd__(base, []) is NotImplemented


def test_OperatorOr_DeepMergePreservesExactTypesAndInputs() -> None:
    base = MUT.NamedDict(
        nested=MUT.NamedDict(left=1),
        scalar=1,
        preserved='default',
    )
    override = MUT.NamedDict(
        nested=MUT.NamedDict(right=2),
        scalar=2,
        new=4,
    )
    baseBefore = MUT.NamedDict(
        nested=MUT.NamedDict(left=1),
        scalar=1,
        preserved='default',
    )
    overrideBefore = MUT.NamedDict(
        nested=MUT.NamedDict(right=2),
        scalar=2,
        new=4,
    )

    merged = base | override

    assert type(merged) is MUT.NamedDict
    assert type(merged.nested) is MUT.NamedDict
    assert type(merged.scalar) is int
    assert merged == {
        'nested': MUT.NamedDict(left=1, right=2),
        'scalar': 2,
        'preserved': 'default',
        'new': 4,
    }
    assert base == baseBefore
    assert override == overrideBefore
    assert type(base.nested) is MUT.NamedDict
    assert type(override.nested) is MUT.NamedDict


def test__DeepMerge_PlainDictsPreserveExactTypesAndInputs() -> None:
    base = {'nested': {'left': 1}, 'preserved': 'default'}
    override = {'nested': {'right': 2}, 'new': 4}
    baseBefore = deepcopy(base)
    overrideBefore = deepcopy(override)

    merged = MUT.NamedDict._DeepMerge(base, override)

    assert type(merged) is dict
    assert type(merged['nested']) is dict
    assert merged == {
        'nested': {'left': 1, 'right': 2},
        'preserved': 'default',
        'new': 4,
    }
    assert base == baseBefore
    assert override == overrideBefore


def test_OperatorOrAndOperatorReverseOr_RejectMixedNestedMappingTypes(
) -> None:
    with pytest.raises(
        TypeError, match='expected `NamedDict`, received `dict`',
    ):
        MUT.NamedDict(section=MUT.NamedDict(left=1)) | MUT.NamedDict(
            section={'right': 2},
        )
    with pytest.raises(
        TypeError, match='expected `dict`, received `NamedDict`',
    ):
        {'section': {'left': 1}} | MUT.NamedDict(
            section=MUT.NamedDict(right=2),
        )


def test_OperatorReverseOr_PlainOrNamedRetainsPlainNestedType() -> None:
    plainBase = {'nested': {'left': 1}, 'preserved': 'default'}

    reflected = plainBase | MUT.NamedDict(nested={'right': 2}, new=4)

    assert type(reflected) is MUT.NamedDict
    assert type(reflected.nested) is dict
    assert reflected == {
        'nested': {'left': 1, 'right': 2},
        'preserved': 'default',
        'new': 4,
    }


def test_OperatorOrAndOperatorReverseOr_RejectsInvalidOperands() -> None:
    base = MUT.NamedDict(value=1)

    with pytest.raises(
        TypeError,
        match=(
            r'^NamedDict deep-merge requires a `NamedDict` right operand, '
            r'not `dict`\.$'
        ),
    ):
        base | {'new': 4}
    assert MUT.NamedDict.__ror__(base, []) is NotImplemented


def test_OperatorOr_RejectPlainNestedConfigBeforeLosingDefaults() -> None:
    base = MUT.NamedDict(
        cliCfg=MUT.NamedDict(
            ocCfg={'default': True},
            vcCfg={'default': True},
        ),
    )
    plainCliCfg = {'ocCfg': {'context': 'override'}}

    with pytest.raises(TypeError) as error:
        base | MUT.NamedDict(cliCfg=plainCliCfg)

    assert str(error.value) == (
        "NamedDict deep-merge at `['cliCfg']`: expected `NamedDict`, "
        'received `dict`.'
    )
    assert base.cliCfg.vcCfg == {'default': True}


@pytest.mark.parametrize(
    ('baseValue', 'overrideValue'),
    [
        (1, 'replacement'), ('default', 2),
        (True, 1), (1, True), (1, 1.0), (1.0, 1),
        ({'left': 1}, 2), (2, {'right': 1}),
        (MUT.NamedDict(left=1), 2), (2, MUT.NamedDict(right=1)),
        ({'left': 1}, MUT.NamedDict(right=1)),
        (MUT.NamedDict(left=1), {'right': 1}),
        ([1], (2,)), ((1,), [2]),
    ],
)
def test__DeepMerge_RejectTypeMismatchWithFullPathAndUnchangedInputs(
    baseValue: object,
    overrideValue: object,
) -> None:
    base = MUT.NamedDict(outer={'non-identifier key': {7: baseValue}})
    override = MUT.NamedDict(
        outer={'non-identifier key': {7: overrideValue}},
    )
    baseData = dict(baseValue) if isinstance(baseValue, dict) else baseValue
    overrideData = (
        dict(overrideValue)
        if isinstance(overrideValue, dict) else overrideValue
    )
    baseBefore = {'outer': {'non-identifier key': {7: deepcopy(baseData)}}}
    overrideBefore = {
        'outer': {'non-identifier key': {7: deepcopy(overrideData)}},
    }

    with pytest.raises(TypeError) as error:
        base | override

    assert str(error.value) == (
        "NamedDict deep-merge at `['outer']['non-identifier key'][7]`: "
        f'expected `{type(baseValue).__name__}`, '
        f'received `{type(overrideValue).__name__}`.'
    )
    assert base == baseBefore
    assert override == overrideBefore


def test__DeepMerge_ErrorDoesNotExposeValuesOrOtherSettings() -> None:
    base = MUT.NamedDict(
        outer={'credential': 'base-sensitive-marker'},
        unrelated='unrelated-sensitive-marker',
    )
    override = MUT.NamedDict(
        outer={'credential': ['override-sensitive-marker']},
    )

    with pytest.raises(TypeError) as error:
        base | override

    assert str(error.value) == (
        "NamedDict deep-merge at `['outer']['credential']`: "
        'expected `str`, received `list`.'
    )
    assert 'sensitive-marker' not in str(error.value)


@pytest.mark.parametrize(
    ('key', 'keyRepr'),
    [
        (7, '7'),
        ('non-identifier key', "'non-identifier key'"),
        ("single'quote", '"single\'quote"'),
        ('double"quote', "'double\"quote'"),
        ('back\\slash', r"'back\\slash'"),
        ('line\nbreak', r"'line\nbreak'"),
        ('controls\r\t\0\x1b\b\f\v',
         r"'controls\r\t\x00\x1b\x08\x0c\x0b'"),
        ('tick`key', "'tick`key'"),
        ('', "''"),
    ],
)
def test__DeepMerge_EscapesKeyPathsAndKeepsValuesPrivate(
    key: object,
    keyRepr: str,
) -> None:
    base = MUT.NamedDict(
        outer={key: {'value': 'base-sensitive-marker'}},
        unrelated='unrelated-sensitive-marker',
    )
    override = MUT.NamedDict(
        outer={key: {'value': ['override-sensitive-marker']}},
    )

    with pytest.raises(TypeError) as error:
        base | override

    message = str(error.value)
    assert message == (
        f"NamedDict deep-merge at `['outer'][{keyRepr}]['value']`: "
        'expected `str`, received `list`.'
    )
    assert all(char not in message for char in '\n\r\t\0\x1b\b\f\v')
    assert 'sensitive-marker' not in message
    assert base.outer[key]['value'] == 'base-sensitive-marker'
    assert override.outer[key]['value'] == ['override-sensitive-marker']


@pytest.mark.parametrize('mappingType', [dict, MUT.NamedDict])
def test__DeepMerge_RecursesCopiesAndPreservesOrdinaryDataAndDefaults(
    mappingType: type,
) -> None:
    base = mappingType(
        nested={'non-identifier key': {7: [1], 'keep': True}},
        untouched=['default'],
        optional=None,
    )
    newValue = MUT.NamedDict(new={'raw-key': []})
    extension = [2, 3]
    override = mappingType(
        nested={'non-identifier key': {7: extension, 'new key': newValue}},
        optional=None,
    )
    baseBefore = deepcopy(dict(base))
    overrideBefore = {
        'nested': {
            'non-identifier key': {
                7: [2, 3], 'new key': {'new': {'raw-key': []}},
            },
        },
        'optional': None,
    }

    merged = MUT.NamedDict._DeepMerge(base, override)

    assert type(merged) is mappingType
    assert type(merged['nested']) is dict
    assert merged['nested']['non-identifier key'] == {
        7: [1, 2, 3], 'keep': True, 'new key': newValue,
    }
    assert merged['optional'] is None
    assert merged['untouched'] is base['untouched']
    assert merged['nested']['non-identifier key'][7] is not extension
    assert (
        merged['nested']['non-identifier key'][7]
        is not base['nested']['non-identifier key'][7]
    )
    assert merged['nested']['non-identifier key']['new key'] is newValue
    assert merged is not base
    assert merged['nested'] is not base['nested']
    assert merged['nested'] is not override['nested']
    merged['nested']['non-identifier key']['keep'] = False
    assert base == baseBefore
    assert override == overrideBefore


@pytest.mark.parametrize(
    ('baseValue', 'overrideValue'),
    [(True, False), (1, 2), (1.0, 2.0), ('old', 'new'),
     ((1,), (2,)), ({1}, {2}), (UserList([1]), UserList([2])),
     (None, None)],
)
def test__DeepMerge_ReplacesMatchingNonMappingsWithoutConversion(
    baseValue: object,
    overrideValue: object,
) -> None:
    base = MUT.NamedDict(nested=MUT.NamedDict(value=baseValue))
    override = MUT.NamedDict(nested=MUT.NamedDict(value=overrideValue))

    merged = base | override

    assert type(merged.nested) is MUT.NamedDict
    assert merged.nested.value is overrideValue
    assert base.nested.value is baseValue
    assert override.nested.value is overrideValue


def test__DeepMerge_RejectsSubclassAndReplacesMatchingCustomMappings() -> None:
    class Data(dict):
        pass

    with pytest.raises(TypeError) as error:
        MUT.NamedDict(data={}) | MUT.NamedDict(data=Data())
    assert str(error.value) == (
        "NamedDict deep-merge at `['data']`: expected `dict`, received `Data`."
    )

    replacement = Data(right=2)
    base = MUT.NamedDict(data=Data(left=1))
    merged = base | MUT.NamedDict(data=replacement)

    assert merged.data is replacement
    assert base.data == {'left': 1}


def test__DeepMerge_LateMismatchLeavesEarlierBranchesUnchanged() -> None:
    base = MUT.NamedDict(first={'value': 1}, items=[1], last='default')
    override = MUT.NamedDict(first={'value': 2}, items=[2], last=3)

    with pytest.raises(TypeError, match=r"at `\['last'\]`"):
        base | override

    assert base == {'first': {'value': 1}, 'items': [1], 'last': 'default'}
    assert override == {'first': {'value': 2}, 'items': [2], 'last': 3}


@pytest.mark.parametrize('mappingType', [dict, MUT.NamedDict])
@pytest.mark.parametrize(
    ('baseItems', 'overrideItems', 'expected'),
    [([], [], []), ([], [2], [2]), ([1], [], [1]),
     ([1, 2], [2, 3], [1, 2, 2, 3])],
)
def test__DeepMerge_ConcatenatesNestedListsWithoutMutatingInputs(
    mappingType: type,
    baseItems: list,
    overrideItems: list,
    expected: list,
) -> None:
    base = mappingType(section=mappingType(items=baseItems))
    override = mappingType(section=mappingType(items=overrideItems))
    baseBefore = {'section': {'items': deepcopy(baseItems)}}
    overrideBefore = {'section': {'items': deepcopy(overrideItems)}}

    merged = MUT.NamedDict._DeepMerge(base, override)
    items = merged['section']['items']

    assert type(items) is list
    assert items == expected
    assert items is not baseItems
    assert items is not overrideItems
    items.append('result-only')
    assert base == baseBefore
    assert override == overrideBefore


def test__DeepMerge_PreservesShallowListItemIdentityWithoutIndexMerge(
) -> None:
    baseChild = ['base']
    overrideChild = ['override']
    baseMapping = {'left': 1}
    overrideMapping = {'right': 2}
    baseItems = [baseChild, baseMapping, baseChild]
    overrideItems = [overrideChild, overrideMapping, baseChild]
    base = MUT.NamedDict(items=baseItems)
    override = MUT.NamedDict(items=overrideItems)

    merged = base | override

    assert merged.items == baseItems + overrideItems
    for (index, item) in enumerate(baseItems + overrideItems):
        assert merged.items[index] is item
    assert merged.items is not baseItems
    assert merged.items is not overrideItems
    assert baseChild == ['base']
    assert overrideChild == ['override']
    assert baseMapping == {'left': 1}
    assert overrideMapping == {'right': 2}


def test_OperatorOr_RepeatedListMergesPreserveDefaultsAndCallerLists() -> None:
    defaults = MUT.NamedDict(section=MUT.NamedDict(items=['default']))
    callerItems = ['caller', 'caller']
    override = MUT.NamedDict(section=MUT.NamedDict(items=callerItems))

    first = defaults | override
    second = first | override
    fresh = defaults | override

    assert first.section.items == ['default', 'caller', 'caller']
    assert second.section.items == [
        'default', 'caller', 'caller', 'caller', 'caller',
    ]
    assert fresh.section.items == first.section.items
    assert fresh.section.items is not first.section.items
    assert second.section.items is not first.section.items
    assert defaults.section.items == ['default']
    assert callerItems == ['caller', 'caller']
    assert override.section.items is callerItems


def test_OperatorReverseOr_ConcatenatesListsAndKeepsNewListIdentity() -> None:
    baseItems = [1]
    overrideItems = [1, 2]
    newItems = [[]]
    base = {'items': baseItems}
    override = MUT.NamedDict(items=overrideItems, newItems=newItems)

    merged = base | override

    assert merged.items == [1, 1, 2]
    assert merged.items is not baseItems
    assert merged.items is not overrideItems
    assert merged.newItems is newItems
    assert base == {'items': [1]}
    assert override.items is overrideItems
    assert overrideItems == [1, 2]


def test__DeepMerge_RejectsListSubclassButReplacesMatchingSubclass() -> None:
    class Items(list):
        pass

    with pytest.raises(TypeError) as error:
        MUT.NamedDict(section={'items': []}) | MUT.NamedDict(
            section={'items': Items([2])},
        )
    assert str(error.value) == (
        "NamedDict deep-merge at `['section']['items']`: "
        'expected `list`, received `Items`.'
    )
    baseItems = Items([1])
    overrideItems = Items([2])

    merged = MUT.NamedDict(items=baseItems) | MUT.NamedDict(
        items=overrideItems,
    )

    assert merged.items is overrideItems
    assert baseItems == [1]
    assert overrideItems == [2]


def test_InitAndRepr_NestedDefDictCreatesChildrenAndHasPlainRepresentation(
) -> None:
    nested = MUT.NestedDefDict()
    nested['outer']['inner'] = 3

    assert isinstance(nested['outer'], MUT.NestedDefDict)
    assert repr(nested) == "{'outer': {'inner': 3}}"


@pytest.mark.parametrize(
    ('args', 'kwargs', 'expected'),
    [
        ((), {}, {}),
        (({'first': 1},), {}, {'first': 1}),
        (([('first', 1)],), {}, {'first': 1}),
        ((), {'first': 1}, {'first': 1}),
        (({'first': 1},), {'first': 2}, {'first': 2}),
        ((MUT.NestedDefDict,), {}, {}),
        ((MUT.NestedDefDict, {'first': 1}), {}, {'first': 1}),
    ],
)
def test_Init_NestedDefDictPreservesConstructionAndFixedFactory(
    args: tuple[object, ...],
    kwargs: dict[str, object],
    expected: dict[str, object],
) -> None:
    nested = MUT.NestedDefDict(*args, **kwargs)

    assert nested == expected
    assert repr(nested) == repr(expected)
    assert nested.default_factory is MUT.NestedDefDict
    assert type(nested['missing']) is MUT.NestedDefDict
    assert nested['missing'].default_factory is MUT.NestedDefDict
    assert nested['missing'] == {}


@pytest.mark.parametrize(
    'copyMethod', [MUT.NestedDefDict.copy, copy], ids=['method', 'stdlib'],
)
@pytest.mark.parametrize('populated', [False, True])
def test_copy_NestedDefDictSeparatesOuterAndSharesChildren(
    copyMethod: Callable[[MUT.NestedDefDict], MUT.NestedDefDict],
    populated: bool,
) -> None:
    nested = MUT.NestedDefDict()
    if populated:
        nested['child']['values'] = [1]

    copied = copyMethod(nested)

    assert type(copied) is MUT.NestedDefDict
    assert copied is not nested
    assert copied == nested
    assert copied.default_factory is MUT.NestedDefDict
    if populated:
        assert copied['child'] is nested['child']
        copied['child']['values'].append(2)
        assert nested['child']['values'] == [1, 2]
    copied['missing']['leaf'] = 3
    assert type(copied['missing']) is MUT.NestedDefDict
    assert 'missing' not in nested


@pytest.mark.parametrize('populated', [False, True])
def test_deepcopy_NestedDefDictSeparatesOuterAndChildren(
    populated: bool,
) -> None:
    nested = MUT.NestedDefDict()
    if populated:
        nested['child']['values'] = [1]

    copied = deepcopy(nested)

    assert type(copied) is MUT.NestedDefDict
    assert copied is not nested
    assert copied == nested
    assert copied.default_factory is MUT.NestedDefDict
    if populated:
        assert copied['child'] is not nested['child']
        assert copied['child']['values'] is not nested['child']['values']
        assert copied['child'].default_factory is MUT.NestedDefDict
        copied['child']['values'].append(2)
        assert nested['child']['values'] == [1]
    copied['missing']['leaf'] = 3
    assert type(copied['missing']) is MUT.NestedDefDict
    assert 'missing' not in nested


def test_deepcopy_NestedDefDictPreservesCyclesAliasesAndMemo() -> None:
    nested = MUT.NestedDefDict()
    child = nested['child']
    child['values'] = [1]
    child['parent'] = nested
    nested['alias'] = child
    nested['self'] = nested
    memo = {}

    copied = deepcopy(nested, memo)

    assert type(copied) is MUT.NestedDefDict
    assert copied is not nested
    assert copied['self'] is copied
    assert copied['child']['parent'] is copied
    assert copied['child'] is copied['alias']
    assert copied['child'] is not child
    assert copied['child']['values'] is not child['values']
    assert memo[id(nested)] is copied
    assert memo[id(child)] is copied['child']
    assert deepcopy(nested, memo) is copied


@pytest.mark.parametrize('value', [
    None, False, True, 0, 2.5, 'value', [1, 1], {'child': [2]},
    MUT.NamedDict(child=[3]),
])
@pytest.mark.parametrize('toNone', [False, True])
@pytest.mark.parametrize('depth', [0, 3])
def test__DeepMerge_NullableReplacementPreservesTypesAndInputs(
    value: object, toNone: bool, depth: int,
) -> None:
    (left, right) = (value, None) if (toNone) else (None, value)
    base = {'value': left, 'retained': [1]}
    override = {'value': right}
    for level in range(depth):
        base = {'nested': base}
        override = {'nested': override}
    beforeBase = repr(base)
    beforeOverride = repr(override)
    for merged in (
        MUT.NamedDict._DeepMerge(base, override),
        MUT.NamedDict(base) | MUT.NamedDict(override),
        base | MUT.NamedDict(override),
    ):
        for level in range(depth): merged = merged['nested']
        assert merged['value'] is right
        assert merged['retained'] == [1]
    assert repr(base) == beforeBase
    assert repr(override) == beforeOverride


def test_Call_SingletonMetaReusesFirstInstance() -> None:
    class Example(metaclass=MUT.SingletonMeta):
        def __init__(self, value: int) -> None:
            self.value = value

    first = Example(1)
    second = Example(2)

    assert first is second
    assert second.value == 1
