import copy
from dataclasses import FrozenInstanceError
import json
from types import SimpleNamespace
from unittest import mock

import pytest
import yaml

import libs.OpenShift.OCP.Resources as MUT


def test_ResCfg_DefaultsAreFreshAndBindingsFrozen() -> None:
    namedDictInit = mock.Mock(return_value=None)
    with mock.patch.object(MUT.NamedDict, '__init__', namedDictInit):
        (first, second) = MUT.ResCfg(), MUT.ResCfg()

    assert (namedDictInit.call_count == 4)
    assert (type(first.meta) is type(first.spec) is MUT.NamedDict)
    assert (first.meta is not second.meta)
    assert (first.spec is not second.spec)
    assert (first.meta is not first.spec)
    assert (first.yaml is None)
    dict.__setitem__(first.meta, 'name', 'one')
    assert (second.meta == {})
    with pytest.raises(FrozenInstanceError): first.spec = {}


def test_OwnInf_RequiresSharedMutableListAndFreezesBindings() -> None:
    with pytest.raises(TypeError): MUT.OwnInf()
    owned = []
    info = MUT.OwnInf(owned=owned)
    assert ((info.owned is owned) and (info.existResDelTO == '5m'))
    owned.append(('dummy-client', 'Secret/one', 'uid'))
    assert (len(info.owned) == 1)
    with pytest.raises(FrozenInstanceError): info.owned = []


# Native (optional, mandatory) key sets used by the `_ValResCfg` tests.
vldMeta = ({'namespace'}, {'name'})
vldSpec = ({'type'}, {'data'})


@pytest.mark.parametrize('meta,spec,vld', [
    # Mandatory keys only.
    ({'name': 'one'}, {'data': {}}, (vldMeta, vldSpec)),
    # Mandatory and optional keys.
    (
        {'name': 'one', 'namespace': 'ns'}, {'data': {}, 'type': 'Opaque'},
        (vldMeta, vldSpec),
    ),
    # Presence only: `None` and empty values count as present.
    ({'name': None}, {'data': ''}, (vldMeta, vldSpec)),
    # Optional keys only, nothing mandatory.
    ({'namespace': 'ns'}, {}, (({'namespace'}, set()), ({'type'}, set()))),
    # Nothing supplied and nothing mandatory.
    ({}, {}, ((set(), set()), (set(), set()))),
])
def test__ValResCfg_AcceptsAllowedNativeKeys(
    meta: dict[str, object],
    spec: dict[str, object],
    vld: tuple,
) -> None:
    cfg = MUT.ResCfg(meta=meta, spec=spec)
    assert (MUT._ValResCfg(cfg, *vld, 'Secret') is None)


# Each input has exactly one violation.
@pytest.mark.parametrize('meta,spec,message', [
    ({}, {'data': {}}, "Missing mandatory `resCfg.meta` keys: {'name'}"),
    ({'name': 'one'}, {}, "Missing mandatory `resCfg.spec` keys: {'data'}"),
    (
        {'name': 'one', 'unsupported': True}, {'data': {}},
        "Unsupported `resCfg.meta` keys found: {'unsupported'}",
    ),
    (
        {'name': 'one'}, {'data': {}, 'unsupported': True},
        "Unsupported `resCfg.spec` keys found: {'unsupported'}",
    ),
])
def test__ValResCfg_RejectsInvalidNativeKeys(
    meta: dict[str, object],
    spec: dict[str, object],
    message: str,
) -> None:
    cfg = MUT.ResCfg(meta=meta, spec=spec)

    with pytest.raises(ValueError) as caught:
        MUT._ValResCfg(cfg, vldMeta, vldSpec, 'Secret')

    assert (str(caught.value) == message)


