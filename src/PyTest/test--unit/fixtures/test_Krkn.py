import dataclasses
import importlib.util
from pathlib import Path
import sys
import types
from types import SimpleNamespace
from unittest import mock

import pytest

import fixtures.Krkn as MUT
from libs.CustomTypes import NamedDict


class KrknClientStub:
    def __init__(self, kubeconfig_path: str = '') -> None:
        self.kubeconfig_path = kubeconfig_path

    def DeletePod(self, podName: str, namespace: str) -> None:
        pass

    def CreatePod(
        self,
        podConfig: dict[str, object],
        namespace: str,
        timeout: int,
    ) -> None:
        pass

    def ExecCmdInPod(
        self,
        command: list[str],
        podName: str,
        namespace: str,
    ) -> str:
        return ''

    # krkn-lib's public API requires these exact attribute names.
    delete_pod = DeletePod
    create_pod = CreatePod
    exec_cmd_in_pod = ExecCmdInPod


def KrknClient() -> mock.MagicMock:
    return mock.create_autospec(KrknClientStub, instance=True)


def KrknModules(clientType: mock.MagicMock) -> dict[str, types.ModuleType]:
    package = types.ModuleType('krkn_lib')
    package.__path__ = []
    ocp = types.ModuleType('krkn_lib.ocp')
    ocp.KrknOpenshift = clientType
    return {'krkn_lib': package, 'krkn_lib.ocp': ocp}


def PatchKrknModules(
    monkeypatch: pytest.MonkeyPatch,
    clientType: mock.MagicMock,
) -> None:
    for (name, module) in KrknModules(clientType).items():
        monkeypatch.setitem(sys.modules, name, module)


@pytest.mark.parametrize('scenario,expected', [
    ('test_VMsurvivesVirtLauncherKill', {
        'vmName': 'bsod-win2022', 'recoveryTO': 300,
    }),
    ('test_HostSideKernelScanOnVMnode', {'vmName': 'bsod-win2022'}),
])
def test_FxtKrkn_PreparesActualExampleConfiguration(
    monkeypatch: pytest.MonkeyPatch, pytestconfig: pytest.Config,
    scenario: str, expected: dict[str, object],
) -> None:
    pytestconfig.addinivalue_line('markers', 'krkn: Krkn chaos scenario.')
    path = (
        Path(MUT.__file__).resolve().parents[1] /
        'test--suites/ts--example/test_Krkn.py'
    )
    spec = importlib.util.spec_from_file_location('krkn_examples', path)
    assert spec is not None and spec.loader is not None
    examples = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(examples)
    method = getattr(examples.TestChaos, scenario)
    mark = next(mark for mark in method.pytestmark if mark.name == 'parametrize')
    config = mark.args[1][0][0]
    clientType = mock.create_autospec(KrknClientStub)
    PatchKrknModules(monkeypatch, clientType)
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()

    generator = MUT.FxtKrkn.__wrapped__(
        SimpleNamespace(param=config), ocFactory, virtCtlFactory,
    )
    try:
        context = next(generator)
        assert type(context.testPars) is NamedDict
        assert dict(context.testPars) == expected
        assert context.ns == 'bsod-test'
        clientType.assert_called_once_with(kubeconfig_path=MUT._DEF__FXT_CFG.kCfg)
        ocFactory.assert_called_once_with(ns='bsod-test')
        virtCtlFactory.assert_called_once_with(ns='bsod-test')
        context.krknClient.delete_pod.assert_not_called()
        context.krknClient.create_pod.assert_not_called()
        context.krknClient.exec_cmd_in_pod.assert_not_called()
    finally:
        generator.close()


