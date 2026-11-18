import inspect
from types import SimpleNamespace
from typing import Optional
from unittest import mock

import pytest

import libs.OpenShift.LP.Virt.VM.VM as MUT
from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.OpenShift.LP.Virt.VM.RHOVssh import RHOVsshCon
from libs.Utils.CmdExec import CmdRes
import Utils


@pytest.fixture(name='vmBoundaries', autouse=True)
def VMBoundaries(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    ocFactory = mock.Mock()
    ocFactory.CleanUpRes = mock.Mock()
    virtCtlFactory = mock.Mock()
    sshFactory = mock.Mock()
    sshFactory.return_value.Probe.return_value = True
    namedDictFactory = mock.Mock(side_effect=SimpleNamespace)
    sysModule = SimpleNamespace(
        exc_info=mock.Mock(return_value=(None, None, None)),
    )
    monkeypatch.setattr(MUT, 'OCcli', ocFactory)
    monkeypatch.setattr(MUT, 'VirtCtlCLI', virtCtlFactory)
    monkeypatch.setattr(MUT, 'RHOVsshCon', sshFactory)
    monkeypatch.setattr(MUT, 'NamedDict', namedDictFactory)
    monkeypatch.setattr(MUT, 'sys', sysModule)
    return SimpleNamespace(
        ocFactory=ocFactory,
        virtCtlFactory=virtCtlFactory,
        sshFactory=sshFactory,
        namedDictFactory=namedDictFactory,
        sysModule=sysModule,
    )


@pytest.fixture(name='provisionClock', autouse=True)
def ProvisionClock(monkeypatch: pytest.MonkeyPatch) -> Utils.FakeClock:
    clock = Utils.FakeClock()
    monkeypatch.setattr(MUT, 'time', SimpleNamespace(
        monotonic=mock.Mock(side_effect=clock.Now),
        sleep=mock.Mock(side_effect=clock.Sleep),
    ))
    return clock


def CommandResult(success: bool, stdout: str = '', stderr: str = '') -> CmdRes:
    return CmdRes(stdout, stderr, 0 if success else 1, success)


def OCMock() -> mock.MagicMock:
    return mock.create_autospec(OCcli, instance=True)


@pytest.mark.parametrize('teardown', ['close', 'resume', 'primary'])
@pytest.mark.parametrize('sshFailure', [False, True])
def test_CleanUpVMs_PreservesGeneratorAndPrimaryExceptions(
    vmBoundaries: SimpleNamespace,
    teardown: str,
    sshFailure: bool,
) -> None:
    cli = OCMock()
    owned = [(cli, 'VirtualMachine/owned', 'owned-uid')]
    vm = SimpleNamespace(name='owned', RmvSSH=mock.Mock(
        side_effect=RuntimeError('SSH failed') if sshFailure else None,
    ))
    vmBoundaries.ocFactory.CleanUpRes.side_effect = RuntimeError(
        'resources failed',
    )

    def Fixture():
        try: yield
        finally: MUT.VM.CleanUpVMs([vm], owned)

    generator = Fixture()
    next(generator)
    error = RuntimeError('primary')
    if (teardown == 'close'):
        vmBoundaries.sysModule.exc_info.return_value = (
            GeneratorExit, GeneratorExit(), None,
        )
    elif (teardown == 'resume'):
        vmBoundaries.sysModule.exc_info.return_value = (None, None, None)
    else:
        vmBoundaries.sysModule.exc_info.return_value = (
            RuntimeError, error, None,
        )
    with pytest.raises(RuntimeError) as caught:
        if (teardown == 'close'): generator.close()
        elif (teardown == 'resume'): next(generator)
        else: generator.throw(error)
    if (teardown == 'primary'): assert (caught.value is error)
    else: assert ('VM cleanup failed' in str(caught.value))
    vm.RmvSSH.assert_called_once_with()
    vmBoundaries.ocFactory.CleanUpRes.assert_called_once_with(owned)
    vmBoundaries.sysModule.exc_info.assert_called_once_with()


@pytest.mark.parametrize('sshFailed', [False, True])
@pytest.mark.parametrize('cleanupFailed', [False, True])
@pytest.mark.parametrize('primary', [False, True])
@pytest.mark.parametrize('options', [
    None, {}, {'optsDelRes': {'waitTime': '5m'}},
    {'ownRes': 'unowned', 'optsDelRes': {'waitTime': '7m'}},
])
def test_CleanUpVMs_ClosesSSHAndDelegatesDespiteErrors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    vmBoundaries: SimpleNamespace,
    sshFailed: bool,
    cleanupFailed: bool,
    primary: bool,
    options: dict | None,
) -> None:
    before = None if (options is None) else {
        key: dict(value) if (isinstance(value, dict)) else value
        for (key, value) in options.items()
    }
    vm = SimpleNamespace(name='example', RmvSSH=mock.Mock())
    if sshFailed: vm.RmvSSH.side_effect = RuntimeError('SSH failed')
    cleanup = vmBoundaries.ocFactory.CleanUpRes
    cleanup.side_effect = (
        RuntimeError('resources failed') if cleanupFailed else None
    )
    if primary:
        primaryError = RuntimeError('primary')
        monkeypatch.setattr(MUT, 'sys', SimpleNamespace(
            exc_info=mock.Mock(return_value=(
                RuntimeError, primaryError, None,
            )),
        ))
    owned = [(OCMock(), 'VirtualMachine/example', 'owned')]
    if ((sshFailed or cleanupFailed) and not primary):
        with pytest.raises(RuntimeError, match='VM cleanup failed'):
            MUT.VM.CleanUpVMs([vm], owned, optsCleanUpRes=options)
    else: MUT.VM.CleanUpVMs([vm], owned, optsCleanUpRes=options)
    vm.RmvSSH.assert_called_once_with()
    expected = dict(options or {})
    expected.pop('ownRes', None)
    cleanup.assert_called_once_with(owned, **expected)
    MUT.sys.exc_info.assert_called_once_with()
    assert (options == before)
    if ((sshFailed or cleanupFailed) and primary):
        assert ('preserving primary exception' in caplog.text)