# Each input has several violations.
@pytest.mark.parametrize('meta,spec', [
    ({'name': 'one', 'unsupported': True}, {}),
    ({}, {'data': {}, 'unsupported': True}),
    ({}, {}),
    ({'name': 'one', 'bad': 1}, {'data': {}, 'worse': 2}),
])
def test__ValResCfg_RejectsMultipleInvalidNativeKeys(
    meta: dict[str, object],
    spec: dict[str, object],
) -> None:
    cfg = MUT.ResCfg(meta=meta, spec=spec)

    with pytest.raises(ValueError):
        MUT._ValResCfg(cfg, vldMeta, vldSpec, 'Secret')


def test__ValResCfg_LeavesKeySetsUnchanged() -> None:
    (meta, spec) = (({'namespace'}, {'name'}), ({'type'}, {'data'}))
    before = copy.deepcopy((meta, spec))
    valid = MUT.ResCfg(meta={'name': 'one'}, spec={'data': {}})
    invalid = MUT.ResCfg(meta={'bad': 1}, spec={'worse': 2})

    assert (MUT._ValResCfg(valid, meta, spec, 'Secret') is None)
    with pytest.raises(ValueError):
        MUT._ValResCfg(invalid, meta, spec, 'Secret')
    assert ((meta, spec) == before)


def test__ValResCfg_YAMLIgnoresInvalidNativeAndChecksOnlyKind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = 'kind: Secret\n'
    parser = mock.Mock(return_value={'kind': 'Secret'})
    monkeypatch.setattr(MUT, 'yaml', SimpleNamespace(safe_load=parser))
    # Authoritative YAML bypasses even missing mandatory native keys.
    cfg = MUT.ResCfg(meta=None, spec=object(), yaml=text)

    assert (MUT._ValResCfg(cfg, vldMeta, vldSpec, 'Secret') is None)
    parser.assert_called_once_with(text)


@pytest.mark.parametrize('text,parsed,error', [
    ('kind: Namespace', {'kind': 'Namespace'}, ValueError),
    ('metadata: {}', {'metadata': {}}, KeyError),
    ('[]', [], TypeError),
    ('kind: [', yaml.YAMLError('invalid yaml'), yaml.YAMLError),
])
def test__ValResCfg_RejectsInvalidAuthoritativeYAML(
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    parsed: object,
    error: type[Exception],
) -> None:
    parser = mock.Mock()
    if (isinstance(parsed, Exception)): parser.side_effect = parsed
    else: parser.return_value = parsed
    monkeypatch.setattr(MUT, 'yaml', SimpleNamespace(safe_load=parser))
    cfg = MUT.ResCfg(meta=None, spec=None, yaml=text)

    with pytest.raises(error):
        MUT._ValResCfg(cfg, (set(), set()), (set(), set()), 'Secret')
    parser.assert_called_once_with(text)


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('isSecret', [False, True])
def test__ResCreate_DispatchesDefaultsAndPreservesOwnedList(
    resourceCLI: mock.MagicMock,
    tracked: bool,
    isSecret: bool,
) -> None:
    resource = {'kind': 'Secret'}
    producer = {'subCmd': 'secret generic', 'args': ['one']}
    owned = [('caller-client', 'Secret/caller', 'caller-uid')]
    ownInf = MUT.OwnInf(owned=owned, existResDelTO='9m') if tracked else None
    resourceCLI.NewRes.return_value = (
        'APPLIED', SimpleNamespace(success=True), resource,
    )
    resourceCLI.EnsureRes.return_value = resource

    assert (MUT._ResCreate(
        resourceCLI, producer, ownInf, isSecret,
    ) is resource)
    if tracked:
        resourceCLI.EnsureRes.assert_called_once_with(
            producer, owned, isSecret=isSecret, existResDelTO='9m',
            optsNewRes={},
        )
        assert (resourceCLI.EnsureRes.call_args.args[1] is owned)
        resourceCLI.NewRes.assert_not_called()
    else:
        resourceCLI.NewRes.assert_called_once_with(producer, isSecret=isSecret)
        resourceCLI.EnsureRes.assert_not_called()
    assert (owned == [('caller-client', 'Secret/caller', 'caller-uid')])


