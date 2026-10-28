from types import SimpleNamespace
from unittest import mock

import pytest

from libs.OpenShift.LP.Virt.VM.RHOVssh import RHOVsshCon
import libs.OpenShift.LP.Virt.VM.VM as MUT
from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.Utils.CmdExec import CmdRes
import Utils


def CommandResult(
    success: bool,
    stdout: str = '',
    stderr: str = '',
) -> CmdRes:
    return CmdRes(stdout, stderr, 0 if success else 1, success)


def OCmock() -> mock.MagicMock:
    return mock.create_autospec(OCcli, instance=True)


@pytest.fixture(name='provisionClock')
def ProvisionClock(monkeypatch: pytest.MonkeyPatch) -> Utils.FakeClock:
    clock = Utils.FakeClock()
    monkeypatch.setattr(MUT, 'time', SimpleNamespace(
        monotonic=mock.Mock(side_effect=clock.Now),
        sleep=mock.Mock(side_effect=clock.Sleep),
    ))
    return clock


@pytest.mark.parametrize('sshFailed', [False, True])
@pytest.mark.parametrize('cleanupFailed', [False, True])
@pytest.mark.parametrize('primary', [False, True])
def test_CleanUpVMs_ClosesSSHAndDelegatesDespiteErrors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    sshFailed: bool, cleanupFailed: bool, primary: bool,
) -> None:
    vm = SimpleNamespace(name='example', RmvSSH=mock.Mock())
    if sshFailed: vm.RmvSSH.side_effect = RuntimeError('SSH failed')
    cleanup = mock.Mock(
        side_effect=RuntimeError('resources failed')
        if cleanupFailed
        else None
    )
    monkeypatch.setattr(MUT.OCcli, 'CleanUpRes', cleanup)
    if primary:
        monkeypatch.setattr(
            MUT.sys,
            'exc_info',
            lambda: (RuntimeError, RuntimeError('primary'), None),
        )
    owned = [(OCmock(), 'VirtualMachine/example', 'owned')]
    if ((sshFailed or cleanupFailed) and not primary):
        with pytest.raises(RuntimeError, match='VM cleanup failed'):
            MUT.VM.CleanUpVMs([vm], owned)
    else:
        MUT.VM.CleanUpVMs([vm], owned)
    vm.RmvSSH.assert_called_once_with()
    cleanup.assert_called_once_with(owned)
    if ((sshFailed or cleanupFailed) and primary):
        assert ('preserving primary exception' in caplog.text)


def test_Init_UsesSuppliedClients(virtualMachine: Utils.VMcontext) -> None:
    vm = virtualMachine.vm

    assert (vm.name == 'vm-one')
    assert (vm.namespace == 'namespace')
    assert (vm.cli.oc is virtualMachine.oc)
    assert (vm.cli.virtCtl is virtualMachine.virtCtl)
    assert (vm.sshUsr == 'qa-usr')
    assert (vm.sshKey is None)
    assert (vm._ssh is None)


def test_Init_CreatesDefaultClients(monkeypatch: pytest.MonkeyPatch) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'OCcli')
    Utils.AutoPatch(monkeypatch, MUT, 'VirtCtlCLI')

    vm = MUT.VM('vm', 'ns', sshUsr='admin', sshKey='/key')

    MUT.OCcli.assert_called_once_with(n='ns')
    MUT.VirtCtlCLI.assert_called_once_with(n='ns')
    assert (vm.cli.oc is MUT.OCcli.return_value)
    assert (vm.cli.virtCtl is MUT.VirtCtlCLI.return_value)


def test__Status_ReturnsValueOnlyForSuccess(
    virtualMachine: Utils.VMcontext,
) -> None:
    virtualMachine.oc.Run.return_value = CommandResult(True, "'Running'")
    assert (virtualMachine.vm._Status() == 'Running')
    virtualMachine.oc.Run.assert_called_once_with(
        'get',
        'VirtualMachine/vm-one',
        o="jsonpath='{.status.printableStatus}'",
    )

    virtualMachine.oc.Run.return_value = CommandResult(False, "'Stopped'")
    assert (virtualMachine.vm._Status() == '')


