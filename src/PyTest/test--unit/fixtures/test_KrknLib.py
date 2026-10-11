import functools
import sys
import types
from types import SimpleNamespace
from unittest import mock

import pytest

from fixtures.KrknLib import ExecOnNode, KrnChaos
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
    for name, module in KrknModules(clientType).items():
        monkeypatch.setitem(sys.modules, name, module)


def test_ExecOnNode_ExecutesInPrivilegedPodAndCleansBeforeAndAfter() -> None:
    client = KrknClient()
    client.exec_cmd_in_pod.return_value = 'kernel output'

    result = ExecOnNode(
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


def test_ExecOnNode_OriginalExecutionExceptionIdentitySurvivesFinalCleanupFailure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = KrknClient()
    executionError = RuntimeError('execution failed')
    client.exec_cmd_in_pod.side_effect = executionError
    client.delete_pod.side_effect = [None, RuntimeError('cleanup failed')]

    with caplog.at_level('WARNING'), pytest.raises(RuntimeError) as raised:
        ExecOnNode(client, 'worker', 'helper', 'command')

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
        result = ExecOnNode(client, 'node', 'pod', 'command')

    assert result == 'output'
    assert client.delete_pod.call_count == 2
    assert 'Could not delete helper Pod' in caplog.text


def test_ExecOnNode_CreateFailureStillCleansUp() -> None:
    client = KrknClient()
    client.create_pod.side_effect = RuntimeError('create failed')

    with pytest.raises(RuntimeError, match='create failed'):
        ExecOnNode(client, 'node', 'pod', 'command')

    assert client.delete_pod.call_count == 2
    client.exec_cmd_in_pod.assert_not_called()


def test_KrnChaos_BuildsContextFromOverridesAndFinishesTeardown(
    monkeypatch: pytest.MonkeyPatch,
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
            'krknPars': NamedDict(chaos=True),
            'testPars': NamedDict(vm='vm-one'),
        },
    )

    generator = KrnChaos.__wrapped__(
        request=request,
        fxtOCcli=ocFactory,
        fxtVirtCtlCLI=virtCtlFactory,
    )
    context = next(generator)

    assert context.krknClient is client
    assert context.krknPars == NamedDict(chaos=True)
    assert context.testPars == NamedDict(vm='vm-one')
    assert context.ns == 'custom-ns'
    assert context.cli.oc is ocFactory.return_value
    assert context.cli.virtCtl is virtCtlFactory.return_value
    assert isinstance(context.ExecOnNode, functools.partial)
    assert context.ExecOnNode.args[0] is client
    clientType.assert_called_once_with(kubeconfig_path='/custom/kubeconfig')
    ocFactory.assert_called_once_with(ns='custom-ns', context='oc-context')
    virtCtlFactory.assert_called_once_with(
        ns='custom-ns',
        kubeconfig='/virt-kubeconfig',
    )
    with pytest.raises(StopIteration):
        next(generator)


@pytest.mark.parametrize('hasRequestParam', [False, True])
def test_KrnChaos_UsesDefaultConfigurationWithAbsentOrEmptyRequestParam(
    monkeypatch: pytest.MonkeyPatch,
    hasRequestParam: bool,
) -> None:
    request = SimpleNamespace(param={}) if hasRequestParam else SimpleNamespace()
    clientType = mock.create_autospec(KrknClientStub)
    PatchKrknModules(monkeypatch, clientType)
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()

    generator = KrnChaos.__wrapped__(request, ocFactory, virtCtlFactory)
    context = next(generator)
    with pytest.raises(StopIteration):
        next(generator)

    assert context.ns == 'default'
    ocFactory.assert_called_once_with(ns='default')
    virtCtlFactory.assert_called_once_with(ns='default')
