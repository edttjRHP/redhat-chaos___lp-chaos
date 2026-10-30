from dataclasses import FrozenInstanceError
from unittest import mock

import pytest
import yaml

from libs.CustomTypes import NamedDict
from libs.OpenShift.OCP.CLI.OC import OCcli
import libs.OpenShift.OCP.Resources as MUT
from libs.Utils.CmdExec import CmdRes


def test_ResCfg_DefaultsAreFreshAndBindingsFrozen() -> None:
    (first, second) = MUT.ResCfg(), MUT.ResCfg()
    assert (type(first.meta) is type(first.spec) is NamedDict)
    assert (first.meta is not second.meta)
    assert (first.spec is not second.spec)
    assert (first.meta is not first.spec)
    assert (first.yaml is None)
    first.meta.___update___(name='one')
    assert (second.meta == {})
    with pytest.raises(FrozenInstanceError): first.spec = NamedDict()


def test_OwnInf_RequiresSharedMutableListAndFreezesBindings() -> None:
    with pytest.raises(TypeError): MUT.OwnInf()
    owned = []
    info = MUT.OwnInf(owned=owned)
    assert ((info.owned is owned) and (info.existResDelTO == '5m'))
    owned.append(('dummy-client', 'Secret/one', 'uid'))
    assert (len(info.owned) == 1)
    with pytest.raises(FrozenInstanceError): info.owned = []


@pytest.mark.parametrize('meta,spec', [
    (NamedDict(name='one'), NamedDict()),
    (NamedDict(), NamedDict(data={})),
])
def test__ValResCfg_AcceptsAllowedNativeKeys(
    meta: NamedDict, spec: NamedDict,
) -> None:
    assert (MUT._ValResCfg(
        MUT.ResCfg(meta=meta, spec=spec), {'name'}, {'data'}, 'Secret',
    ) is None)


@pytest.mark.parametrize('field', ['meta', 'spec'])
def test__ValResCfg_RejectsUnsupportedNativeKeys(field: str) -> None:
    cfg = MUT.ResCfg(**{field: NamedDict(unsupported=True)})
    with pytest.raises(ValueError, match='Unsupported'):
        MUT._ValResCfg(cfg, {'name'}, {'data'}, 'Secret')


def test__ValResCfg_YAMLIgnoresInvalidNativeAndChecksOnlyKind() -> None:
    cfg = MUT.ResCfg(meta=None, spec=object(), yaml='kind: Secret\n')
    assert (MUT._ValResCfg(cfg, set(), set(), 'Secret') is None)


@pytest.mark.parametrize('text,error', [
    ('kind: Namespace', ValueError),
    ('metadata: {}', KeyError),
    ('[]', TypeError),
    ('kind: [', yaml.YAMLError),
])
def test__ValResCfg_RejectsInvalidAuthoritativeYAML(
    text: str, error: type[Exception],
) -> None:
    with pytest.raises(error):
        MUT._ValResCfg(MUT.ResCfg(yaml=text), set(), set(), 'Secret')


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('isSecret', [False, True])
def test__ResCreate_DispatchesDefaultsAndPreservesOwnedList(
    resourceCLI: mock.MagicMock, tracked: bool, isSecret: bool,
) -> None:
    resource = {'kind': 'Secret'}
    producer = {'subCmd': 'secret generic', 'args': ['one']}
    owned = [('caller-client', 'Secret/caller', 'caller-uid')]
    info = (MUT.OwnInf(owned=owned, existResDelTO='9m') if tracked else None)
    resourceCLI.NewRes.return_value = (
        OCcli.ResPhase.APPLIED, CmdRes('', '', 0, True), resource,
    )
    resourceCLI.EnsureRes.return_value = resource
    assert (MUT._ResCreate(resourceCLI, producer, info, isSecret) is resource)
    if tracked:
        resourceCLI.EnsureRes.assert_called_once_with(
            producer, owned, isSecret=isSecret, existResDelTO='9m',
        )
        assert (resourceCLI.EnsureRes.call_args.args[1] is owned)
        resourceCLI.NewRes.assert_not_called()
    else:
        resourceCLI.NewRes.assert_called_once_with(
            producer, isSecret=isSecret,
        )
        resourceCLI.EnsureRes.assert_not_called()
    assert (owned == [('caller-client', 'Secret/caller', 'caller-uid')])


def test__ResCreate_EmptyOwnedListEnablesTracking(
    resourceCLI: mock.MagicMock,
) -> None:
    owned = []
    resourceCLI.EnsureRes.return_value = {'kind': 'Namespace'}
    MUT._ResCreate(resourceCLI, 'kind: Namespace', MUT.OwnInf(owned))
    resourceCLI.EnsureRes.assert_called_once_with(
        'kind: Namespace', owned, isSecret=False, existResDelTO='5m',
    )
    resourceCLI.NewRes.assert_not_called()


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('resource', [None, {}, [], 'bad'])
def test__ResCreate_RejectsInvalidReturnedState(
    resourceCLI: mock.MagicMock, tracked: bool, resource: object,
) -> None:
    resourceCLI.NewRes.return_value = (
        OCcli.ResPhase.APPLIED, CmdRes('', '', 0, True), resource,
    )
    resourceCLI.EnsureRes.return_value = resource
    with pytest.raises(RuntimeError, match='No valid resource dictionary'):
        MUT._ResCreate(
            resourceCLI, 'kind: Secret', (MUT.OwnInf([]) if tracked else None),
        )


def test__ResCreate_FailedCommandOmitsPrivateOutput(
    resourceCLI: mock.MagicMock,
) -> None:
    resourceCLI.NewRes.return_value = (
        OCcli.ResPhase.DRY_RUN,
        CmdRes('dummy-private-output', 'dummy-private-error', 1, False), None,
    )
    with pytest.raises(RuntimeError, match='DRY_RUN') as caught:
        MUT._ResCreate(resourceCLI, 'kind: Secret', isSecret=True)
    assert ('dummy-private' not in str(caught.value))


@pytest.mark.parametrize('tracked', [False, True])
def test__ResCreate_PreservesLifecycleException(
    resourceCLI: mock.MagicMock, tracked: bool,
) -> None:
    error = ValueError('invalid lifecycle state')
    method = (resourceCLI.EnsureRes if tracked else resourceCLI.NewRes)
    method.side_effect = error
    with pytest.raises(ValueError) as caught:
        MUT._ResCreate(
            resourceCLI, 'kind: Namespace',
            (MUT.OwnInf([]) if tracked else None),
        )
    assert (caught.value is error)