def test_Init_UsesSuppliedClients(
    virtualMachine: Utils.VMcontext,
    vmBoundaries: SimpleNamespace,
) -> None:
    vm = virtualMachine.vm
    vmBoundaries.namedDictFactory.assert_any_call(
        oc=virtualMachine.oc,
        virtCtl=virtualMachine.virtCtl,
    )

    assert (vm.name == 'vm-one')
    assert (vm.namespace == 'namespace')
    assert (vm.cli.oc is virtualMachine.oc)
    assert (vm.cli.virtCtl is virtualMachine.virtCtl)
    assert (vm.sshUsr == 'qa-usr')
    assert (vm.sshKey is None)
    assert (vm._ssh is None)


def test_Init_CreatesDefaultClients(vmBoundaries: SimpleNamespace) -> None:
    vm = MUT.VM('vm', 'ns', sshUsr='admin', sshKey='/key')

    vmBoundaries.ocFactory.assert_called_once_with(n='ns')
    vmBoundaries.virtCtlFactory.assert_called_once_with(n='ns')
    assert (vm.cli.oc is vmBoundaries.ocFactory.return_value)
    assert (vm.cli.virtCtl is vmBoundaries.virtCtlFactory.return_value)
    vmBoundaries.namedDictFactory.assert_called_once_with(
        oc=vmBoundaries.ocFactory.return_value,
        virtCtl=vmBoundaries.virtCtlFactory.return_value,
    )


@pytest.mark.parametrize(('success', 'stdout', 'expected'), [
    (True, 'Running', 'Running'),
    (True, "'Running'", "'Running'"),
    (True, " \t'Running'\n", " \t'Running'\n"),
    (True, '', ''),
    (False, "'Stopped'", ''),
])
def test__Status_ReturnsValueOnlyForSuccess(
    virtualMachine: Utils.VMcontext,
    success: bool,
    stdout: str,
    expected: str,
) -> None:
    virtualMachine.oc.Run.return_value = CommandResult(success, stdout)

    assert (virtualMachine.vm._Status() == expected)
    virtualMachine.oc.Run.assert_called_once_with(
        'get',
        'VirtualMachine/vm-one',
        o='jsonpath={.status.printableStatus}',
    )


@pytest.mark.parametrize('status', [
    'Running', 'Starting', 'Scheduling', 'Migrating',
])
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
        'wait',
        'VirtualMachine/vm-one',
        **{'for': 'condition=Ready'},
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
    assert (provisionClock.now == (10.0 + elapsed))
    virtualMachine.virtCtl.Run.assert_called_once_with('start', 'vm-one')
    if not startSuccess: virtualMachine.oc.Run.assert_not_called()
    else:
        virtualMachine.oc.Run.assert_called_once_with(
            'wait',
            'VirtualMachine/vm-one',
            **{'for': 'condition=Ready'},
            timeout='5m' if (timeout is None) else timeout,
        )


@pytest.mark.parametrize('status', [
    'Stopped', 'Running', 'Starting', 'Scheduling', 'Migrating', '',
])
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
        virtualMachine.vm.Start(provTO=7) if (timeout is None)
        else virtualMachine.vm.Start(timeout=timeout, provTO=7)
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


