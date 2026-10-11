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


def test_GetVMuid_ReturnsStrippedCommandFields() -> None:
    oc = OCmock()
    oc.Run.return_value = CommandResult(True, ' uid-1\n', ' warning\n')

    result = MUT.VM.GetVMuid(oc, 'vm-one')

    assert result == (True, 'uid-1', 'warning')
    oc.Run.assert_called_once_with(
        'get',
        'VirtualMachine/vm-one',
        o='jsonpath={.metadata.uid}',
        ignore_not_found=True,
    )


def test_CleanUpVMs_HandlesEveryOwnershipOutcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.VM, 'GetVMuid')
    goodVM = SimpleNamespace(name='good', RmvSSH=mock.MagicMock())
    badVM = SimpleNamespace(name='bad', RmvSSH=mock.MagicMock())
    badVM.RmvSSH.side_effect = RuntimeError('close failed')
    lookupFail, absent, changed, deleteFail, deleted, lookupException = (
        OCmock() for _ in range(6)
    )
    MUT.VM.GetVMuid.side_effect = [
        (False, '', 'lookup failed'),
        (True, '', ''),
        (True, 'new-uid', ''),
        (True, 'owned', ''),
        (True, 'owned', ''),
        RuntimeError('lookup exploded'),
    ]
    deleteFail.DelRes.return_value = (
        SimpleNamespace(name='DELETED'),
        CommandResult(False, stderr='delete failed'),
    )
    deleted.DelRes.return_value = (
        SimpleNamespace(name='WAITED'),
        CommandResult(True),
    )

    with pytest.raises(RuntimeError, match='VM cleanup failed'):
        MUT.VM.CleanUpVMs(
            [goodVM, badVM],
            [
                (lookupFail, 'lookup-fail', 'owned'),
                (absent, 'absent', 'owned'),
                (changed, 'changed', 'owned'),
                (deleteFail, 'delete-fail', 'owned'),
                (deleted, 'deleted', 'owned'),
                (lookupException, 'lookup-exception', 'owned'),
            ],
        )

    goodVM.RmvSSH.assert_called_once_with()
    badVM.RmvSSH.assert_called_once_with()
    lookupFail.DelRes.assert_not_called()
    absent.DelRes.assert_not_called()
    changed.DelRes.assert_not_called()
    deleteFail.DelRes.assert_called_once_with('VirtualMachine/delete-fail')
    deleted.DelRes.assert_called_once_with('VirtualMachine/deleted')


def test_CleanUpVMs_PreservesPrimaryException(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.VM, 'GetVMuid')
    Utils.AutoPatch(monkeypatch, MUT.sys, 'exc_info')
    primary = RuntimeError('primary')
    MUT.VM.GetVMuid.return_value = (False, '', 'failed')
    MUT.sys.exc_info.return_value = (RuntimeError, primary, None)

    with caplog.at_level('ERROR', logger=MUT.__name__):
        MUT.VM.CleanUpVMs([], [(OCmock(), 'vm', 'uid')])

    assert 'preserving primary exception: primary' in caplog.text


def test_CleanUpVMs_WithNoFailuresReturnsNormally() -> None:
    assert MUT.VM.CleanUpVMs([], []) is None


def test_Init_UsesSuppliedClients(virtualMachine: Utils.VMcontext) -> None:
    vm = virtualMachine.vm

    assert vm.name == 'vm-one'
    assert vm.namespace == 'namespace'
    assert vm.cli.oc is virtualMachine.oc
    assert vm.cli.virtCtl is virtualMachine.virtCtl
    assert vm.sshUsr == 'qa-usr'
    assert vm.sshKey is None
    assert vm._ssh is None


def test_Init_CreatesDefaultClients(monkeypatch: pytest.MonkeyPatch) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'OCcli')
    Utils.AutoPatch(monkeypatch, MUT, 'VirtCtlCLI')

    vm = MUT.VM('vm', 'ns', sshUsr='admin', sshKey='/key')

    MUT.OCcli.assert_called_once_with(n='ns')
    MUT.VirtCtlCLI.assert_called_once_with(n='ns')
    assert vm.cli.oc is MUT.OCcli.return_value
    assert vm.cli.virtCtl is MUT.VirtCtlCLI.return_value