def test__ResCreate_EmptyOwnedListEnablesTracking(
    resourceCLI: mock.MagicMock,
) -> None:
    owned = []
    resource = {'kind': 'Namespace'}
    resourceCLI.EnsureRes.return_value = resource

    assert (MUT._ResCreate(
        resourceCLI, 'kind: Namespace', MUT.OwnInf(owned),
    ) is resource)
    resourceCLI.EnsureRes.assert_called_once_with(
        'kind: Namespace', owned, isSecret=False, existResDelTO='5m',
        optsNewRes={},
    )
    assert (resourceCLI.EnsureRes.call_args.args[1] is owned)
    resourceCLI.NewRes.assert_not_called()


@pytest.mark.parametrize('tracked', [False, True])
def test__ResCreate_ForwardsOnlyNewResOptions(
    resourceCLI: mock.MagicMock,
    tracked: bool,
) -> None:
    producer = 'kind: Namespace\n'
    options = {
        'waitCond': 'create', 'waitTime': '17s',
        'resYAMLorSubCmd': 'stale-input',
    }
    resource = {'kind': 'Namespace'}
    ownInf = MUT.OwnInf([], existResDelTO='9m') if tracked else None
    resourceCLI.NewRes.return_value = (
        'APPLIED', SimpleNamespace(success=True), resource,
    )
    resourceCLI.EnsureRes.return_value = resource

    assert (MUT._ResCreate(
        resourceCLI, producer, ownInf, True, options,
    ) is resource)
    if tracked:
        resourceCLI.EnsureRes.assert_called_once_with(
            producer, [], isSecret=True, existResDelTO='9m',
            optsNewRes={'waitCond': 'create', 'waitTime': '17s'},
        )
        resourceCLI.NewRes.assert_not_called()
    else:
        resourceCLI.NewRes.assert_called_once_with(
            producer, isSecret=True, waitCond='create', waitTime='17s',
        )
        resourceCLI.EnsureRes.assert_not_called()
    assert (options == {
        'waitCond': 'create', 'waitTime': '17s',
        'resYAMLorSubCmd': 'stale-input',
    })


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('resource', [None, {}, [], 'bad'])
def test__ResCreate_RejectsInvalidReturnedState(
    resourceCLI: mock.MagicMock,
    tracked: bool,
    resource: object,
) -> None:
    resourceCLI.NewRes.return_value = (
        'APPLIED', SimpleNamespace(success=True), resource,
    )
    resourceCLI.EnsureRes.return_value = resource

    with pytest.raises(RuntimeError, match='No valid resource dictionary'):
        MUT._ResCreate(
            resourceCLI, 'kind: Secret',
            MUT.OwnInf([]) if tracked else None,
        )


def test__ResCreate_FailedCommandOmitsPrivateOutput(
    resourceCLI: mock.MagicMock,
) -> None:
    phase = SimpleNamespace(name='DRY_RUN')
    result = SimpleNamespace(
        success=False, stdout='dummy-private-output',
        stderr='dummy-private-error',
    )
    resourceCLI.NewRes.return_value = (phase, result, None)

    with pytest.raises(RuntimeError) as caught:
        MUT._ResCreate(resourceCLI, 'kind: Secret', isSecret=True)

    assert (str(caught.value) == 'Resource creation failed at DRY_RUN.')
    assert ('dummy-private' not in str(caught.value))
    resourceCLI.EnsureRes.assert_not_called()


@pytest.mark.parametrize('tracked', [False, True])
def test__ResCreate_PreservesLifecycleException(
    resourceCLI: mock.MagicMock,
    tracked: bool,
) -> None:
    failure = ValueError('invalid lifecycle state')
    method = resourceCLI.EnsureRes if tracked else resourceCLI.NewRes
    method.side_effect = failure
    ownInf = MUT.OwnInf([]) if tracked else None

    with pytest.raises(ValueError) as caught:
        MUT._ResCreate(resourceCLI, 'kind: Namespace', ownInf)

    assert (caught.value is failure)
    if tracked:
        resourceCLI.EnsureRes.assert_called_once_with(
            'kind: Namespace', [], isSecret=False, existResDelTO='5m',
            optsNewRes={},
        )
        resourceCLI.NewRes.assert_not_called()
    else:
        resourceCLI.NewRes.assert_called_once_with(
            'kind: Namespace', isSecret=False,
        )
        resourceCLI.EnsureRes.assert_not_called()