def test_ExecOnNode_ExecutesInPrivilegedPodAndCleansBeforeAndAfter() -> None:
    client = KrknClient()
    client.exec_cmd_in_pod.return_value = 'kernel output'

    result = MUT.ExecOnNode(
        client,
        'worker-1',
        'helper',
        'uname -r',
        ns='target-ns',
        ctrImg='registry/image',
        timeout=17,
    )

    assert result == 'kernel output'
    assert client.delete_pod.call_args_list == [
        mock.call('helper', 'target-ns'),
        mock.call('helper', 'target-ns'),
    ]
    podConfig = client.create_pod.call_args.args[0]
    assert podConfig['metadata']['name'] == 'helper'
    assert podConfig['spec']['nodeName'] == 'worker-1'
    assert podConfig['spec']['hostNetwork']
    assert podConfig['spec']['containers'][0]['securityContext']['privileged']
    assert podConfig['spec']['containers'][0]['image'] == 'registry/image'
    client.create_pod.assert_called_once_with(podConfig, 'target-ns', 17)
    client.exec_cmd_in_pod.assert_called_once_with(
        ['uname -r'],
        'helper',
        'target-ns',
    )
    assert client.mock_calls == [
        mock.call.delete_pod('helper', 'target-ns'),
        mock.call.create_pod(podConfig, 'target-ns', 17),
        mock.call.exec_cmd_in_pod(['uname -r'], 'helper', 'target-ns'),
        mock.call.delete_pod('helper', 'target-ns'),
    ]


def test_ExecOnNode_ExecutionExceptionIdentitySurvivesFinalCleanupFailure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = KrknClient()
    executionError = RuntimeError('execution failed')
    client.exec_cmd_in_pod.side_effect = executionError
    client.delete_pod.side_effect = [None, RuntimeError('cleanup failed')]

    with caplog.at_level('WARNING'), pytest.raises(RuntimeError) as raised:
        MUT.ExecOnNode(client, 'worker', 'helper', 'command')

    assert raised.value is executionError
    podConfig = client.create_pod.call_args.args[0]
    assert client.mock_calls == [
        mock.call.delete_pod('helper', 'default'),
        mock.call.create_pod(podConfig, 'default', 120),
        mock.call.exec_cmd_in_pod(['command'], 'helper', 'default'),
        mock.call.delete_pod('helper', 'default'),
    ]
    assert 'Could not delete helper Pod' in caplog.text


