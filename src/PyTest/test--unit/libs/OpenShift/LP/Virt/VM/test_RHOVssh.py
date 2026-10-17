from pathlib import Path
from unittest import mock

import pytest

import libs.OpenShift.LP.Virt.VM.RHOVssh as MUT
from libs.Utils.CmdExec import CmdRes
import Utils


def CommandResult(success: bool, stderr: str = '') -> CmdRes:
    return CmdRes('', stderr, 0 if success else 1, success)


def test_Init_IsolatesControlSocketsByNamespace(
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    first = rhovConnection.connection
    second = MUT.RHOVsshCon(
        'other-ns', first.host, first.user, first.idFile,
        virtCtl=rhovConnection.virtCtl, cmdExec=rhovConnection.cmdExec,
    )

    assert second.ctrlPath == '/tmp/ssh..user@vm-one.other-ns..sock'
    assert first.ctrlPath != second.ctrlPath


def test_Init_UsesSuppliedCollaboratorsAndExpandsIdentityPath(
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    connection = rhovConnection.connection

    assert connection.ns == 'namespace'
    assert connection.host == 'vm-one'
    assert connection.user == 'user'
    assert connection.idFile == str(Path('~/key').expanduser())
    assert connection.target == 'user@vm-one'
    assert connection.ctrlPath == '/tmp/ssh..user@vm-one.namespace..sock'
    assert connection.virtCtl is rhovConnection.virtCtl
    assert connection.cmdExec is rhovConnection.cmdExec


def test_Init_CreatesDefaultsWithExplicitKubeconfig(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VirtCtlCLI')
    Utils.AutoPatch(monkeypatch, MUT, 'CmdExec')

    connection = MUT.RHOVsshCon(
        'ns',
        'vm',
        'user',
        '$HOME/key',
        kubeconfig='/kube',
    )

    MUT.VirtCtlCLI.assert_called_once_with(n='ns', kubeconfig='/kube')
    MUT.CmdExec.assert_called_once_with()
    assert connection.virtCtl is MUT.VirtCtlCLI.return_value
    assert connection.cmdExec is MUT.CmdExec.return_value


def test_Init_ReadsKubeconfigEnvironment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('KUBECONFIG', '/from-env')
    Utils.AutoPatch(monkeypatch, MUT, 'VirtCtlCLI')
    Utils.AutoPatch(monkeypatch, MUT, 'CmdExec')

    MUT.RHOVsshCon('ns', 'vm', 'user', 'key')

    MUT.VirtCtlCLI.assert_called_once_with(n='ns', kubeconfig='/from-env')


def test_Init_OmitsAbsentKubeconfig(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('KUBECONFIG', raising=False)
    Utils.AutoPatch(monkeypatch, MUT, 'VirtCtlCLI')
    Utils.AutoPatch(monkeypatch, MUT, 'CmdExec')

    MUT.RHOVsshCon('ns', 'vm', 'user', 'key')

    MUT.VirtCtlCLI.assert_called_once_with(n='ns')


@pytest.mark.parametrize('expected', [True, False])
def test_IsAlive_ReturnsCommandSuccess(
    rhovConnection: Utils.RHOVconnectionContext,
    expected: bool,
) -> None:
    rhovConnection.cmdExec.Run.return_value = CommandResult(expected)

    assert rhovConnection.connection.IsAlive() is expected
    rhovConnection.cmdExec.Run.assert_called_once_with(
        [
            'ssh',
            '-O',
            'check',
            '-o',
            'ControlPath=/tmp/ssh..user@vm-one.namespace..sock',
            'muxSock',
        ]
    )


def test_Connect_ReturnsWhenAlreadyAlive(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'IsAlive')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.IsAlive.return_value = True

    rhovConnection.connection.Connect()

    MUT.RHOVsshCon.IsAlive.assert_called_once_with(rhovConnection.connection)
    MUT.RHOVsshCon.Close.assert_not_called()
    rhovConnection.virtCtl.Run.assert_not_called()


def test_Connect_ClosesStaleSocketAndOpensTunnel(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'IsAlive')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.IsAlive.return_value = False
    rhovConnection.virtCtl.Run.return_value = CommandResult(True)

    rhovConnection.connection.Connect()

    MUT.RHOVsshCon.Close.assert_called_once_with(rhovConnection.connection)
    rhovConnection.virtCtl.Run.assert_called_once_with(
        'ssh',
        '-t',
        '-o UserKnownHostsFile=/dev/null',
        '-t',
        '-o StrictHostKeyChecking=no',
        '-t',
        '-o ControlMaster=yes',
        '-t',
        '-o ControlPath=/tmp/ssh..user@vm-one.namespace..sock',
        '-t',
        '-o ControlPersist=2h',
        '-t',
        '-N',
        '-i',
        rhovConnection.connection.idFile,
        'user@vm/vm-one',
    )


def test_Connect_RaisesWithTunnelError(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'IsAlive')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.IsAlive.return_value = False
    rhovConnection.virtCtl.Run.return_value = CommandResult(
        False,
        'connection denied',
    )

    with pytest.raises(RuntimeError, match='connection denied'):
        rhovConnection.connection.Connect()


def test_Close_RequestsControlMasterExit(
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    rhovConnection.connection.Close()

    rhovConnection.cmdExec.Run.assert_called_once_with(
        [
            'ssh',
            '-O',
            'exit',
            '-o',
            'ControlPath=/tmp/ssh..user@vm-one.namespace..sock',
            'muxSock',
        ]
    )


def test_Probe_RetriesThenSucceeds(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    Utils.AutoPatch(monkeypatch, MUT.time, 'monotonic')
    Utils.AutoPatch(monkeypatch, MUT.time, 'sleep')
    MUT.time.monotonic.side_effect = [10, 11, 12, 13]
    MUT.RHOVsshCon.Connect.side_effect = [RuntimeError('not ready'), None]

    assert rhovConnection.connection.Probe(timeout=5, interval=2)
    assert MUT.RHOVsshCon.Connect.call_count == 2
    MUT.RHOVsshCon.Close.assert_called_once_with(rhovConnection.connection)
    MUT.time.sleep.assert_called_once_with(2)


def test_Probe_ReturnsFalseAfterTimeout(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.time, 'monotonic')
    MUT.time.monotonic.side_effect = [10, 16]

    assert not rhovConnection.connection.Probe(timeout=5)
    MUT.RHOVsshCon.Connect.assert_not_called()


@pytest.mark.parametrize('field', ['timeout', 'interval'])
@pytest.mark.parametrize(
    'value',
    [
        False, None, '1', 'bad', [], {}, 1j, 0, 0.0, -1, -0.5,
        float('nan'),
    ],
)
def test_Probe_RejectsInvalidTimingBeforeConnection(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
    field: str,
    value: object,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')

    with pytest.raises(
        ValueError, match=f'^{field} must be a positive number',
    ):
        rhovConnection.connection.Probe(**{field: value})

    MUT.RHOVsshCon.Connect.assert_not_called()
    MUT.RHOVsshCon.Close.assert_not_called()
    MUT.time.monotonic.assert_not_called()
    MUT.time.sleep.assert_not_called()
    rhovConnection.cmdExec.Run.assert_not_called()
    rhovConnection.virtCtl.Run.assert_not_called()


@pytest.mark.parametrize(
    ('timeout', 'interval'), [(5, 2), (0.5, 0.125), (True, 0.25), (2, True)],
)
def test_Probe_AcceptsPositiveTimingAndPreservesSuccess(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
    timeout: int | float,
    interval: int | float,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = [RuntimeError('not ready'), None]

    assert rhovConnection.connection.Probe(timeout, interval) is True
    assert MUT.RHOVsshCon.Connect.call_count == 2
    MUT.RHOVsshCon.Close.assert_called_once_with(rhovConnection.connection)
    MUT.time.sleep.assert_called_once_with(interval)
    assert probeClock.now == 10 + interval


def test_Probe_CapsSleepToRemainingTimeout(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = RuntimeError('not ready')

    assert rhovConnection.connection.Probe(timeout=0.5, interval=2) is False
    MUT.time.sleep.assert_called_once_with(0.5)
    MUT.RHOVsshCon.Connect.assert_called_once_with(rhovConnection.connection)
    MUT.RHOVsshCon.Close.assert_not_called()
    assert probeClock.now == 10.5
    assert 'SSH probe timeout on user@vm-one after 0.5s' in caplog.text


def test_Probe_CapsFinalSleepAfterMultipleFailedAttempts(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = RuntimeError('not ready')

    assert rhovConnection.connection.Probe(timeout=2.5, interval=1) is False
    assert MUT.time.sleep.call_args_list == [
        mock.call(1), mock.call(1), mock.call(0.5),
    ]
    assert MUT.RHOVsshCon.Connect.call_args_list == [
        mock.call(rhovConnection.connection),
    ] * 3
    MUT.RHOVsshCon.Close.assert_not_called()
    assert probeClock.now == 12.5


def test_Probe_SubtractsAttemptDurationFromRetryWait(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
) -> None:
    def FailedConnect(connection: MUT.RHOVsshCon) -> None:
        probeClock.now += 0.25
        raise RuntimeError('not ready')

    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    MUT.RHOVsshCon.Connect.side_effect = FailedConnect

    assert rhovConnection.connection.Probe(timeout=0.5, interval=2) is False
    MUT.time.sleep.assert_called_once_with(0.25)
    MUT.RHOVsshCon.Connect.assert_called_once_with(rhovConnection.connection)
    assert probeClock.now == 10.5


@pytest.mark.parametrize('attemptDuration', [0.5, 0.75])
def test_Probe_SkipsSleepAndFurtherAttemptsAfterDeadline(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
    attemptDuration: float,
) -> None:
    def FailedConnect(connection: MUT.RHOVsshCon) -> None:
        probeClock.now += attemptDuration
        raise RuntimeError('not ready')

    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = FailedConnect

    assert rhovConnection.connection.Probe(timeout=0.5, interval=2) is False
    MUT.time.sleep.assert_not_called()
    MUT.RHOVsshCon.Connect.assert_called_once_with(rhovConnection.connection)
    MUT.RHOVsshCon.Close.assert_not_called()


def test_Probe_PreservesSuccessfulAttemptThatConsumesDeadline(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
) -> None:
    def SuccessfulConnect(connection: MUT.RHOVsshCon) -> None:
        probeClock.now += 1

    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = SuccessfulConnect

    assert rhovConnection.connection.Probe(timeout=0.5, interval=2) is True
    MUT.RHOVsshCon.Connect.assert_called_once_with(rhovConnection.connection)
    MUT.RHOVsshCon.Close.assert_called_once_with(rhovConnection.connection)
    MUT.time.sleep.assert_not_called()


def test_RunShell_ConnectsAndPassesStdin(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    expected = CommandResult(True)
    rhovConnection.cmdExec.Run.return_value = expected

    result = rhovConnection.connection.RunShell('hostname', stdin='input\n')

    assert result is expected
    MUT.RHOVsshCon.Connect.assert_called_once_with(rhovConnection.connection)
    rhovConnection.cmdExec.Run.assert_called_once_with(
        [
            'ssh',
            '-o',
            'ControlMaster=no',
            '-o',
            'ControlPath=/tmp/ssh..user@vm-one.namespace..sock',
            'muxSock',
            'hostname',
        ],
        input='input\n',
    )


def test_RunPowerShell_DelegatesToShell(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'RunShell')
    expected = CommandResult(True)
    MUT.RHOVsshCon.RunShell.return_value = expected

    result = rhovConnection.connection.RunPowerShell('Get-Service')

    assert result is expected
    MUT.RHOVsshCon.RunShell.assert_called_once_with(
        rhovConnection.connection,
        'powershell.exe -NonInteractive -File -',
        'Get-Service',
    )


def test_Send_ConnectsExactlyOnceAndRunsSCP(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    expected = CommandResult(True)
    rhovConnection.cmdExec.Run.return_value = expected

    result = rhovConnection.connection.Send(Path('/local/file'), 'C:/remote')

    assert result is expected
    MUT.RHOVsshCon.Connect.assert_called_once_with(rhovConnection.connection)
    rhovConnection.cmdExec.Run.assert_called_once_with(
        [
            'scp',
            '-o',
            'ControlMaster=no',
            '-o',
            'ControlPath=/tmp/ssh..user@vm-one.namespace..sock',
            '/local/file',
            'muxSock:C:/remote',
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

    assert result is expected
    MUT.RHOVsshCon.Connect.assert_called_once_with(rhovConnection.connection)
    rhovConnection.cmdExec.Run.assert_called_once_with(
        [
            'scp',
            '-o',
            'ControlMaster=no',
            '-o',
            'ControlPath=/tmp/ssh..user@vm-one.namespace..sock',
            'muxSock:C:/remote',
            '/local/file',
        ]
    )
