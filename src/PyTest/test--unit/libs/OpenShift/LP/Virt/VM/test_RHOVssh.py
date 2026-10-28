import hashlib
from pathlib import Path
import socket
import subprocess
from unittest import mock
import uuid

import pytest

import libs.OpenShift.LP.Virt.VM.RHOVssh as MUT
from libs.Utils.CmdExec import CmdRes
import Utils


def CommandResult(success: bool, stderr: str = '') -> CmdRes:
    return CmdRes('', stderr, 0 if success else 1, success)


@pytest.mark.parametrize('method,operation', [
    ('IsAlive', 'check'), ('Close', 'exit'),
])
@pytest.mark.parametrize('exitCode', [0, 7])
def test_IsAlive_Close_SuppressOnlyNativeNonzeroWarnings(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    rhovConnection: Utils.RHOVconnectionContext,
    method: str, operation: str, exitCode: int,
) -> None:
    Utils.AutoPatch(monkeypatch, subprocess, 'run')
    subprocess.run.return_value = subprocess.CompletedProcess(
        [], exitCode, 'synthetic-output', 'synthetic-error',
    )
    connection = rhovConnection.connection
    executor = MUT.CmdExec()
    executor.Run = mock.Mock(wraps=executor.Run)
    connection.cmdExec = executor
    result = getattr(connection, method)()
    command = [
        'ssh', '-O', operation, '-o',
        f'ControlPath={connection.ctrlPath}', 'muxSock',
    ]
    executor.Run.assert_called_once_with(command, warnOnFail=False)
    subprocess.run.assert_called_once_with(
        command, input=None, capture_output=True, shell=False, text=True,
    )
    assert (result is ((exitCode == 0) if (method == 'IsAlive') else None))
    assert (caplog.text == '')


def test_Connect_OnlyControlChecksOptOutOfNativeWarnings(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, subprocess, 'run')
    subprocess.run.return_value = subprocess.CompletedProcess([], 0, '', '')
    connection = rhovConnection.connection
    executor = MUT.CmdExec()
    connection.cmdExec = executor
    connection.virtCtl = MUT.VirtCtlCLI('synthetic-virtctl', cmdExec=executor)
    subprocess.run.reset_mock()
    executor.Run = mock.Mock(wraps=executor.Run)
    subprocess.run.side_effect = [
        subprocess.CompletedProcess([], 7, '', 'synthetic-check-error'),
        subprocess.CompletedProcess([], 7, '', 'synthetic-close-error'),
        subprocess.CompletedProcess([], 9, '', 'synthetic-tunnel-error'),
    ]

    with pytest.raises(RuntimeError, match='synthetic-tunnel-error'):
        connection.Connect()

    assert (executor.Run.call_count == 3)
    for call, operation in zip(executor.Run.call_args_list[:2],
                               ('check', 'exit')):
        assert (call == mock.call([
            'ssh', '-O', operation, '-o',
            f'ControlPath={connection.ctrlPath}', 'muxSock',
        ], warnOnFail=False))
    tunnel = executor.Run.call_args_list[2]
    assert (tunnel.kwargs == {
        'input': None, 'isSecret': False, 'spPars': None,
    })
    assert (tunnel.args[0] == [
        'synthetic-virtctl', 'ssh', '-t', '-o UserKnownHostsFile=/dev/null',
        '-t', '-o StrictHostKeyChecking=no', '-t', '-o ControlMaster=yes',
        '-t', f'-o ControlPath={connection.ctrlPath}',
        '-t', f'-o ControlPersist={connection.ctrlPersist}', '-t', '-N',
        '-i', connection.idFile, f'{connection.user}@vm/{connection.host}',
    ])
    assert (caplog.messages == [
        'Command failed with exit code 9 (Try 1/1): synthetic-tunnel-error',
    ])
    assert (subprocess.run.call_count == 3)


def test_RunShell_RetainsNativeFailureWarning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, subprocess, 'run')
    subprocess.run.side_effect = [
        subprocess.CompletedProcess([], 0, '', ''),
        subprocess.CompletedProcess([], 9, 'synthetic-out', 'synthetic-error'),
    ]
    connection = rhovConnection.connection
    executor = MUT.CmdExec()
    executor.Run = mock.Mock(wraps=executor.Run)
    connection.cmdExec = executor
    result = connection.RunShell('synthetic-command', stdin='synthetic-input')

    assert (result == CmdRes('synthetic-out', 'synthetic-error', 9, False))
    assert (executor.Run.call_count == 2)
    assert (executor.Run.call_args_list[0].kwargs == {'warnOnFail': False})
    assert (executor.Run.call_args_list[1] == mock.call([
        'ssh', '-T', '-o', 'ControlMaster=no',
        '-o', f'ControlPath={connection.ctrlPath}', 'muxSock',
        'synthetic-command',
    ], input='synthetic-input'))
    assert (caplog.messages == [
        'Command failed with exit code 9 (Try 1/1): synthetic-error',
    ])