def test__Status_ReturnsValueOnlyForSuccess(virtualMachine: Utils.VMcontext) -> None:
    virtualMachine.oc.Run.return_value = CommandResult(True, "'Running'")
    assert virtualMachine.vm._Status() == 'Running'
    virtualMachine.oc.Run.assert_called_once_with(
        'get',
        'VirtualMachine/vm-one',
        o="jsonpath='{.status.printableStatus}'",
    )

    virtualMachine.oc.Run.return_value = CommandResult(False, "'Stopped'")
    assert virtualMachine.vm._Status() == ''


@pytest.mark.parametrize(
    'status',
    ['Running', 'Starting', 'Scheduling', 'Provisioning', 'Migrating'],
)
def test_Start_SkipsActiveVM(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
    status: str,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.VM, '_Status')
    MUT.VM._Status.return_value = status

    assert virtualMachine.vm.Start()
    virtualMachine.virtCtl.Run.assert_not_called()


def test_Start_InvokesVirtCtlForInactiveVM(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.VM, '_Status')
    MUT.VM._Status.return_value = 'Stopped'
    virtualMachine.virtCtl.Run.return_value = CommandResult(False)

    assert not virtualMachine.vm.Start()
    virtualMachine.virtCtl.Run.assert_called_once_with('start', 'vm-one')


@pytest.mark.parametrize('status', ['Stopped', 'Stopping'])
def test_Stop_SkipsInactiveVM(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
    status: str,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.VM, '_Status')
    MUT.VM._Status.return_value = status

    assert virtualMachine.vm.Stop()
    virtualMachine.virtCtl.Run.assert_not_called()


def test_Stop_InvokesVirtCtlForActiveVM(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.VM, '_Status')
    MUT.VM._Status.return_value = 'Running'
    virtualMachine.virtCtl.Run.return_value = CommandResult(True)

    assert virtualMachine.vm.Stop()
    virtualMachine.virtCtl.Run.assert_called_once_with('stop', 'vm-one')


def test_NewSSH_UsesDefaultsAndProbes(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'RHOVsshCon')
    connection = MUT.RHOVsshCon.return_value
    connection.Probe.return_value = True

    result = virtualMachine.vm.NewSSH(probeTO=12)

    assert result is connection
    MUT.RHOVsshCon.assert_called_once_with(
        ns='namespace',
        host='vm-one',
        user='qa-usr',
        idFile='~/.ssh/id_ed25519',
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
    )

    MUT.RHOVsshCon.assert_called_once_with(
        ns='namespace',
        host='vm-one',
        user='override',
        idFile='/override',
        ctrlPersist='1h',
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
    assert virtualMachine.vm._ssh is None


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

    assert virtualMachine.vm._ssh is None
    assert virtualMachine.vm.ssh is recovered
    assert virtualMachine.vm._ssh is recovered
    assert MUT.RHOVsshCon.call_count == 2


def test_RmvSSH_IsNoopWithoutConnection(virtualMachine: Utils.VMcontext) -> None:
    virtualMachine.vm.RmvSSH()
    assert virtualMachine.vm._ssh is None


def test_RmvSSH_ClosesAndClearsConnection(virtualMachine: Utils.VMcontext) -> None:
    connection = mock.create_autospec(RHOVsshCon, instance=True)
    virtualMachine.vm._ssh = connection

    virtualMachine.vm.RmvSSH()

    connection.Close.assert_called_once_with()
    assert virtualMachine.vm._ssh is None


def test_ssh_IsLazyAndCached(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.VM, 'NewSSH')
    connection = mock.create_autospec(RHOVsshCon, instance=True)
    MUT.VM.NewSSH.return_value = connection

    assert virtualMachine.vm.ssh is connection
    assert virtualMachine.vm.ssh is connection
    MUT.VM.NewSSH.assert_called_once_with(virtualMachine.vm)