@pytest.mark.parametrize(
    'status',
    ['Running', 'Starting', 'Scheduling', 'Migrating'],
)
@pytest.mark.parametrize('timeout', [None, '2m'])
@pytest.mark.parametrize('waitSuccess', [False, True])
def test_Start_SkipsNativeStartButWaitsForActiveVM(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
    status: str,
    timeout: str | None,
    waitSuccess: bool,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.VM, '_Status')
    MUT.VM._Status.return_value = status
    virtualMachine.oc.Run.return_value = CommandResult(waitSuccess)

    result = (
        virtualMachine.vm.Start() if (timeout is None)
        else virtualMachine.vm.Start(timeout=timeout)
    )
    assert (result is waitSuccess)
    virtualMachine.virtCtl.Run.assert_not_called()
    virtualMachine.oc.Run.assert_called_once_with(
        'wait', 'VirtualMachine/vm-one', **{'for': 'condition=Ready'},
        timeout='5m' if (timeout is None) else timeout,
    )


@pytest.mark.parametrize('status', ['Stopped', 'Provisioning', ''])
@pytest.mark.parametrize('timeout', [None, '2m'])
@pytest.mark.parametrize('startSuccess', [False, True])
@pytest.mark.parametrize('waitSuccess', [False, True])
def test_Start_WaitsOnlyAfterNativeStartSucceeds(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
    provisionClock: Utils.FakeClock,
    status: str,
    timeout: str | None,
    startSuccess: bool,
    waitSuccess: bool,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.VM, '_Status')
    MUT.VM._Status.return_value = status
    if (status == 'Provisioning'):
        MUT.VM._Status.side_effect = ['Provisioning', 'Stopped']
    virtualMachine.virtCtl.Run.return_value = CommandResult(startSuccess)
    virtualMachine.oc.Run.return_value = CommandResult(waitSuccess)

    result = (
        virtualMachine.vm.Start() if (timeout is None)
        else virtualMachine.vm.Start(timeout=timeout)
    )
    assert (result is (startSuccess and waitSuccess))
    elapsed = 5.0 if (status == 'Provisioning') else 0.0
    assert (provisionClock.now == 10.0 + elapsed)
    virtualMachine.virtCtl.Run.assert_called_once_with('start', 'vm-one')
    if (not startSuccess): virtualMachine.oc.Run.assert_not_called()
    else:
        virtualMachine.oc.Run.assert_called_once_with(
            'wait', 'VirtualMachine/vm-one', **{'for': 'condition=Ready'},
            timeout='5m' if (timeout is None) else timeout,
        )


@pytest.mark.parametrize(
    'status',
    ['Stopped', 'Running', 'Starting', 'Scheduling', 'Migrating', ''],
)
@pytest.mark.parametrize('timeout', [None, '2m'])
@pytest.mark.parametrize('waitSuccess', [False, True])
def test_Start_SleepsRemainingProvisioningBudgetBeforeTransition(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
    provisionClock: Utils.FakeClock,
    status: str,
    timeout: str | None,
    waitSuccess: bool,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.VM, '_Status')
    MUT.VM._Status.side_effect = ['Provisioning', 'Provisioning', status]
    virtualMachine.virtCtl.Run.return_value = CommandResult(True)
    virtualMachine.oc.Run.return_value = CommandResult(waitSuccess)
    sequence = mock.Mock()
    sequence.attach_mock(MUT.VM._Status, 'status')
    sequence.attach_mock(MUT.time.sleep, 'sleep')
    sequence.attach_mock(virtualMachine.virtCtl.Run, 'native')
    sequence.attach_mock(virtualMachine.oc.Run, 'oc')

    result = (
        virtualMachine.vm.Start(provTO=7.0) if (timeout is None)
        else virtualMachine.vm.Start(timeout=timeout, provTO=7.0)
    )

    expected = [
        mock.call.status(virtualMachine.vm), mock.call.sleep(5.0),
        mock.call.status(virtualMachine.vm), mock.call.sleep(2.0),
        mock.call.status(virtualMachine.vm),
    ]
    if (status in ('Stopped', '')):
        expected.append(mock.call.native('start', 'vm-one'))
    expected.append(mock.call.oc(
        'wait', 'VirtualMachine/vm-one', **{'for': 'condition=Ready'},
        timeout='5m' if (timeout is None) else timeout,
    ))
    assert (sequence.mock_calls == expected)
    assert (result is waitSuccess)
    assert (provisionClock.now == 17.0)


