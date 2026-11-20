from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest import mock

import pytest

import libs.OpenShift.LP.Virt.VM.RHOVssh as MUT
from libs.Utils.CmdExec import CmdRes
import Utils


def CommandResult(success: bool, stderr: str = '') -> CmdRes:
    return CmdRes('', stderr, 0 if success else 1, success)


@pytest.fixture(name='rhovHash', autouse=True)
def RHOVHash(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    digestValue = 'a' * 64
    digestResult = mock.Mock()
    digestResult.hexdigest.return_value = digestValue
    sha256 = mock.Mock(return_value=digestResult)
    monkeypatch.setattr(MUT, 'hashlib', SimpleNamespace(sha256=sha256))
    return SimpleNamespace(
        sha256=sha256,
        digestResult=digestResult,
        digestValue=digestValue,
    )


@pytest.fixture(name='rhovIO', autouse=True)
def RHOVIO(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    environ = {}
    expandvars = mock.Mock(return_value='~/key')
    pathFactory = mock.Mock()
    pathFactory.return_value.expanduser.return_value = '/synthetic/home/key'
    namedDictFactory = mock.Mock(side_effect=SimpleNamespace)
    monkeypatch.setattr(MUT, 'NamedDict', namedDictFactory)
    monkeypatch.setattr(MUT, 'os', SimpleNamespace(
        path=SimpleNamespace(expandvars=expandvars),
        environ=environ,
        getenv=environ.get,
    ))
    monkeypatch.setattr(MUT, 'Path', pathFactory)
    return SimpleNamespace(
        environ=environ,
        expandvars=expandvars,
        pathFactory=pathFactory,
        namedDictFactory=namedDictFactory,
    )


@pytest.fixture(name='rhovTime', autouse=True)
def RHOVTime(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    clock = Utils.FakeClock()
    fakeTime = SimpleNamespace(
        monotonic=mock.Mock(side_effect=clock.Now),
        sleep=mock.Mock(side_effect=clock.Sleep),
    )
    monkeypatch.setattr(MUT, 'time', fakeTime)
    return SimpleNamespace(clock=clock, time=fakeTime)


@pytest.fixture(name='probeClock')
def ProbeClock(rhovTime: SimpleNamespace) -> Utils.FakeClock:
    return rhovTime.clock


@pytest.fixture(name='rhovConnection')
def RHOVConnection(rhovIO: SimpleNamespace) -> Utils.RHOVconnectionContext:
    oc = mock.create_autospec(MUT.OCcli, instance=True)
    oc.GetResUID.return_value = (True, 'cluster-one', '')
    virtCtl = mock.create_autospec(MUT.VirtCtlCLI, instance=True)
    cmdExec = mock.create_autospec(MUT.CmdExec, instance=True)
    connection = MUT.RHOVsshCon(
        ns='namespace',
        host='vm-one',
        user='user',
        idFile='~/key',
        ctrlPersist='2h',
        virtCtl=virtCtl,
        oc=oc,
        cmdExec=cmdExec,
    )
    return Utils.RHOVconnectionContext(
        connection=connection,
        virtCtl=virtCtl,
        oc=oc,
        cmdExec=cmdExec,
    )


def test_Init_UsesSuppliedClientsAndExpandsIdentityPath(
    rhovHash: SimpleNamespace,
    rhovConnection: Utils.RHOVconnectionContext,
    rhovIO: SimpleNamespace,
) -> None:
    connection = rhovConnection.connection
    expectedDigest = rhovHash.digestValue
    rhovHash.sha256.assert_called_once_with(
        b'user@vm-one.namespace.cluster-one',
    )
    rhovHash.digestResult.hexdigest.assert_called_once_with()

    assert (connection.ns == 'namespace')
    assert (connection.host == 'vm-one')
    assert (connection.user == 'user')
    assert (connection.idFile == '/synthetic/home/key')
    assert (connection.target == 'user@vm-one.namespace.cluster-one')
    assert (connection.ctrlPath == f'/tmp/ssh..{expectedDigest}..sock')
    assert (connection.cli.virtCtl is rhovConnection.virtCtl)
    assert (connection.cli.cmdExec is rhovConnection.cmdExec)
    assert (connection.cli.oc is rhovConnection.oc)
    rhovIO.expandvars.assert_called_once_with('~/key')
    rhovIO.pathFactory.assert_called_once_with('~/key')
    rhovIO.pathFactory.return_value.expanduser.assert_called_once_with()
    rhovIO.namedDictFactory.assert_called_once_with(
        cmdExec=rhovConnection.cmdExec,
        oc=rhovConnection.oc,
        virtCtl=rhovConnection.virtCtl,
    )
    rhovConnection.oc.GetResUID.assert_called_once_with(
        'namespace/kube-system',
    )


def test_Init_CreatesDefaultsWithExplicitKubeconfig(
    monkeypatch: pytest.MonkeyPatch,
    rhovIO: SimpleNamespace,
) -> None:
    rhovIO.expandvars.return_value = '$HOME/key'
    Utils.AutoPatch(monkeypatch, MUT, 'OCcli')
    MUT.OCcli.return_value.GetResUID.return_value = (True, 'cluster-one', '')
    Utils.AutoPatch(monkeypatch, MUT, 'VirtCtlCLI')
    Utils.AutoPatch(monkeypatch, MUT, 'CmdExec')

    connection = MUT.RHOVsshCon(
        'ns', 'vm', 'user', '$HOME/key', kubeconfig='/kube',
    )

    MUT.OCcli.assert_called_once_with(n='ns', kubeconfig='/kube')
    MUT.VirtCtlCLI.assert_called_once_with(n='ns', kubeconfig='/kube')
    MUT.CmdExec.assert_called_once_with()
    rhovIO.expandvars.assert_called_once_with('$HOME/key')
    assert (connection.cli.virtCtl is MUT.VirtCtlCLI.return_value)
    assert (connection.cli.oc is MUT.OCcli.return_value)
    assert (connection.cli.cmdExec is MUT.CmdExec.return_value)


def test_Init_ReadsKubeconfigEnvironment(
    monkeypatch: pytest.MonkeyPatch,
    rhovIO: SimpleNamespace,
) -> None:
    rhovIO.environ['KUBECONFIG'] = '/from-env'
    Utils.AutoPatch(monkeypatch, MUT, 'OCcli')
    MUT.OCcli.return_value.GetResUID.return_value = (True, 'cluster-one', '')
    Utils.AutoPatch(monkeypatch, MUT, 'VirtCtlCLI')
    Utils.AutoPatch(monkeypatch, MUT, 'CmdExec')

    MUT.RHOVsshCon('ns', 'vm', 'user', 'key')

    MUT.OCcli.assert_called_once_with(n='ns', kubeconfig='/from-env')
    MUT.VirtCtlCLI.assert_called_once_with(n='ns', kubeconfig='/from-env')
    MUT.CmdExec.assert_called_once_with()
    rhovIO.expandvars.assert_called_once_with('key')


def test_Init_OmitsAbsentKubeconfig(
    monkeypatch: pytest.MonkeyPatch,
    rhovIO: SimpleNamespace,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'OCcli')
    MUT.OCcli.return_value.GetResUID.return_value = (True, 'cluster-one', '')
    Utils.AutoPatch(monkeypatch, MUT, 'VirtCtlCLI')
    Utils.AutoPatch(monkeypatch, MUT, 'CmdExec')

    MUT.RHOVsshCon('ns', 'vm', 'user', 'key')

    MUT.OCcli.assert_called_once_with(n='ns')
    MUT.VirtCtlCLI.assert_called_once_with(n='ns')
    MUT.CmdExec.assert_called_once_with()
    rhovIO.expandvars.assert_called_once_with('key')


@pytest.mark.parametrize('lookup', [
    (False, '', 'forbidden'),
    (False, 'partial-uid', 'failed'),
    (True, '', ''),
])
def test_Init_RejectsFailedOrEmptyClusterUID(
    rhovConnection: Utils.RHOVconnectionContext,
    lookup: tuple[bool, str, str],
) -> None:
    oc = mock.create_autospec(MUT.OCcli, instance=True)
    oc.GetResUID.return_value = lookup

    with pytest.raises(RuntimeError, match='namespace/kube-system'):
        MUT.RHOVsshCon(
            'ns', 'vm', 'user', 'key', virtCtl=rhovConnection.virtCtl,
            oc=oc, cmdExec=rhovConnection.cmdExec,
        )

    oc.GetResUID.assert_called_once_with('namespace/kube-system')
    rhovConnection.virtCtl.Run.assert_not_called()
    rhovConnection.cmdExec.Run.assert_not_called()


@pytest.mark.parametrize('falsey', [False, True])
def test_Init_PreservesProvidedClientsInsteadOfCreatingDefaults(
    rhovHash: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    falsey: bool,
) -> None:
    oc = mock.MagicMock()
    virtCtl = mock.MagicMock()
    executor = mock.MagicMock()
    for client in (oc, virtCtl, executor):
        client.__bool__.return_value = not falsey
    oc.GetResUID.return_value = (True, 'provided-cluster', '')
    expectedDigest = '2' * 64
    rhovHash.digestResult.hexdigest.return_value = expectedDigest
    ocFactory = mock.Mock()
    virtCtlFactory = mock.Mock()
    executorFactory = mock.Mock()
    monkeypatch.setattr(MUT, 'OCcli', ocFactory)
    monkeypatch.setattr(MUT, 'VirtCtlCLI', virtCtlFactory)
    monkeypatch.setattr(MUT, 'CmdExec', executorFactory)

    connection = MUT.RHOVsshCon(
        'ns', 'vm', 'user', 'key', kubeconfig='/ignored',
        virtCtl=virtCtl, oc=oc, cmdExec=executor,
    )

    assert (connection.cli.oc is oc)
    assert (connection.cli.virtCtl is virtCtl)
    assert (connection.cli.cmdExec is executor)
    assert (connection.target == 'user@vm.ns.provided-cluster')
    assert (connection.ctrlPath == f'/tmp/ssh..{expectedDigest}..sock')
    rhovHash.sha256.assert_called_once_with(b'user@vm.ns.provided-cluster')
    rhovHash.digestResult.hexdigest.assert_called_once_with()
    oc.GetResUID.assert_called_once_with('namespace/kube-system')
    ocFactory.assert_not_called()
    virtCtlFactory.assert_not_called()
    executorFactory.assert_not_called()


@pytest.mark.parametrize(('ns', 'host', 'user', 'expectedDigest'), [
    ('other-ns', 'vm-one', 'user', ('b' * 64)),
    ('namespace', 'vm-two', 'user', ('c' * 64)),
    ('namespace', 'vm-one', 'other-user', ('d' * 64)),
])
def test_Init_ControlSocketIdentityIsStableAndDistinct(
    rhovHash: SimpleNamespace,
    expectedDigest: str,
    rhovConnection: Utils.RHOVconnectionContext,
    ns: str,
    host: str,
    user: str,
) -> None:
    first = rhovConnection.connection
    expectedTarget = f'{user}@{host}.{ns}.cluster-one'
    rhovHash.digestResult.hexdigest.side_effect = [
        expectedDigest, expectedDigest,
    ]
    changed = MUT.RHOVsshCon(
        ns, host, user, first.idFile,
        virtCtl=rhovConnection.virtCtl, oc=rhovConnection.oc,
        cmdExec=rhovConnection.cmdExec,
    )
    repeated = MUT.RHOVsshCon(
        ns, host, user, first.idFile,
        virtCtl=rhovConnection.virtCtl, oc=rhovConnection.oc,
        cmdExec=rhovConnection.cmdExec,
    )

    assert (changed.target == expectedTarget)
    assert (changed.ctrlPath == f'/tmp/ssh..{expectedDigest}..sock')
    assert (changed.ctrlPath == repeated.ctrlPath)
    assert (first.ctrlPath == f'/tmp/ssh..{rhovHash.digestValue}..sock')
    assert (changed.ctrlPath != first.ctrlPath)
    assert (rhovHash.sha256.call_args_list == [
        mock.call(b'user@vm-one.namespace.cluster-one'),
        mock.call(expectedTarget.encode('utf-8')),
        mock.call(expectedTarget.encode('utf-8')),
    ])
    assert (rhovHash.digestResult.hexdigest.call_args_list == [
        mock.call(), mock.call(), mock.call(),
    ])


def test_Init_IsolatesClustersAndReusesTheSameClusterIdentity(
    rhovHash: SimpleNamespace,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    first = rhovConnection.connection
    otherOC = mock.create_autospec(MUT.OCcli, instance=True)
    otherOC.GetResUID.return_value = (True, 'cluster-two', '')
    otherDigest = 'e' * 64
    sameDigest = rhovHash.digestValue
    rhovHash.digestResult.hexdigest.side_effect = [otherDigest, sameDigest]
    otherVirtCtl = mock.create_autospec(MUT.VirtCtlCLI, instance=True)
    other = MUT.RHOVsshCon(
        first.ns, first.host, first.user, first.idFile,
        virtCtl=otherVirtCtl, oc=otherOC, cmdExec=rhovConnection.cmdExec,
    )
    sameOC = mock.create_autospec(MUT.OCcli, instance=True)
    sameOC.GetResUID.return_value = (True, 'cluster-one', '')
    sameVirtCtl = mock.create_autospec(MUT.VirtCtlCLI, instance=True)
    repeated = MUT.RHOVsshCon(
        first.ns, first.host, first.user, first.idFile,
        virtCtl=sameVirtCtl, oc=sameOC, cmdExec=rhovConnection.cmdExec,
    )

    assert (other.target == 'user@vm-one.namespace.cluster-two')
    assert (other.ctrlPath == f'/tmp/ssh..{otherDigest}..sock')
    assert (other.ctrlPath != first.ctrlPath)
    assert (repeated.target == first.target)
    assert (repeated.ctrlPath == f'/tmp/ssh..{sameDigest}..sock')
    assert (repeated.ctrlPath == first.ctrlPath)
    assert (rhovHash.sha256.call_args_list == [
        mock.call(b'user@vm-one.namespace.cluster-one'),
        mock.call(b'user@vm-one.namespace.cluster-two'),
        mock.call(b'user@vm-one.namespace.cluster-one'),
    ])
    assert (rhovHash.digestResult.hexdigest.call_args_list == [
        mock.call(), mock.call(), mock.call(),
    ])
    otherOC.GetResUID.assert_called_once_with('namespace/kube-system')
    sameOC.GetResUID.assert_called_once_with('namespace/kube-system')
    otherVirtCtl.Run.assert_not_called()
    sameVirtCtl.Run.assert_not_called()
    rhovConnection.virtCtl.Run.assert_not_called()
    rhovConnection.cmdExec.Run.assert_not_called()


@pytest.mark.parametrize(('host', 'ns', 'expectedDigest'), [
    (('v' * 40), ('n' * 60), ('0' * 64)),
    (('v' * 63), ('n' * 63), ('1' * 64)),
])
def test_Init_LongNamesKeepControlAndTemporarySocketPathsBounded(
    rhovConnection: Utils.RHOVconnectionContext,
    rhovHash: SimpleNamespace,
    host: str,
    ns: str,
    expectedDigest: str,
) -> None:
    rhovHash.digestResult.hexdigest.side_effect = [expectedDigest]
    expectedTarget = f'qa-usr@{host}.{ns}.cluster-one'
    connection = MUT.RHOVsshCon(
        ns, host, 'qa-usr', '/synthetic/not-read',
        virtCtl=rhovConnection.virtCtl, oc=rhovConnection.oc,
        cmdExec=rhovConnection.cmdExec,
    )
    socketPath = connection.ctrlPath + '.0123456789abcdef'

    assert (connection.target == expectedTarget)
    assert (connection.ctrlPath == f'/tmp/ssh..{expectedDigest}..sock')
    assert (len(connection.ctrlPath.encode('utf-8')) == 80)
    assert (len(socketPath.encode('utf-8')) == 97)
    assert (rhovHash.sha256.call_args_list == [
        mock.call(b'user@vm-one.namespace.cluster-one'),
        mock.call(expectedTarget.encode('utf-8')),
    ])
    assert (rhovHash.digestResult.hexdigest.call_args_list == [
        mock.call(), mock.call(),
    ])
    rhovConnection.virtCtl.Run.assert_not_called()
    rhovConnection.cmdExec.Run.assert_not_called()


@pytest.mark.parametrize('cmdTO', [None, 1, 9])
@pytest.mark.parametrize('success', [False, True])
def test_IsAlive_PassesControlCheckToExecutor(
    rhovConnection: Utils.RHOVconnectionContext,
    cmdTO: Optional[int],
    success: bool,
) -> None:
    connection = rhovConnection.connection
    rhovConnection.cmdExec.Run.return_value = CommandResult(success)
    options = {} if (cmdTO is None) else {'cmdTO': cmdTO}
    effectiveTO = 5 if (cmdTO is None) else cmdTO

    result = connection.IsAlive(**options)

    assert (result is success)
    rhovConnection.cmdExec.Run.assert_called_once_with(
        [
            'ssh', '-O', 'check', '-o',
            f'ControlPath={connection.ctrlPath}', 'muxSock',
        ],
        warnOnFail=False,
        tryMax=1,
        spPars={'timeout': effectiveTO},
    )


@pytest.mark.parametrize('cmdTO', [None, 1, 9])
def test_Close_PassesControlExitToExecutor(
    rhovConnection: Utils.RHOVconnectionContext,
    cmdTO: Optional[int],
) -> None:
    connection = rhovConnection.connection
    options = {} if (cmdTO is None) else {'cmdTO': cmdTO}
    effectiveTO = 5 if (cmdTO is None) else cmdTO

    assert (connection.Close(**options) is None)
    rhovConnection.cmdExec.Run.assert_called_once_with(
        [
            'ssh', '-O', 'exit', '-o',
            f'ControlPath={connection.ctrlPath}', 'muxSock',
        ],
        warnOnFail=False,
        tryMax=1,
        spPars={'timeout': effectiveTO},
    )


def test_Connect_ReturnsWhenAlreadyAlive(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'IsAlive')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.IsAlive.return_value = True

    rhovConnection.connection.Connect()

    MUT.RHOVsshCon.IsAlive.assert_called_once_with(
        rhovConnection.connection, 5,
    )
    MUT.RHOVsshCon.Close.assert_not_called()
    rhovConnection.virtCtl.Run.assert_not_called()


@pytest.mark.parametrize('cmdTO', [None, 1, 9])
def test_Connect_ClosesStaleSocketAndOpensTunnel(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    cmdTO: Optional[int],
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'IsAlive')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.IsAlive.return_value = False
    rhovConnection.virtCtl.Run.return_value = CommandResult(True)
    connection = rhovConnection.connection
    options = {} if (cmdTO is None) else {'cmdTO': cmdTO}
    effectiveTO = 5 if (cmdTO is None) else cmdTO

    connection.Connect(**options)

    MUT.RHOVsshCon.IsAlive.assert_called_once_with(connection, effectiveTO)
    MUT.RHOVsshCon.Close.assert_called_once_with(connection, effectiveTO)
    rhovConnection.virtCtl.Run.assert_called_once_with(
        'ssh',
        '-t', '-o UserKnownHostsFile=/dev/null',
        '-t', '-o StrictHostKeyChecking=no',
        '-t', '-o ControlMaster=yes',
        '-t', f'-o ControlPath={connection.ctrlPath}',
        '-t', f'-o ControlPersist={connection.ctrlPersist}',
        '-t', '-N',
        '-i', connection.idFile,
        f'{connection.user}@vm/{connection.host}',
        tryMax=1,
        spPars={'timeout': effectiveTO},
    )


def test_Connect_RaisesWithTunnelError(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'IsAlive')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.IsAlive.return_value = False
    rhovConnection.virtCtl.Run.return_value = CommandResult(
        False, 'connection denied',
    )

    with pytest.raises(RuntimeError, match='connection denied') as caught:
        rhovConnection.connection.Connect(cmdTO=9)

    assert ('user@vm-one.namespace.cluster-one' in str(caught.value))
    MUT.RHOVsshCon.IsAlive.assert_called_once_with(
        rhovConnection.connection, 9,
    )
    MUT.RHOVsshCon.Close.assert_called_once_with(
        rhovConnection.connection, 9,
    )
    assert (rhovConnection.virtCtl.Run.call_args.kwargs == {
        'tryMax': 1,
        'spPars': {'timeout': 9},
    })


def test_Probe_RetriesThenSucceeds(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = [RuntimeError('not ready'), None]
    connection = rhovConnection.connection
    expectedCalls = [mock.call(connection, 1), mock.call(connection, 1)]

    assert (connection.Probe(probeTO=30, interval=2, cmdTO=1) is True)

    assert (MUT.RHOVsshCon.Connect.call_args_list == expectedCalls)
    assert (MUT.RHOVsshCon.Close.call_args_list == expectedCalls)
    MUT.time.sleep.assert_called_once_with(2)
    assert (probeClock.now == 12)


def test_Probe_ReturnsFalseAfterTimeout(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    rhovTime: SimpleNamespace,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    rhovTime.time.monotonic.side_effect = [10, 16]
    MUT.RHOVsshCon.Connect.side_effect = RuntimeError('not ready')
    connection = rhovConnection.connection

    assert (connection.Probe(probeTO=6, interval=1, cmdTO=1) is False)

    MUT.RHOVsshCon.Connect.assert_called_once_with(connection, 1)
    MUT.RHOVsshCon.Close.assert_called_once_with(connection, 1)
    MUT.time.sleep.assert_not_called()


@pytest.mark.parametrize('field', ['probeTO', 'interval', 'cmdTO'])
@pytest.mark.parametrize('value', [
    False, None, '1', 'bad', [], {}, 1j, 0, 0.0, -1, -0.5,
    True, 0.5, 1.5, float('nan'), float('inf'), float('-inf'),
])
def test_Probe_RejectsInvalidTimingBeforeConnection(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    rhovTime: SimpleNamespace,
    field: str,
    value: object,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')

    with pytest.raises(
        ValueError, match=f'^The `{field}` must be a positive integer',
    ):
        rhovConnection.connection.Probe(**{field: value})

    MUT.RHOVsshCon.Connect.assert_not_called()
    MUT.RHOVsshCon.Close.assert_not_called()
    rhovTime.time.monotonic.assert_not_called()
    rhovTime.time.sleep.assert_not_called()
    rhovConnection.cmdExec.Run.assert_not_called()
    rhovConnection.virtCtl.Run.assert_not_called()


@pytest.mark.parametrize(('probeTO', 'interval'), [
    (7, 2), (6, 1), (10, 3), (12, 2),
])
def test_Probe_AcceptsPositiveTimingAndPreservesSuccess(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
    probeTO: int,
    interval: int,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = [RuntimeError('not ready'), None]
    connection = rhovConnection.connection
    expectedCalls = [mock.call(connection, 1), mock.call(connection, 1)]

    assert (connection.Probe(probeTO, interval, 1) is True)

    assert (MUT.RHOVsshCon.Connect.call_args_list == expectedCalls)
    assert (MUT.RHOVsshCon.Close.call_args_list == expectedCalls)
    MUT.time.sleep.assert_called_once_with(interval)
    assert (probeClock.now == (10 + interval))


def test_Probe_SkipsWaitWhenAnotherAttemptCannotFit(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')

    def FailedConnect(connection: MUT.RHOVsshCon, cmdTO: int) -> None:
        probeClock.now += 9.5
        raise RuntimeError('not ready')

    MUT.RHOVsshCon.Connect.side_effect = FailedConnect
    connection = rhovConnection.connection

    assert (connection.Probe(probeTO=10, interval=5, cmdTO=1) is False)

    MUT.RHOVsshCon.Connect.assert_called_once_with(connection, 1)
    MUT.RHOVsshCon.Close.assert_called_once_with(connection, 1)
    MUT.time.sleep.assert_not_called()
    assert (probeClock.now == 19.5)


def test_Probe_SkipsFinalWaitAfterMultipleFailedAttempts(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = RuntimeError('not ready')
    connection = rhovConnection.connection
    expectedCalls = [mock.call(connection, 1)] * 3

    assert (connection.Probe(probeTO=17, interval=6, cmdTO=1) is False)

    assert (MUT.time.sleep.call_args_list == [mock.call(6), mock.call(6)])
    assert (MUT.RHOVsshCon.Connect.call_args_list == expectedCalls)
    assert (MUT.RHOVsshCon.Close.call_args_list == expectedCalls)
    assert (probeClock.now == 22)


def test_Probe_CountsAttemptDurationBeforeAdmittingRetryWait(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')

    def FailedConnect(connection: MUT.RHOVsshCon, cmdTO: int) -> None:
        probeClock.now += 9.75
        raise RuntimeError('not ready')

    MUT.RHOVsshCon.Connect.side_effect = FailedConnect
    connection = rhovConnection.connection

    assert (connection.Probe(probeTO=10, interval=5, cmdTO=1) is False)

    MUT.RHOVsshCon.Connect.assert_called_once_with(connection, 1)
    MUT.RHOVsshCon.Close.assert_called_once_with(connection, 1)
    MUT.time.sleep.assert_not_called()
    assert (probeClock.now == 19.75)


@pytest.mark.parametrize('attemptDuration', [10, 10.25])
def test_Probe_SkipsSleepAndFurtherAttemptsAfterDeadline(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
    attemptDuration: float,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')

    def FailedConnect(connection: MUT.RHOVsshCon, cmdTO: int) -> None:
        probeClock.now += attemptDuration
        raise RuntimeError('not ready')

    MUT.RHOVsshCon.Connect.side_effect = FailedConnect
    connection = rhovConnection.connection

    assert (connection.Probe(probeTO=10, interval=5, cmdTO=1) is False)

    MUT.RHOVsshCon.Connect.assert_called_once_with(connection, 1)
    MUT.RHOVsshCon.Close.assert_called_once_with(connection, 1)
    MUT.time.sleep.assert_not_called()


@pytest.mark.parametrize(('connectDuration', 'closeDuration'), [
    (5, 0), (6, 0), (7, 0), (5, 0.5), (5, 1), (5, 1.25),
])
def test_Probe_PreservesSuccessfulAttemptDespiteDeadlineOverrun(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
    connectDuration: float,
    closeDuration: float,
) -> None:
    def SuccessfulConnect(connection: MUT.RHOVsshCon, cmdTO: int) -> None:
        probeClock.now += connectDuration

    def Close(connection: MUT.RHOVsshCon, cmdTO: int) -> None:
        probeClock.now += closeDuration

    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = SuccessfulConnect
    MUT.RHOVsshCon.Close.side_effect = Close
    connection = rhovConnection.connection

    assert (connection.Probe(probeTO=6, interval=1, cmdTO=1) is True)

    MUT.RHOVsshCon.Connect.assert_called_once_with(connection, 1)
    MUT.RHOVsshCon.Close.assert_called_once_with(connection, 1)
    MUT.time.sleep.assert_not_called()
    assert (probeClock.now == (10 + connectDuration + closeDuration))


@pytest.mark.parametrize('tty, option', [
    (None, '-T'), (False, '-T'), (True, '-t'),
], ids=['default', 'no-tty', 'tty'])
@pytest.mark.parametrize('stdin', [None, 'input\n', 'last line'])
@pytest.mark.parametrize('success', [False, True])
def test_RunShell_ConnectsAndPassesStdin(
    tty: bool | None,
    option: str,
    stdin: str | None,
    success: bool,
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    expected = CmdRes('stdout\n', 'stderr\n', 0 if success else 1, success)
    rhovConnection.cmdExec.Run.return_value = expected
    options = {} if (tty is None) else {'tty': tty}

    result = rhovConnection.connection.RunShell(
        'hostname', stdin=stdin, **options,
    )

    assert (result is expected)
    MUT.RHOVsshCon.Connect.assert_called_once_with(rhovConnection.connection)
    rhovConnection.cmdExec.Run.assert_called_once_with(
        [
            'ssh', option, '-o', 'ControlMaster=no',
            '-o', f'ControlPath={rhovConnection.connection.ctrlPath}',
            'muxSock', 'hostname',
        ],
        input=stdin,
    )


@pytest.mark.parametrize('interactive,command', [
    (None, 'powershell.exe -NonInteractive -Command -'),
    (False, 'powershell.exe -NonInteractive -Command -'),
    (True, 'powershell.exe -Command -'),
], ids=['default', 'noninteractive', 'interactive'])
@pytest.mark.parametrize('script', [
    'Get-Service\n', 'Write-Output "caf\u00e9"\r\n',
])
@pytest.mark.parametrize('success', [False, True])
def test_RunPowerShell_DelegatesToShell(
    interactive: bool | None,
    command: str,
    script: str,
    success: bool,
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'RunShell')
    expected = CmdRes('stdout\n', 'stderr\n', 0 if success else 1, success)
    MUT.RHOVsshCon.RunShell.return_value = expected
    options = {} if (interactive is None) else {'interactive': interactive}

    result = rhovConnection.connection.RunPowerShell(script, **options)

    assert (result is expected)
    MUT.RHOVsshCon.RunShell.assert_called_once_with(
        rhovConnection.connection, command, script,
    )


@pytest.mark.parametrize('interactive, command', [
    (None, 'powershell.exe -NonInteractive -Command -'),
    (False, 'powershell.exe -NonInteractive -Command -'),
    (True, 'powershell.exe -Command -'),
], ids=['default', 'noninteractive', 'interactive'])
@pytest.mark.parametrize('exitCode', [0, 7])
def test_RunPowerShell_PreservesNativeStdinAndDisablesTTY(
    interactive: bool | None,
    command: str,
    exitCode: int,
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    connection = rhovConnection.connection
    script = 'Write-Output "caf\u00e9"\r\n'
    expected = CmdRes('stdout\n', 'stderr\n', exitCode, (exitCode == 0))
    rhovConnection.cmdExec.Run.return_value = expected
    options = {} if (interactive is None) else {'interactive': interactive}

    result = connection.RunPowerShell(script, **options)

    assert (result is expected)
    MUT.RHOVsshCon.Connect.assert_called_once_with(connection)
    rhovConnection.cmdExec.Run.assert_called_once_with(
        [
            'ssh', '-T', '-o', 'ControlMaster=no',
            '-o', f'ControlPath={connection.ctrlPath}', 'muxSock', command,
        ],
        input=script,
    )


def test_Send_ConnectsExactlyOnceAndRunsSCP(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    expected = CommandResult(True)
    rhovConnection.cmdExec.Run.return_value = expected

    result = rhovConnection.connection.Send(Path('/local/file'), 'C:/remote')

    assert (result is expected)
    MUT.RHOVsshCon.Connect.assert_called_once_with(rhovConnection.connection)
    rhovConnection.cmdExec.Run.assert_called_once_with(
        [
            'scp', '-o', 'ControlMaster=no',
            '-o', f'ControlPath={rhovConnection.connection.ctrlPath}',
            '/local/file', 'muxSock:C:/remote',
        ]
    )


def test_Recv_ConnectsExactlyOnceAndRunsSCP(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    expected = CommandResult(True)
    rhovConnection.cmdExec.Run.return_value = expected

    result = rhovConnection.connection.Recv(Path('C:/remote'), '/local/file')

    assert (result is expected)
    MUT.RHOVsshCon.Connect.assert_called_once_with(rhovConnection.connection)
    rhovConnection.cmdExec.Run.assert_called_once_with(
        [
            'scp', '-o', 'ControlMaster=no',
            '-o', f'ControlPath={rhovConnection.connection.ctrlPath}',
            'muxSock:C:/remote', '/local/file',
        ]
    )


@pytest.mark.parametrize('method', ['Connect', 'IsAlive', 'Close'])
@pytest.mark.parametrize('cmdTO', [
    None, '5', False, True, 0, -1, 0.25, 1.0,
    float('nan'), float('inf'), float('-inf'),
])
def test_Connect_IsAlive_Close_RejectInvalidCommandTimeout(
    rhovConnection: Utils.RHOVconnectionContext,
    method: str,
    cmdTO: object,
) -> None:
    with pytest.raises(
        ValueError, match='The `cmdTO` must be a positive integer',
    ):
        getattr(rhovConnection.connection, method)(cmdTO=cmdTO)

    rhovConnection.cmdExec.Run.assert_not_called()
    rhovConnection.virtCtl.Run.assert_not_called()


@pytest.mark.parametrize(('probeTO', 'interval', 'cmdTO', 'accepted'), [
    (34, 10, 5, True), (35, 10, 5, True), (36, 10, 5, True),
    (5, 1, 1, True), (6, 1, 1, True),
    (24, 10, 5, False), (4, 1, 1, False),
    (25, 10, 5, True), (5, 100, 1, True),
])
def test_Probe_RequiresOnlyCommandAllowanceForFirstAttempt(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeTO: int,
    interval: int,
    cmdTO: int,
    accepted: bool,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    connection = rhovConnection.connection

    if accepted:
        assert (connection.Probe(probeTO, interval, cmdTO) is True)
        MUT.RHOVsshCon.Connect.assert_called_once_with(connection, cmdTO)
        MUT.RHOVsshCon.Close.assert_called_once_with(connection, cmdTO)
    else:
        with pytest.raises(ValueError, match='The `probeTO` must cover'):
            connection.Probe(probeTO, interval, cmdTO)
        MUT.RHOVsshCon.Connect.assert_not_called()
        MUT.RHOVsshCon.Close.assert_not_called()
        MUT.time.monotonic.assert_not_called()
    MUT.time.sleep.assert_not_called()


@pytest.mark.parametrize(('probeTO', 'accepted'), [
    (35, False), (44, False), (45, True), (50, True),
])
def test_Probe_RetainsCommandTimeoutWhenAdmittingAttempts(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
    probeTO: int,
    accepted: bool,
) -> None:
    def Connect(connection: MUT.RHOVsshCon, cmdTO: int) -> None:
        if (probeClock.now == 10):
            probeClock.now += 10
            raise RuntimeError('not ready')

    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = Connect
    connection = rhovConnection.connection

    assert (connection.Probe(probeTO, 10, 5) is accepted)

    expectedCalls = [mock.call(connection, 5)] * (2 if accepted else 1)
    assert (MUT.RHOVsshCon.Connect.call_args_list == expectedCalls)
    assert (MUT.RHOVsshCon.Close.call_args_list == expectedCalls)
    if accepted:
        MUT.time.sleep.assert_called_once_with(10)
        assert (probeClock.now == 30)
    else:
        MUT.time.sleep.assert_not_called()
        assert (probeClock.now == 20)


def test_Probe_DoesNotStartAttemptWithoutFullCommandAllowance(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
) -> None:
    def Connect(connection: MUT.RHOVsshCon, cmdTO: int) -> None:
        probeClock.now += 21
        raise RuntimeError('not ready')

    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = Connect
    connection = rhovConnection.connection

    assert (connection.Probe(35, 10, 5) is False)

    MUT.RHOVsshCon.Connect.assert_called_once_with(connection, 5)
    MUT.RHOVsshCon.Close.assert_called_once_with(connection, 5)
    MUT.time.sleep.assert_not_called()
    assert (probeClock.now == 31)


@pytest.mark.parametrize('overrun', [0, 0.25])
def test_Probe_PreservesAdmittedRetryAfterSleepOverrun(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
    overrun: float,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = [RuntimeError('not ready'), None]

    def Sleep(duration: float) -> None:
        probeClock.now += duration + overrun

    MUT.time.sleep.side_effect = Sleep
    connection = rhovConnection.connection
    expectedCalls = [mock.call(connection, 1), mock.call(connection, 1)]

    assert (connection.Probe(6, 1, 1) is True)

    assert (MUT.RHOVsshCon.Connect.call_args_list == expectedCalls)
    assert (MUT.RHOVsshCon.Close.call_args_list == expectedCalls)
    MUT.time.sleep.assert_called_once_with(1)
    assert (probeClock.now == (11 + overrun))


@pytest.mark.parametrize('probeTO', [5, 6, 30])
def test_Probe_GivesUpWhenRemainingBudgetDoesNotExceedInterval(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
    probeTO: int,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = [
        RuntimeError('not ready'), AssertionError('unexpected retry'),
    ]
    connection = rhovConnection.connection

    assert (connection.Probe(probeTO, probeTO, 1) is False)

    MUT.RHOVsshCon.Connect.assert_called_once_with(connection, 1)
    MUT.RHOVsshCon.Close.assert_called_once_with(connection, 1)
    MUT.time.sleep.assert_not_called()
    assert (probeClock.now == 10)


@pytest.mark.parametrize('clockReadings', [
    pytest.param((10.0, float('nan')), id='nan-after-attempt'),
    pytest.param((float('nan'), 10.0), id='nan-at-start'),
    pytest.param((float('inf'), float('inf')), id='inf-minus-inf'),
])
def test_Probe_NonFiniteClockCharacterizationEndsLoopAtGuard(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    rhovTime: SimpleNamespace,
    clockReadings: tuple[float, float],
) -> None:
    '''
    Characterize the retry loop guard with non-finite mocked clock readings.

    Robustness case only, not normal monotonic clock behavior. With finite
    readings and float-exact timing values, an admitted retry wait always
    keeps the `while` guard true, so only a NaN remaining budget skips the
    early `break` and ends the loop at the guard, still reporting the probe
    failure.
    '''
    logs = mock.Mock(name='logs')
    monkeypatch.setattr(MUT, 'logs', logs)
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    rhovTime.time.monotonic.side_effect = list(clockReadings)
    MUT.RHOVsshCon.Connect.side_effect = [
        RuntimeError('not ready'), AssertionError('unexpected retry'),
    ]
    connection = rhovConnection.connection

    assert (connection.Probe(probeTO=6, interval=1, cmdTO=1) is False)

    MUT.RHOVsshCon.Connect.assert_called_once_with(connection, 1)
    MUT.RHOVsshCon.Close.assert_called_once_with(connection, 1)
    assert (
        rhovTime.time.monotonic.call_args_list == [mock.call(), mock.call()]
    )
    MUT.time.sleep.assert_called_once_with(1)
    target = 'user@vm-one.namespace.cluster-one'
    assert (logs.mock_calls == [
        mock.call.info(f'SSH not ready on {target}, retrying in 1s...'),
        mock.call.warning(f'SSH probe failed on {target} (budget: 6s)'),
    ])