def test_Init_IsolatesControlSocketsByNamespace(
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    first = rhovConnection.connection
    second = MUT.RHOVsshCon(
        'other-ns', first.host, first.user, first.idFile,
        virtCtl=rhovConnection.virtCtl, cmdExec=rhovConnection.cmdExec,
    )

    assert (second.ctrlPath == (
        f'/tmp/ssh..{hashlib.sha256(b"user@vm-one.other-ns").hexdigest()}..sock'
    ))
    assert (first.ctrlPath != second.ctrlPath)


def test_Init_UsesSuppliedCollaboratorsAndExpandsIdentityPath(
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    connection = rhovConnection.connection

    assert (connection.ns == 'namespace')
    assert (connection.host == 'vm-one')
    assert (connection.user == 'user')
    assert (connection.idFile == str(Path('~/key').expanduser()))
    assert (connection.target == 'user@vm-one.namespace')
    assert (connection.ctrlPath == (
        f'/tmp/ssh..{hashlib.sha256(b"user@vm-one.namespace").hexdigest()}..sock'
    ))
    assert (len(connection.ctrlPath.encode('utf-8')) == 80)
    assert (connection.virtCtl is rhovConnection.virtCtl)
    assert (connection.cmdExec is rhovConnection.cmdExec)


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
    assert (connection.virtCtl is MUT.VirtCtlCLI.return_value)
    assert (connection.cmdExec is MUT.CmdExec.return_value)


@pytest.mark.parametrize('ns,host,user', [
    ('other-ns', 'vm-one', 'user'),
    ('namespace', 'vm-two', 'user'),
    ('namespace', 'vm-one', 'other-user'),
])
def test_Init_ControlSocketIdentityIsStableAndDistinct(
    rhovConnection: Utils.RHOVconnectionContext,
    ns: str, host: str, user: str,
) -> None:
    first = rhovConnection.connection
    changed = MUT.RHOVsshCon(
        ns, host, user, first.idFile,
        virtCtl=rhovConnection.virtCtl, cmdExec=rhovConnection.cmdExec,
    )
    repeated = MUT.RHOVsshCon(
        ns, host, user, first.idFile,
        virtCtl=rhovConnection.virtCtl, cmdExec=rhovConnection.cmdExec,
    )

    assert (changed.target == f'{user}@{host}.{ns}')
    assert (len(changed.ctrlPath.encode('utf-8')) == 80)
    assert (changed.ctrlPath == repeated.ctrlPath)
    assert (changed.ctrlPath != first.ctrlPath)


@pytest.mark.parametrize('host,ns', [('v' * 40, 'n' * 60), ('v' * 63, 'n' * 63)])
def test_Init_LongNamesAllowLocalSocketWithTemporarySuffix(
    rhovConnection: Utils.RHOVconnectionContext,
    host: str, ns: str,
) -> None:
    connection = MUT.RHOVsshCon(
        ns, host, 'qa-usr', '/synthetic/not-read',
        virtCtl=rhovConnection.virtCtl, cmdExec=rhovConnection.cmdExec,
    )
    socketPath = connection.ctrlPath + '.' + uuid.uuid4().hex[:16]

    assert (len(connection.ctrlPath.encode('utf-8')) == 80)
    assert (len(socketPath.encode('utf-8')) == 97)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(socketPath)
        try:
            assert Path(socketPath).is_socket()
        finally:
            Path(socketPath).unlink()
    rhovConnection.virtCtl.Run.assert_not_called()
    rhovConnection.cmdExec.Run.assert_not_called()


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

    assert (rhovConnection.connection.IsAlive() is expected)
    rhovConnection.cmdExec.Run.assert_called_once_with(
        [
            'ssh',
            '-O',
            'check',
            '-o',
            f'ControlPath={rhovConnection.connection.ctrlPath}',
            'muxSock',
        ],
        warnOnFail=False,
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
    caplog: pytest.LogCaptureFixture,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'IsAlive')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.IsAlive.return_value = False
    rhovConnection.virtCtl.Run.return_value = CommandResult(True)

    with caplog.at_level('INFO', logger=MUT.__name__):
        rhovConnection.connection.Connect()

    assert ('user@vm-one.namespace' in caplog.text)

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
        f'-o ControlPath={rhovConnection.connection.ctrlPath}',
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

    with pytest.raises(RuntimeError, match='connection denied') as caught:
        rhovConnection.connection.Connect()
    assert ('user@vm-one.namespace' in str(caught.value))


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
            f'ControlPath={rhovConnection.connection.ctrlPath}',
            'muxSock',
        ],
        warnOnFail=False,
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
    assert (MUT.RHOVsshCon.Connect.call_count == 2)
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

    assert (rhovConnection.connection.Probe(timeout, interval) is True)
    assert (MUT.RHOVsshCon.Connect.call_count == 2)
    MUT.RHOVsshCon.Close.assert_called_once_with(rhovConnection.connection)
    MUT.time.sleep.assert_called_once_with(interval)
    assert (probeClock.now == (10 + interval))


def test_Probe_CapsSleepToRemainingTimeout(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = RuntimeError('not ready')

    assert (rhovConnection.connection.Probe(timeout=0.5, interval=2) is False)
    MUT.time.sleep.assert_called_once_with(0.5)
    MUT.RHOVsshCon.Connect.assert_called_once_with(rhovConnection.connection)
    MUT.RHOVsshCon.Close.assert_not_called()
    assert (probeClock.now == 10.5)
    assert ('SSH probe timeout on user@vm-one.namespace after 0.5s'
        in caplog.text)


def test_Probe_CapsFinalSleepAfterMultipleFailedAttempts(
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
    probeClock: Utils.FakeClock,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Close')
    MUT.RHOVsshCon.Connect.side_effect = RuntimeError('not ready')

    assert (rhovConnection.connection.Probe(timeout=2.5, interval=1) is False)
    assert (MUT.time.sleep.call_args_list == [
        mock.call(1), mock.call(1), mock.call(0.5),
    ])
    assert (MUT.RHOVsshCon.Connect.call_args_list == ([
        mock.call(rhovConnection.connection),
    ] * 3))
    MUT.RHOVsshCon.Close.assert_not_called()
    assert (probeClock.now == 12.5)


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

    assert (rhovConnection.connection.Probe(timeout=0.5, interval=2) is False)
    MUT.time.sleep.assert_called_once_with(0.25)
    MUT.RHOVsshCon.Connect.assert_called_once_with(rhovConnection.connection)
    assert (probeClock.now == 10.5)


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

    assert (rhovConnection.connection.Probe(timeout=0.5, interval=2) is False)
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

    assert (rhovConnection.connection.Probe(timeout=0.5, interval=2) is True)
    MUT.RHOVsshCon.Connect.assert_called_once_with(rhovConnection.connection)
    MUT.RHOVsshCon.Close.assert_called_once_with(rhovConnection.connection)
    MUT.time.sleep.assert_not_called()


@pytest.mark.parametrize('tty, option', [
    (None, '-T'), (False, '-T'), (True, '-t'),
], ids=['default', 'no-tty', 'tty'])
@pytest.mark.parametrize('stdin', [None, 'input\n', 'last line'])
@pytest.mark.parametrize('success', [False, True])
def test_RunShell_ConnectsAndPassesStdin(
    tty: bool | None, option: str, stdin: str | None, success: bool,
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
            'ssh',
            option,
            '-o',
            'ControlMaster=no',
            '-o',
            f'ControlPath={rhovConnection.connection.ctrlPath}',
            'muxSock',
            'hostname',
        ],
        input=stdin,
    )


@pytest.mark.parametrize('interactive, command', [
    (None, 'powershell.exe -NonInteractive -Command -'),
    (False, 'powershell.exe -NonInteractive -Command -'),
    (True, 'powershell.exe -Command -'),
], ids=['default', 'noninteractive', 'interactive'])
@pytest.mark.parametrize('script', [
    'Get-Service\n', 'Write-Output "caf\u00e9"\r\n',
])
@pytest.mark.parametrize('success', [False, True])
def test_RunPowerShell_DelegatesToShell(
    interactive: bool | None, command: str, script: str, success: bool,
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
        rhovConnection.connection,
        command,
        script,
    )


@pytest.mark.parametrize('interactive, command', [
    (None, 'powershell.exe -NonInteractive -Command -'),
    (False, 'powershell.exe -NonInteractive -Command -'),
    (True, 'powershell.exe -Command -'),
], ids=['default', 'noninteractive', 'interactive'])
@pytest.mark.parametrize('exitCode', [0, 7])
def test_RunPowerShell_PreservesNativeStdinAndDisablesTTY(
    interactive: bool | None, command: str, exitCode: int,
    monkeypatch: pytest.MonkeyPatch,
    rhovConnection: Utils.RHOVconnectionContext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.RHOVsshCon, 'Connect')
    Utils.AutoPatch(monkeypatch, subprocess, 'run')
    subprocess.run.return_value = subprocess.CompletedProcess(
        ['synthetic'], exitCode, 'stdout\n', 'stderr\n',
    )
    connection = rhovConnection.connection
    connection.cmdExec = MUT.CmdExec()
    script = 'Write-Output "caf\u00e9"\r\n'
    options = {} if (interactive is None) else {'interactive': interactive}

    result = connection.RunPowerShell(script, **options)

    MUT.RHOVsshCon.Connect.assert_called_once_with(connection)
    subprocess.run.assert_called_once_with(
        [
            'ssh', '-T', '-o', 'ControlMaster=no',
            '-o', f'ControlPath={connection.ctrlPath}', 'muxSock', command,
        ],
        input=script, capture_output=True, shell=False, text=True,
    )
    assert (result == CmdRes(
        'stdout\n', 'stderr\n', exitCode, (exitCode == 0),
    ))


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
            'scp',
            '-o',
            'ControlMaster=no',
            '-o',
            f'ControlPath={rhovConnection.connection.ctrlPath}',
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

    assert (result is expected)
    MUT.RHOVsshCon.Connect.assert_called_once_with(rhovConnection.connection)
    rhovConnection.cmdExec.Run.assert_called_once_with(
        [
            'scp',
            '-o',
            'ControlMaster=no',
            '-o',
            f'ControlPath={rhovConnection.connection.ctrlPath}',
            'muxSock:C:/remote',
            '/local/file',
        ]
    )