@pytest.mark.parametrize(
    'provTO,queryLatency,sleeps,elapsed',
    [
        (None, 0.0, [5.0] * 360, 1800.0),
        (12.0, 0.0, [5.0, 5.0, 2.0], 12.0),
        (12.0, 2.0, [5.0, 3.0], 14.0),
        (12.0, 6.0, [5.0], 17.0),
        (3.0, 4.0, [], 4.0),
        (0.0, 0.0, [], 0.0),
    ],
)
def test_Start_ProvisioningTimeoutIncludesQueryLatencyWithoutNativeStart(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
    provisionClock: Utils.FakeClock,
    provTO: float | None,
    queryLatency: float,
    sleeps: list[float],
    elapsed: float,
) -> None:
    def ReadStatus(vm: MUT.VM) -> str:
        assert (vm is virtualMachine.vm)
        provisionClock.Sleep(queryLatency)
        return 'Provisioning'

    Utils.AutoPatch(monkeypatch, MUT.VM, '_Status')
    MUT.VM._Status.side_effect = ReadStatus

    result = (
        virtualMachine.vm.Start() if (provTO is None)
        else virtualMachine.vm.Start(provTO=provTO)
    )

    assert (result is False)
    assert (MUT.time.sleep.call_args_list == [
        mock.call(wait) for wait in sleeps
    ])
    assert (MUT.VM._Status.call_count == len(sleeps) + 1)
    assert (MUT.time.monotonic.call_count == len(sleeps) + 2)
    assert (provisionClock.now == 10.0 + elapsed)
    virtualMachine.virtCtl.Run.assert_not_called()
    virtualMachine.oc.Run.assert_not_called()


@pytest.mark.parametrize('status', ['Stopped', 'Running', ''])
def test_Start_UsesFinalQueryStatusBeforeProvisioningDeadlineCheck(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
    provisionClock: Utils.FakeClock,
    status: str,
) -> None:
    statuses = iter(['Provisioning', status])

    def ReadStatus(vm: MUT.VM) -> str:
        assert (vm is virtualMachine.vm)
        provisionClock.Sleep(3.0)
        return next(statuses)

    Utils.AutoPatch(monkeypatch, MUT.VM, '_Status')
    MUT.VM._Status.side_effect = ReadStatus
    virtualMachine.virtCtl.Run.return_value = CommandResult(True)
    virtualMachine.oc.Run.return_value = CommandResult(True)

    assert (virtualMachine.vm.Start(timeout='2m', provTO=7.0) is True)

    assert (provisionClock.now == 20.0)
    assert (MUT.VM._Status.call_count == 2)
    MUT.time.sleep.assert_called_once_with(4.0)
    if (status == 'Running'): virtualMachine.virtCtl.Run.assert_not_called()
    else: virtualMachine.virtCtl.Run.assert_called_once_with('start', 'vm-one')
    virtualMachine.oc.Run.assert_called_once_with(
        'wait', 'VirtualMachine/vm-one', **{'for': 'condition=Ready'},
        timeout='2m',
    )


@pytest.mark.parametrize('status', ['Stopped', 'Stopping'])
@pytest.mark.parametrize('timeout', [None, '2m'])
@pytest.mark.parametrize('waitSuccess', [False, True])
def test_Stop_SkipsNativeStopButWaitsForInactiveVM(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
    status: str,
    timeout: str | None,
    waitSuccess: bool,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.VM, '_Status')
    MUT.VM._Status.return_value = status
    virtualMachine.oc.Run.return_value = CommandResult(waitSuccess)

    result = (
        virtualMachine.vm.Stop() if (timeout is None)
        else virtualMachine.vm.Stop(timeout=timeout)
    )
    assert (result is waitSuccess)
    virtualMachine.virtCtl.Run.assert_not_called()
    virtualMachine.oc.Run.assert_called_once_with(
        'wait', 'VirtualMachine/vm-one',
        **{'for': 'jsonpath={.status.printableStatus}=Stopped'},
        timeout='5m' if (timeout is None) else timeout,
    )


@pytest.mark.parametrize('timeout', [None, '2m'])
@pytest.mark.parametrize('stopSuccess', [False, True])
@pytest.mark.parametrize('waitSuccess', [False, True])
def test_Stop_WaitsOnlyAfterNativeStopSucceeds(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
    timeout: str | None,
    stopSuccess: bool,
    waitSuccess: bool,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.VM, '_Status')
    MUT.VM._Status.return_value = 'Running'
    virtualMachine.virtCtl.Run.return_value = CommandResult(stopSuccess)
    virtualMachine.oc.Run.return_value = CommandResult(waitSuccess)

    result = (
        virtualMachine.vm.Stop() if (timeout is None)
        else virtualMachine.vm.Stop(timeout=timeout)
    )
    assert (result is (stopSuccess and waitSuccess))
    virtualMachine.virtCtl.Run.assert_called_once_with('stop', 'vm-one')
    if (not stopSuccess): virtualMachine.oc.Run.assert_not_called()
    else:
        virtualMachine.oc.Run.assert_called_once_with(
            'wait', 'VirtualMachine/vm-one',
            **{'for': 'jsonpath={.status.printableStatus}=Stopped'},
            timeout='5m' if (timeout is None) else timeout,
        )