@pytest.mark.parametrize(('provTO', 'queryLatency', 'sleeps', 'elapsed'), [
    (None, 0.0, ([5.0] * 360), 1800.0),
    (12, 0.0, [5.0, 5.0, 2.0], 12.0),
    (12, 2.0, [5.0, 3.0], 14.0),
    (12, 6.0, [5.0], 17.0),
    (3, 4.0, [], 4.0),
])
def test_Start_ProvisioningTimeoutIncludesQueryLatencyWithoutNativeStart(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
    provisionClock: Utils.FakeClock,
    provTO: Optional[int],
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
    assert (MUT.VM._Status.call_count == (len(sleeps) + 1))
    assert (MUT.time.monotonic.call_count == (len(sleeps) + 2))
    assert (provisionClock.now == (10.0 + elapsed))
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

    assert (virtualMachine.vm.Start(timeout='2m', provTO=7) is True)

    assert (provisionClock.now == 20.0)
    assert (MUT.VM._Status.call_count == 2)
    MUT.time.sleep.assert_called_once_with(4.0)
    if (status == 'Running'): virtualMachine.virtCtl.Run.assert_not_called()
    else:
        virtualMachine.virtCtl.Run.assert_called_once_with('start', 'vm-one')
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
    if not stopSuccess: virtualMachine.oc.Run.assert_not_called()
    else:
        virtualMachine.oc.Run.assert_called_once_with(
            'wait', 'VirtualMachine/vm-one',
            **{'for': 'jsonpath={.status.printableStatus}=Stopped'},
            timeout='5m' if (timeout is None) else timeout,
        )


def test_NewSSH_UsesDefaultsAndProbes(
    virtualMachine: Utils.VMcontext,
    vmBoundaries: SimpleNamespace,
) -> None:
    connection = vmBoundaries.sshFactory.return_value

    result = virtualMachine.vm.NewSSH(probeTO=42)

    assert (result is connection)
    vmBoundaries.sshFactory.assert_called_once_with(
        ns='namespace',
        host='vm-one',
        user='qa-usr',
        idFile='~/.ssh/id_ed25519',
        virtCtl=virtualMachine.virtCtl,
        oc=virtualMachine.oc,
    )
    connection.Probe.assert_called_once_with(42)


def test_NewSSH_AllowsOverrides(
    virtualMachine: Utils.VMcontext,
    vmBoundaries: SimpleNamespace,
) -> None:
    connection = vmBoundaries.sshFactory.return_value
    virtualMachine.vm.sshKey = '/default-key'

    virtualMachine.vm.NewSSH(
        user='override', idFile='/override', ctrlPersist='1h',
        virtCtl=None,
    )

    vmBoundaries.sshFactory.assert_called_once_with(
        ns='namespace',
        host='vm-one',
        user='override',
        idFile='/override',
        ctrlPersist='1h',
        virtCtl=None,
        oc=virtualMachine.oc,
    )
    connection.Probe.assert_called_once_with(300)


@pytest.mark.parametrize(('overrideVirtCtl', 'overrideOC'), [
    (False, False), (True, False), (False, True), (True, True),
])
def test_NewSSH_AppliesClientOverridesIndependently(
    virtualMachine: Utils.VMcontext,
    vmBoundaries: SimpleNamespace,
    overrideVirtCtl: bool,
    overrideOC: bool,
) -> None:
    overrides = {}
    if overrideVirtCtl: overrides['virtCtl'] = None
    if overrideOC: overrides['oc'] = None

    virtualMachine.vm.NewSSH(**overrides)

    kwargs = vmBoundaries.sshFactory.call_args.kwargs
    assert (kwargs['virtCtl'] is (
        None if overrideVirtCtl else virtualMachine.virtCtl
    ))
    assert (kwargs['oc'] is (None if overrideOC else virtualMachine.oc))
    vmBoundaries.sshFactory.return_value.Probe.assert_called_once_with(300)


@pytest.mark.parametrize('clientName', ['virtCtl', 'oc'])
def test_NewSSH_PreservesSingleExplicitClientOverride(
    virtualMachine: Utils.VMcontext,
    vmBoundaries: SimpleNamespace,
    clientName: str,
) -> None:
    client = mock.MagicMock()
    virtualMachine.vm.NewSSH(**{clientName: client})
    kwargs = vmBoundaries.sshFactory.call_args.kwargs

    assert (kwargs[clientName] is client)
    if (clientName == 'virtCtl'): assert (kwargs['oc'] is virtualMachine.oc)
    else: assert (kwargs['virtCtl'] is virtualMachine.virtCtl)


def test_NewSSH_PreservesExplicitOCAndVirtCtlPair(
    virtualMachine: Utils.VMcontext,
    vmBoundaries: SimpleNamespace,
) -> None:
    otherOC = OCMock()
    otherVirtCtl = mock.MagicMock()

    virtualMachine.vm.NewSSH(virtCtl=otherVirtCtl, oc=otherOC)

    kwargs = vmBoundaries.sshFactory.call_args.kwargs
    assert (kwargs['oc'] is otherOC)
    assert (kwargs['virtCtl'] is otherVirtCtl)
    assert (virtualMachine.vm.cli.oc is virtualMachine.oc)
    assert (virtualMachine.vm.cli.virtCtl is virtualMachine.virtCtl)


def test_NewSSH_ForwardsKubeconfigAndNoneClientOverrides(
    virtualMachine: Utils.VMcontext,
    vmBoundaries: SimpleNamespace,
) -> None:
    connection = vmBoundaries.sshFactory.return_value
    executor = mock.Mock()

    result = virtualMachine.vm.NewSSH(
        probeTO=42,
        kubeconfig='/selected-config',
        virtCtl=None,
        oc=None,
        cmdExec=executor,
    )

    assert (result is connection)
    vmBoundaries.sshFactory.assert_called_once_with(
        ns='namespace',
        host='vm-one',
        user='qa-usr',
        idFile='~/.ssh/id_ed25519',
        kubeconfig='/selected-config',
        virtCtl=None,
        oc=None,
        cmdExec=executor,
    )
    connection.Probe.assert_called_once_with(42)
    vmBoundaries.ocFactory.assert_not_called()
    vmBoundaries.virtCtlFactory.assert_not_called()


def test_NewSSH_TimeoutRaisesAndDoesNotCache(
    virtualMachine: Utils.VMcontext,
    vmBoundaries: SimpleNamespace,
) -> None:
    connection = vmBoundaries.sshFactory.return_value
    connection.Probe.return_value = False

    with pytest.raises(TimeoutError, match='after 37 seconds'):
        virtualMachine.vm.NewSSH(probeTO=37)

    connection.Probe.assert_called_once_with(37)
    assert (virtualMachine.vm._ssh is None)


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


def test_ssh_RetriesAfterTimeoutWithoutCachingFailure(
    virtualMachine: Utils.VMcontext,
    vmBoundaries: SimpleNamespace,
) -> None:
    failed = mock.create_autospec(RHOVsshCon, instance=True)
    recovered = mock.create_autospec(RHOVsshCon, instance=True)
    failed.Probe.return_value = False
    recovered.Probe.return_value = True
    vmBoundaries.sshFactory.side_effect = [failed, recovered]

    with pytest.raises(TimeoutError, match='SSH readiness probe timed out'):
        _ = virtualMachine.vm.ssh

    assert (virtualMachine.vm._ssh is None)
    assert (virtualMachine.vm.ssh is recovered)
    assert (virtualMachine.vm._ssh is recovered)
    assert (vmBoundaries.sshFactory.call_count == 2)


@pytest.mark.parametrize('provTO', [
    0, -1, 1.0, 0.5, True, False, None, '1', float('nan'), float('inf'),
])
def test_Start_RejectsInvalidProvisioningTimeoutBeforeCommands(
    monkeypatch: pytest.MonkeyPatch,
    virtualMachine: Utils.VMcontext,
    provisionClock: Utils.FakeClock,
    provTO: object,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.VM, '_Status')

    with pytest.raises(
        ValueError, match='^The `provTO` must be a positive integer',
    ):
        virtualMachine.vm.Start(provTO=provTO)

    MUT.VM._Status.assert_not_called()
    MUT.time.monotonic.assert_not_called()
    MUT.time.sleep.assert_not_called()
    virtualMachine.virtCtl.Run.assert_not_called()
    virtualMachine.oc.Run.assert_not_called()


def test_CleanUpVMs_DocumentsOwnershipAndCleanupContract() -> None:
    method = MUT.VM.CleanUpVMs
    parameters = ['vmList', 'ownVMs', 'optsCleanUpRes']
    doc = inspect.getdoc(method)
    assert (list(inspect.signature(method).parameters) == parameters)
    for parameter in parameters: assert (f':param {parameter}:' in doc)
    assert (inspect.signature(method).return_annotation is None)
    assert (':return:' not in doc)
    normalized = ' '.join(doc.split())
    assert ('wrappers for reused VMs' in normalized)
    assert ('not atomic' in normalized)
    assert ('OCcli.CleanUpRes' in doc)
    assert all((len(line) <= 79) for line in method.__doc__.splitlines())
