from copy import deepcopy

import pytest

from libs.CustomTypes import NamedDict, NestedDict, SingletonMeta


def test_GetAttribute_AttributeAccessUpdateAndKnownFromkeysLimitation() -> None:
    '''Characterize the current fromkeys/plain-method name limitation.'''
    value = NamedDict(answer=42)

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
        NamedDict(source)


def test_CheckKeys_AcceptsMappingsIterablesAndKeywords() -> None:
    assert NamedDict({'valid': 1}) == {'valid': 1}
    candidate = NamedDict()
    candidate.____check_keys____([('first', 1), (), 3])
    candidate.____check_keys____(3)
    assert NamedDict(valid_name=1) == {'valid_name': 1}
    with pytest.raises(KeyError, match='valid python identifier'):
        NamedDict().___update___({'also-invalid': 1})


def test_OperatorAddAndOperatorReverseAdd_ShallowMergePreservesNamedDictType() -> None:
    base = NamedDict(
        nested=NamedDict(left=1),
        scalar=1,
        preserved='default',
    )

    forward = base + {'right': 2}
    reflected = {'first': 0} + base

    assert type(forward) is NamedDict
    assert type(forward.nested) is NamedDict
    assert forward == {
        'nested': NamedDict(left=1),
        'scalar': 1,
        'preserved': 'default',
        'right': 2,
    }
    assert type(reflected) is NamedDict
    assert reflected == {'first': 0, **base}
    assert NamedDict.__add__(base, []) is NotImplemented
    assert NamedDict.__radd__(base, []) is NotImplemented


def test_OperatorOr_DeepMergePreservesExactTypesAndInputs() -> None:
    base = NamedDict(
        nested=NamedDict(left=1),
        scalar=1,
        preserved='default',
    )
    override = NamedDict(
        nested=NamedDict(right=2),
        scalar=NamedDict(replacement=True),
        new=4,
    )
    baseBefore = NamedDict(
        nested=NamedDict(left=1),
        scalar=1,
        preserved='default',
    )
    overrideBefore = NamedDict(
        nested=NamedDict(right=2),
        scalar=NamedDict(replacement=True),
        new=4,
    )

    merged = base | override

    assert type(merged) is NamedDict
    assert type(merged.nested) is NamedDict
    assert type(merged.scalar) is NamedDict
    assert merged == {
        'nested': NamedDict(left=1, right=2),
        'scalar': NamedDict(replacement=True),
        'preserved': 'default',
        'new': 4,
    }
    assert base == baseBefore
    assert override == overrideBefore
    assert type(base.nested) is NamedDict
    assert type(override.nested) is NamedDict


def test__DeepMerge_PlainDictsPreserveExactTypesAndInputs() -> None:
    '''Characterize the private helper behavior needed for branch coverage.'''
    base = {'nested': {'left': 1}, 'preserved': 'default'}
    override = {'nested': {'right': 2}, 'new': 4}
    baseBefore = deepcopy(base)
    overrideBefore = deepcopy(override)

    merged = NamedDict._DeepMerge(base, override)

    assert type(merged) is dict
    assert type(merged['nested']) is dict
    assert merged == {
        'nested': {'left': 1, 'right': 2},
        'preserved': 'default',
        'new': 4,
    }
    assert base == baseBefore
    assert override == overrideBefore


def test_OperatorOrAndOperatorReverseOr_MixedTypesReplaceRatherThanCoerce() -> None:
    plainRHS = {'right': 2}
    mixedForward = NamedDict(section=NamedDict(left=1)) | NamedDict(
        section=plainRHS,
    )
    namedRHS = NamedDict(right=2)
    mixedReflected = {'section': {'left': 1}} | NamedDict(section=namedRHS)

    assert mixedForward.section is plainRHS
    assert type(mixedForward.section) is dict
    assert mixedForward.section == {'right': 2}
    assert type(mixedReflected) is NamedDict
    assert mixedReflected.section is namedRHS
    assert type(mixedReflected.section) is NamedDict


def test_OperatorReverseOr_PlainOrNamedRetainsPlainNestedType() -> None:
    plainBase = {'nested': {'left': 1}, 'preserved': 'default'}

    reflected = plainBase | NamedDict(nested={'right': 2}, new=4)

    assert type(reflected) is NamedDict
    assert type(reflected.nested) is dict
    assert reflected == {
        'nested': {'left': 1, 'right': 2},
        'preserved': 'default',
        'new': 4,
    }


def test_OperatorOrAndOperatorReverseOr_RejectsInvalidOperands() -> None:
    base = NamedDict(value=1)

    with pytest.raises(
        TypeError,
        match=(
            r'^NamedDict deep-merge requires a `NamedDict` right operand, '
            r'not `dict`\.$'
        ),
    ):
        base | {'new': 4}
    assert NamedDict.__ror__(base, []) is NotImplemented


def test_OperatorOr_PlainNestedConfigKnownDotAccessLimitation() -> None:
    '''Characterize the known plain-nested-config limitation.'''
    base = NamedDict(
        cliCfg=NamedDict(
            ocCfg={'default': True},
            vcCfg={'default': True},
        ),
    )
    plainCliCfg = {'ocCfg': {'context': 'override'}}

    cfg = base | NamedDict(cliCfg=plainCliCfg)

    assert cfg.cliCfg is plainCliCfg
    assert type(cfg.cliCfg) is dict
    assert cfg.cliCfg == {'ocCfg': {'context': 'override'}}
    assert 'vcCfg' not in cfg.cliCfg
    assert 'default' not in cfg.cliCfg['ocCfg']
    with pytest.raises(
        AttributeError,
        match=r"^'dict' object has no attribute 'ocCfg'$",
    ):
        _ = cfg.cliCfg.ocCfg


def test_InitAndRepr_NestedDictCreatesChildrenAndHasPlainRepresentation() -> None:
    nested = NestedDict()
    nested['outer']['inner'] = 3

    assert isinstance(nested['outer'], NestedDict)
    assert repr(nested) == "{'outer': {'inner': 3}}"


def test_Call_SingletonMetaReusesFirstInstance() -> None:
    class Example(metaclass=SingletonMeta):
        def __init__(self, value: int) -> None:
            self.value = value

    first = Example(1)
    second = Example(2)

    assert first is second
    assert second.value == 1