def test_NewSSH_UsesDefaultsAndProbes(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'RHOVsshCon')
    connection = MUT.RHOVsshCon.return_value
    connection.Probe.return_value = True

    result = virtualMachine.vm.NewSSH(probeTO=12)

    assert (result is connection)
    MUT.RHOVsshCon.assert_called_once_with(
        ns='namespace',
        host='vm-one',
        user='qa-usr',
        idFile='~/.ssh/id_ed25519',
        virtCtl=virtualMachine.virtCtl,
    )
    connection.Probe.assert_called_once_with(timeout=12)


def test_NewSSH_AllowsOverrides(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'RHOVsshCon')
    MUT.RHOVsshCon.return_value.Probe.return_value = True
    virtualMachine.vm.sshKey = '/default-key'

    virtualMachine.vm.NewSSH(
        user='override',
        idFile='/override',
        ctrlPersist='1h',
        virtCtl=None,
    )

    MUT.RHOVsshCon.assert_called_once_with(
        ns='namespace',
        host='vm-one',
        user='override',
        idFile='/override',
        ctrlPersist='1h',
        virtCtl=None,
    )


def test_NewSSH_TimeoutRaisesAndDoesNotCache(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'RHOVsshCon')
    connection = MUT.RHOVsshCon.return_value
    connection.Probe.return_value = False

    with pytest.raises(TimeoutError, match='after 7 seconds'):
        virtualMachine.vm.NewSSH(probeTO=7)

    connection.Probe.assert_called_once_with(timeout=7)
    assert (virtualMachine.vm._ssh is None)


def test_ssh_UsesSelectedClientAndCachesConnection(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
) -> None:
    Utils.AutoPatch(monkeypatch, RHOVsshCon, 'Probe')
    RHOVsshCon.Probe.return_value = True

    connection = virtualMachine.vm.ssh

    assert (connection.virtCtl is virtualMachine.virtCtl)
    assert (connection.ns == virtualMachine.vm.namespace)
    assert (virtualMachine.vm.ssh is connection)
    RHOVsshCon.Probe.assert_called_once_with(connection, timeout=300)


def test_NewSSH_PreservesExplicitClientOverride(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
) -> None:
    Utils.AutoPatch(monkeypatch, RHOVsshCon, 'Probe')
    RHOVsshCon.Probe.return_value = True
    otherClient = mock.MagicMock()

    connection = virtualMachine.vm.NewSSH(virtCtl=otherClient)

    assert (connection.virtCtl is otherClient)
    assert (virtualMachine.vm.cli.virtCtl is virtualMachine.virtCtl)


def test_ssh_RetriesAfterTimeoutWithoutCachingFailure(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
) -> None:
    failed = mock.create_autospec(RHOVsshCon, instance=True)
    recovered = mock.create_autospec(RHOVsshCon, instance=True)
    failed.Probe.return_value = False
    recovered.Probe.return_value = True
    Utils.AutoPatch(monkeypatch, MUT, 'RHOVsshCon')
    MUT.RHOVsshCon.side_effect = [failed, recovered]

    with pytest.raises(TimeoutError, match='SSH readiness probe timed out'):
        _ = virtualMachine.vm.ssh

    assert (virtualMachine.vm._ssh is None)
    assert (virtualMachine.vm.ssh is recovered)
    assert (virtualMachine.vm._ssh is recovered)
    assert (MUT.RHOVsshCon.call_count == 2)


def test_RmvSSH_IsNoopWithoutConnection(
    virtualMachine: Utils.VMcontext,
) -> None:
    virtualMachine.vm.RmvSSH()
    assert (virtualMachine.vm._ssh is None)


def test_RmvSSH_ClosesAndClearsConnection(
    virtualMachine: Utils.VMcontext,
) -> None:
    connection = mock.create_autospec(RHOVsshCon, instance=True)
    virtualMachine.vm._ssh = connection

    virtualMachine.vm.RmvSSH()

    connection.Close.assert_called_once_with()
    assert (virtualMachine.vm._ssh is None)


def test_ssh_IsLazyAndCached(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.VM, 'NewSSH')
    connection = mock.create_autospec(RHOVsshCon, instance=True)
    MUT.VM.NewSSH.return_value = connection

    assert (virtualMachine.vm.ssh is connection)
    assert (virtualMachine.vm.ssh is connection)
    MUT.VM.NewSSH.assert_called_once_with(virtualMachine.vm)