def ResourceText(uid: str, terminating: bool = False) -> str:
    deletion = '  deletionTimestamp: "2026-01-01T00:00:00Z"\n'
    return (
        f'kind: Pod\nmetadata:\n  uid: {uid}\n'
        f'{deletion if terminating else ""}'
    )


@pytest.mark.parametrize('form', ['yaml', 'json', 'parsed'])
def test_GetUIDs_ListSkipsTerminatingItems(form: str) -> None:
    parsed = {
        'kind': 'List',
        'items': [
            {'metadata': {'uid': 'live-one'}},
            {
                'metadata': {
                    'uid': 'terminating-one',
                    'deletionTimestamp': '2026-01-01T00:00:00Z',
                },
            },
            # Only the exact key presence marks deletion, even when `null`.
            {
                'metadata': {
                    'uid': 'terminating-null', 'deletionTimestamp': None,
                },
            },
            {
                'metadata': {
                    'uid': 'live-two', 'deletionGracePeriodSeconds': 30,
                },
            },
        ],
    }
    before = copy.deepcopy(parsed)
    source = {
        'yaml': yaml.safe_dump(parsed),
        'json': json.dumps(parsed),
        'parsed': parsed,
    }[form]

    assert (MUT.GetUIDs(source) == {'live-one', 'live-two'})
    assert (parsed == before)


@pytest.mark.parametrize(('source', 'expected'), [
    (ResourceText('single-one'), {'single-one'}),
    (ResourceText('single-one', terminating=True), set()),
    (
        'metadata:\n  uid: null-one\n  deletionTimestamp: null\n',
        set(),
    ),
    ({'metadata': {'uid': 'parsed-one'}}, {'parsed-one'}),
    ('kind: List\nitems: []\n', set()),
    ({'items': []}, set()),
    # Non-empty input whose items are all terminating (any key value).
    (
        {
            'items': [
                {'metadata': {'uid': 'gone', 'deletionTimestamp': 0}},
                {'metadata': {'uid': 'going', 'deletionTimestamp': 'now'}},
            ],
        },
        set(),
    ),
])
def test_GetUIDs_HandlesSingleAndEmptyResources(
    source: str | dict,
    expected: set,
) -> None:
    result = MUT.GetUIDs(source)
    assert (type(result) is set)
    assert (result == expected)


@pytest.mark.parametrize(('source', 'match'), [
    ('', 'Expected a resource map, got `NoneType`.'),
    ('plain text', 'Expected a resource map, got `str`.'),
    ('- metadata:\n    uid: one\n', 'Expected a resource map, got `list`.'),
    ([], 'Expected a resource map, got `list`.'),
    ('items: null\n', 'Expected `.items` as a list, got `NoneType`.'),
    ({'items': {}}, 'Expected `.items` as a list, got `dict`.'),
])
def test_GetUIDs_RejectsNonResourceInput(
    source: str | list | dict,
    match: str,
) -> None:
    with pytest.raises(TypeError) as caught:
        MUT.GetUIDs(source)

    assert (str(caught.value) == match)


@pytest.mark.parametrize(('source', 'error'), [
    ('items: [\n', yaml.YAMLError),
    ('kind: Pod\n', KeyError),
    ({'items': [{'metadata': {}}]}, KeyError),
])
def test_GetUIDs_PropagatesMalformedResourceErrors(
    source: str | dict,
    error: type,
) -> None:
    with pytest.raises(error):
        MUT.GetUIDs(source)