def test_ExecOnNode_CleanupFailuresAreBestEffort(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = KrknClient()
    client.delete_pod.side_effect = [
        RuntimeError('stale'),
        RuntimeError('cleanup'),
    ]
    client.exec_cmd_in_pod.return_value = 'output'

    with caplog.at_level('WARNING'):
        result = MUT.ExecOnNode(client, 'node', 'pod', 'command')

    assert result == 'output'
    assert client.delete_pod.call_count == 2
    assert 'Could not delete helper Pod' in caplog.text


def test_ExecOnNode_CreateFailureStillCleansUp() -> None:
    client = KrknClient()
    client.create_pod.side_effect = RuntimeError('create failed')

    with pytest.raises(RuntimeError, match='create failed'):
        MUT.ExecOnNode(client, 'node', 'pod', 'command')

    assert client.delete_pod.call_count == 2
    client.exec_cmd_in_pod.assert_not_called()


@pytest.mark.parametrize(
    'fld', ['krknClient', 'krknPars', 'testPars', 'cli', 'ns', 'ExecOnNode'],
)
def test_FxtKrkn_YieldsFrozenObjectWithMutableContents(
    monkeypatch: pytest.MonkeyPatch,
    fld: str,
) -> None:
    clientType = mock.create_autospec(KrknClientStub)
    client = clientType.return_value
    PatchKrknModules(monkeypatch, clientType)
    ocFactory = mock.MagicMock(name='ocFactory')
    virtCtlFactory = mock.MagicMock(name='virtCtlFactory')
    request = SimpleNamespace(
        param={
            'kCfg': '/custom/kubeconfig',
            'ns': 'custom-ns',
            'cliCfg': NamedDict(
                ocCfg={'context': 'oc-context'},
                vcCfg={'kubeconfig': '/virt-kubeconfig'},
            ),
            'krknPars': NamedDict(chaos=True, targets=[]),
            'testPars': NamedDict(vm='vm-one', results=[]),
        },
    )

    generator = MUT.FxtKrkn.__wrapped__(
        request=request,
        fxtOCcli=ocFactory,
        fxtVirtCtlCLI=virtCtlFactory,
    )
    context = next(generator)

    assert context.krknClient is client
    assert (context.krknPars == NamedDict(chaos=True, targets=[]))
    assert (context.testPars == NamedDict(vm='vm-one', results=[]))
    assert context.ns == 'custom-ns'
    assert context.cli.oc is ocFactory.return_value
    assert context.cli.virtCtl is virtCtlFactory.return_value
    clientType.assert_called_once_with(kubeconfig_path='/custom/kubeconfig')
    ocFactory.assert_called_once_with(ns='custom-ns', context='oc-context')
    virtCtlFactory.assert_called_once_with(
        ns='custom-ns',
        kubeconfig='/virt-kubeconfig',
    )

    orig = getattr(context, fld)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(context, fld, object())
    assert (getattr(context, fld) is orig)

    context.krknPars.targets.append('node-one')
    context.testPars.results.append('recovered')
    context.cli.oc.Run.return_value = 'running'
    assert (context.krknPars.targets == ['node-one'])
    assert (context.testPars.results == ['recovered'])
    assert (context.cli.oc.Run() == 'running')

    with pytest.raises(StopIteration):
        next(generator)


@pytest.mark.parametrize(
    'overrides, namespace, image',
    [
        ({}, 'custom-ns', 'configured-image'),
        ({'ns': 'call-ns'}, 'call-ns', 'configured-image'),
        ({'ctrImg': 'call-image'}, 'custom-ns', 'call-image'),
        ({'ns': 'call-ns', 'ctrImg': 'call-image'}, 'call-ns', 'call-image'),
    ],
)
@pytest.mark.parametrize('executionFails', [False, True])
def test_FxtKrkn_ExecOnNodeUsesConfiguredDefaultsAndCallOverrides(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str],
    namespace: str,
    image: str,
    executionFails: bool,
) -> None:
    clientType = mock.create_autospec(KrknClientStub)
    client = clientType.return_value
    client.exec_cmd_in_pod.return_value = 'output'
    PatchKrknModules(monkeypatch, clientType)
    generator = MUT.FxtKrkn.__wrapped__(
        request=SimpleNamespace(param={
            'ns': 'custom-ns', 'ctrImgURL_ExecNode': 'configured-image',
        }),
        fxtOCcli=mock.MagicMock(),
        fxtVirtCtlCLI=mock.MagicMock(),
    )
    context = next(generator)

    if executionFails:
        client.exec_cmd_in_pod.side_effect = RuntimeError('execution failed')
        with pytest.raises(RuntimeError, match='execution failed'):
            context.ExecOnNode('node', 'owned-helper', 'uname -r', **overrides)
    else:
        assert context.ExecOnNode(
            'node', 'owned-helper', 'uname -r', **overrides,
        ) == 'output'

    podConfig = client.create_pod.call_args.args[0]
    assert podConfig['spec']['containers'][0]['image'] == image
    client.create_pod.assert_called_once_with(podConfig, namespace, 120)
    client.exec_cmd_in_pod.assert_called_once_with(
        ['uname -r'], 'owned-helper', namespace,
    )
    assert client.delete_pod.call_args_list == [
        mock.call('owned-helper', namespace),
        mock.call('owned-helper', namespace),
    ]
    with pytest.raises(StopIteration):
        next(generator)


@pytest.mark.parametrize('hasRequestParam', [False, True])
def test_FxtKrkn_UsesDefaultConfigurationWithAbsentOrEmptyRequestParam(
    monkeypatch: pytest.MonkeyPatch,
    hasRequestParam: bool,
) -> None:
    request = (
        SimpleNamespace(param={}) if hasRequestParam else SimpleNamespace()
    )
    clientType = mock.create_autospec(KrknClientStub)
    PatchKrknModules(monkeypatch, clientType)
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()

    generator = MUT.FxtKrkn.__wrapped__(request, ocFactory, virtCtlFactory)
    context = next(generator)
    with pytest.raises(StopIteration):
        next(generator)

    client = clientType.return_value
    client.exec_cmd_in_pod.return_value = 'default-output'
    assert context.ExecOnNode(
        'node', 'owned-helper', 'uname -r',
    ) == 'default-output'
    podConfig = client.create_pod.call_args.args[0]
    assert (
        podConfig['spec']['containers'][0]['image'] ==
        MUT._DEF__FXT_CFG.ctrImgURL_ExecNode
    )
    client.create_pod.assert_called_once_with(podConfig, 'default', 120)
    client.exec_cmd_in_pod.assert_called_once_with(
        ['uname -r'], 'owned-helper', 'default',
    )

    assert context.ns == 'default'
    ocFactory.assert_called_once_with(ns='default')
    virtCtlFactory.assert_called_once_with(ns='default')
