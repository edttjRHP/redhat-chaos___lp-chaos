from dataclasses import FrozenInstanceError
import json
import sys
import types
from types import SimpleNamespace
from unittest import mock

import pytest

import fixtures.BenchmarkRunner as MUT
from libs.CustomTypes import NamedDict
import Utils


class BenchmarkRunnerModules:
    def __init__(self, environment: dict[str, object]) -> None:
        self.brMain = mock.MagicMock(name='benchmarkRunnerMain')
        self.temporaryEnvironment = mock.MagicMock(
            name='TemporaryEnvironmentVariables',
        )
        self.temporaryEnvironment.return_value.__enter__.return_value = None
        self.temporaryEnvironment.return_value.__exit__.return_value = False

        package = types.ModuleType('benchmark_runner')
        package.__path__ = []
        mainPackage = types.ModuleType('benchmark_runner.main')
        mainPackage.__path__ = []
        environmentModule = types.ModuleType(
            'benchmark_runner.main.environment_variables',
        )
        environmentModule.environment_variables = SimpleNamespace(
            environment_variables_dict=environment,
        )
        mainModule = types.ModuleType('benchmark_runner.main.main')
        mainModule.main = self.brMain
        temporaryModule = types.ModuleType(
            'benchmark_runner.main.temporary_environment_variables',
        )
        temporaryModule.TemporaryEnvironmentVariables = self.temporaryEnvironment
        self.modules = {
            'benchmark_runner': package,
            'benchmark_runner.main': mainPackage,
            'benchmark_runner.main.environment_variables': environmentModule,
            'benchmark_runner.main.main': mainModule,
            'benchmark_runner.main.temporary_environment_variables': temporaryModule,
        }


def PatchModules(
    monkeypatch: pytest.MonkeyPatch,
    runner: BenchmarkRunnerModules,
) -> None:
    for name, module in runner.modules.items():
        monkeypatch.setitem(sys.modules, name, module)


def test_VMcreateByBR_NegativeCountIsRejectedBeforeImportingRunner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.tempfile, 'mkdtemp')
    vmList = []
    ownVMs = []
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()

    with pytest.raises(ValueError, match='must not be negative'):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            vmList,
            ownVMs,
            ocCLI=ocFactory,
            virtCtlCLI=virtCtlFactory,
            count=-1,
        )

    assert vmList == []
    assert ownVMs == []
    ocFactory.assert_not_called()
    virtCtlFactory.assert_not_called()
    MUT.VM.GetVMuid.assert_not_called()
    MUT.uuid4.assert_not_called()
    MUT.tempfile.mkdtemp.assert_not_called()


def test_VMcreateByBR_ZeroCountReturnsEmptyWithoutRunner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.tempfile, 'mkdtemp')
    vmList = []
    ownVMs = []
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()
    runner = BenchmarkRunnerModules({'run_artifacts_path': '/tmp'})
    PatchModules(monkeypatch, runner)

    result = MUT.VMcreateByBR(
        SimpleNamespace(),
        vmList,
        ownVMs,
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
        count=0,
    )

    assert result == []
    assert vmList == []
    assert ownVMs == []
    ocFactory.assert_not_called()
    virtCtlFactory.assert_not_called()
    MUT.VM.GetVMuid.assert_not_called()
    MUT.uuid4.assert_not_called()
    MUT.tempfile.mkdtemp.assert_not_called()
    runner.brMain.assert_not_called()
    runner.temporaryEnvironment.assert_not_called()


def test_VMcreateByBR_SingleVMhappyPathUsesGeneratedIdentityKey(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.uuid4.return_value = '12345678-abcd-0000-0000-000000000000'
    MUT.Path.is_file.return_value = True
    MUT.VM.GetVMuid.side_effect = [
        (True, '', ''),
        (True, 'uid-one', ''),
    ]
    runner = BenchmarkRunnerModules({'run_artifacts_path': '/artifacts'})
    PatchModules(monkeypatch, runner)
    ocFactory = mock.MagicMock(name='ocFactory')
    virtCtlFactory = mock.MagicMock(name='virtCtlFactory')
    request = SimpleNamespace()
    vmList = []
    ownVMs = []

    created = MUT.VMcreateByBR(
        request,
        vmList,
        ownVMs,
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
        workloadName='windows',
        workloadKind='vm',
        cliCfg=NamedDict(ocCfg={'context': 'oc-context'}),
        gstCfg=NamedDict(usrCrd=NamedDict(sshUsr='unit-user')),
    )

    assert created == [MUT.VM.return_value]
    assert vmList == created
    assert ownVMs == [
        (ocFactory.return_value, 'windows-vm-12345678', 'uid-one'),
    ]
    environment = runner.modules[
        'benchmark_runner.main.environment_variables'
    ].environment_variables.environment_variables_dict
    assert environment['workload'] == 'windows_vm'
    assert environment['run_type'] == 'test_ci'
    assert environment['namespace'] == 'default'
    assert not environment['delete_all']
    assert environment['scale'] == ''
    assert environment['scale_nodes'] == ''
    assert environment['uuid'] == str(MUT.uuid4.return_value)
    assert environment['trunc_uuid'] == '12345678'
    ocFactory.assert_called_once_with(ns='default', context='oc-context')
    virtCtlFactory.assert_called_once_with(ns='default')
    MUT.VM.assert_called_once_with(
        name='windows-vm-12345678',
        ns='default',
        oc=ocFactory.return_value,
        virtCtl=virtCtlFactory.return_value,
        sshUsr='unit-user',
        sshKey='/artifacts/ssh/vm_key',
    )

    runner.brMain.assert_called_once_with()
    runner.temporaryEnvironment.assert_called_once_with()
    temporaryEnvironment = runner.temporaryEnvironment.return_value
    temporaryEnvironment.__enter__.assert_called_once_with()
    temporaryEnvironment.__exit__.assert_called_once_with(None, None, None)


def test_VMcreateByBR_ScalePathNamesVMsAndUsesFallbackKey(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('WORKER_NODE', 'worker-a')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    Utils.AutoPatch(monkeypatch, MUT.tempfile, 'mkdtemp')
    MUT.uuid4.return_value = 'deadbeef-rest'
    MUT.Path.is_file.return_value = False
    MUT.tempfile.mkdtemp.return_value = '/tmp/run'
    MUT.VM.GetVMuid.side_effect = [
        (True, '', ''),
        (True, '', ''),
        (True, 'uid-0', ''),
        (True, 'uid-1', ''),
    ]
    runner = BenchmarkRunnerModules({'run_artifacts_path': ''})
    PatchModules(monkeypatch, runner)
    ocZero = mock.MagicMock(name='ocZero')
    ocOne = mock.MagicMock(name='ocOne')
    ocFactory = mock.MagicMock(side_effect=[ocZero, ocOne])
    virtCtlFactory = mock.MagicMock()
    vmList = []
    ownVMs = []

    created = MUT.VMcreateByBR(
        SimpleNamespace(),
        vmList,
        ownVMs,
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
        count=2,
        workloadName='windows_scale',
    )

    assert len(created) == 2
    assert vmList == created
    assert ownVMs == [
        (ocZero, 'windows-scale-vm-deadbeef-0', 'uid-0'),
        (ocOne, 'windows-scale-vm-deadbeef-1', 'uid-1'),
    ]
    environment = runner.modules[
        'benchmark_runner.main.environment_variables'
    ].environment_variables.environment_variables_dict
    assert environment['scale'] == '2'
    assert environment['scale_nodes'] == "['worker-a']"
    MUT.tempfile.mkdtemp.assert_called_once_with()
    assert [item.kwargs['name'] for item in MUT.VM.call_args_list] == [
        'windows-scale-vm-deadbeef-0',
        'windows-scale-vm-deadbeef-1',
    ]
    assert all(
        item.kwargs['sshKey'] == '~/.ssh/openshift-qe.pem'
        for item in MUT.VM.call_args_list
    )


def test_VMcreateByBR_NestedOverridesDoNotMutateDefaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.uuid4.side_effect = ['custom-rest', 'default-rest']
    MUT.Path.is_file.return_value = False
    MUT.VM.GetVMuid.side_effect = [
        (True, '', ''),
        (True, 'custom-uid', ''),
        (True, '', ''),
        (True, 'default-uid', ''),
    ]
    runner = BenchmarkRunnerModules({'run_artifacts_path': '/tmp'})
    PatchModules(monkeypatch, runner)
    defaultFxtCfg = json.loads(json.dumps(MUT._DEF__FXT_CFG))
    defaultNestedValues = (
        dict(MUT._DEF__FXT_CFG.cliCfg.ocCfg),
        dict(MUT._DEF__FXT_CFG.cliCfg.vcCfg),
        dict(MUT._DEF__FXT_CFG.gstCfg.usrCrd),
    )
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()

    MUT.VMcreateByBR(
        SimpleNamespace(),
        [],
        [],
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
        cliCfg=NamedDict(
            ocCfg={'context': 'custom-context'},
            vcCfg={'kubeconfig': '/custom-config'},
        ),
        gstCfg=NamedDict(usrCrd=NamedDict(sshUsr='custom-user')),
    )
    MUT.VMcreateByBR(
        SimpleNamespace(),
        [],
        [],
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
    )

    assert json.loads(json.dumps(MUT._DEF__FXT_CFG)) == defaultFxtCfg
    assert (
        dict(MUT._DEF__FXT_CFG.cliCfg.ocCfg),
        dict(MUT._DEF__FXT_CFG.cliCfg.vcCfg),
        dict(MUT._DEF__FXT_CFG.gstCfg.usrCrd),
    ) == defaultNestedValues
    assert ocFactory.call_args_list == [
        mock.call(ns='default', context='custom-context'),
        mock.call(ns='default'),
    ]
    assert virtCtlFactory.call_args_list == [
        mock.call(ns='default', kubeconfig='/custom-config'),
        mock.call(ns='default'),
    ]
    assert MUT.VM.call_args_list[0].kwargs['sshUsr'] == 'custom-user'
    assert MUT.VM.call_args_list[0].kwargs['sshKey'] == (
        defaultFxtCfg['gstCfg']['usrCrd']['sshKey']
    )
    assert MUT.VM.call_args_list[1].kwargs['sshUsr'] == (
        defaultFxtCfg['gstCfg']['usrCrd']['sshUsr']
    )
    assert MUT.VM.call_args_list[1].kwargs['sshKey'] == (
        defaultFxtCfg['gstCfg']['usrCrd']['sshKey']
    )


def test_VMcreateByBR_PreflightLookupFailureRaisesWithoutTracking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    MUT.uuid4.return_value = 'abcd-rest'
    MUT.VM.GetVMuid.return_value = (False, '', 'lookup error')
    runner = BenchmarkRunnerModules({'run_artifacts_path': '/tmp'})
    PatchModules(monkeypatch, runner)
    vmList = []
    ownVMs = []

    with pytest.raises(RuntimeError, match='lookup error'):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            vmList,
            ownVMs,
            ocCLI=mock.MagicMock(),
            virtCtlCLI=mock.MagicMock(),
        )

    assert vmList == []
    assert ownVMs == []


def test_VMcreateByBR_PreflightExistingVMIsRefused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    MUT.uuid4.return_value = 'abcd-rest'
    MUT.VM.GetVMuid.return_value = (True, 'existing-uid', '')
    runner = BenchmarkRunnerModules({'run_artifacts_path': '/tmp'})
    PatchModules(monkeypatch, runner)

    with pytest.raises(RuntimeError, match='already exists'):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            [],
            [],
            ocCLI=mock.MagicMock(),
            virtCtlCLI=mock.MagicMock(),
        )


def test_VMcreateByBR_RunnerFailurePreservesPrimaryErrorDuringReconciliation(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    MUT.uuid4.return_value = 'abcd-rest'
    MUT.VM.GetVMuid.side_effect = [
        (True, '', ''),
        (True, '', ''),
        (True, '', ''),
        (False, '', 'lookup failed'),
        (True, '', ''),
        RuntimeError('lookup exploded'),
    ]
    runner = BenchmarkRunnerModules({'run_artifacts_path': '/tmp'})
    primary = ValueError('runner failed')
    runner.brMain.side_effect = primary
    PatchModules(monkeypatch, runner)

    with caplog.at_level('ERROR'), pytest.raises(ValueError) as raised:
        MUT.VMcreateByBR(
            SimpleNamespace(),
            [],
            [],
            ocCLI=mock.MagicMock(),
            virtCtlCLI=mock.MagicMock(),
            count=3,
        )

    assert raised.value is primary
    runner.temporaryEnvironment.assert_called_once_with()
    temporaryEnvironment = runner.temporaryEnvironment.return_value
    temporaryEnvironment.__enter__.assert_called_once_with()
    temporaryEnvironment.__exit__.assert_called_once()
    excType, excValue, excTraceback = temporaryEnvironment.__exit__.call_args.args
    assert excType is ValueError
    assert excValue is primary
    assert excTraceback is not None
    assert excTraceback.tb_frame.f_code.co_name == 'VMcreateByBR'
    assert 'Preserving benchmark-runner failure' in caplog.text


def test_VMcreateByBR_ReconciliationLookupFailureRaisesWithoutRunnerError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    MUT.uuid4.return_value = 'abcd-rest'
    MUT.VM.GetVMuid.side_effect = [
        (True, '', ''),
        (False, '', 'lookup failed'),
    ]
    runner = BenchmarkRunnerModules({'run_artifacts_path': '/tmp'})
    PatchModules(monkeypatch, runner)

    with pytest.raises(RuntimeError, match='reconcile'):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            [],
            [],
            ocCLI=mock.MagicMock(),
            virtCtlCLI=mock.MagicMock(),
        )


def test_VMcreateByBR_ReconciliationMissingVMRaisesWithoutRunnerError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    MUT.uuid4.return_value = 'abcd-rest'
    MUT.VM.GetVMuid.side_effect = [(True, '', ''), (True, '', '')]
    runner = BenchmarkRunnerModules({'run_artifacts_path': '/tmp'})
    PatchModules(monkeypatch, runner)

    with pytest.raises(RuntimeError, match='did not create expected'):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            [],
            [],
            ocCLI=mock.MagicMock(),
            virtCtlCLI=mock.MagicMock(),
        )


def test_FxtVMbyBR_RegistrationUsesModuleScope() -> None:
    assert MUT.FxtVMbyBR.name == 'fxtVMbyBR'
    assert MUT.FxtVMbyBR._fixture_function_marker.scope == 'module'


def test_FxtVMbyBR_InitialNegativeFailsBeforeYieldWithoutCleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={'count': -1}),
        mock.MagicMock(),
        mock.MagicMock(),
    )

    with pytest.raises(ValueError, match='must not be negative'):
        next(generator)

    MUT.VM.CleanUpVMs.assert_not_called()


def test_FxtVMbyBR_InitialZeroYieldsFrozenEmptyBundleWithoutCleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.tempfile, 'mkdtemp')
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={'count': 0}),
        mock.MagicMock(),
        mock.MagicMock(),
    )

    bundle = next(generator)

    assert isinstance(bundle, MUT.FxtObjVMbyBR)
    assert issubclass(MUT.FxtObjVMbyBR, MUT.FxtObjVM)
    assert bundle.vmList == []
    assert bundle.vmCreate(count=0) == []
    with pytest.raises(FrozenInstanceError):
        bundle.vmList = []
    generator.close()
    MUT.VM.CleanUpVMs.assert_not_called()
    MUT.VM.GetVMuid.assert_not_called()
    MUT.uuid4.assert_not_called()
    MUT.tempfile.mkdtemp.assert_not_called()


def test_FxtVMbyBR_InitialSuccessUsesSelectedConfigAndCleansOnClose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.uuid4.return_value = 'initial-rest'
    MUT.Path.is_file.return_value = False
    MUT.VM.GetVMuid.side_effect = [
        (True, '', ''),
        (True, 'initial-uid', ''),
    ]
    vm = mock.MagicMock(name='initialVM')
    MUT.VM.return_value = vm
    runner = BenchmarkRunnerModules({'run_artifacts_path': '/tmp'})
    PatchModules(monkeypatch, runner)
    oc = mock.MagicMock(name='initialOC')
    ocFactory = mock.MagicMock(return_value=oc)
    virtCtlFactory = mock.MagicMock()
    generator = MUT.FxtVMbyBR.__wrapped__(
        request=SimpleNamespace(param={
            'count': 1,
            'ns': 'initial-ns',
            'workloadName': 'initial',
            'cliCfg': NamedDict(
                ocCfg={'context': 'initial-context'},
                vcCfg={'kubeconfig': '/initial-config'},
            ),
        }),
        fxtOCcli=ocFactory,
        fxtVirtCtlCLI=virtCtlFactory,
    )

    bundle = next(generator)

    assert bundle.vmList == [vm]
    ocFactory.assert_called_once_with(
        ns='initial-ns',
        context='initial-context',
    )
    virtCtlFactory.assert_called_once_with(
        ns='initial-ns',
        kubeconfig='/initial-config',
    )
    generator.close()
    MUT.VM.CleanUpVMs.assert_called_once_with(
        bundle.vmList,
        [(oc, 'initial-vm-initial', 'initial-uid')],
    )


def test_FxtVMbyBR_PerCallReturnsAreAggregatedAcrossBatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.uuid4.side_effect = ['first-rest', 'second-rest']
    MUT.Path.is_file.return_value = False
    MUT.VM.GetVMuid.side_effect = [
        (True, '', ''),
        (True, 'uid-one', ''),
        (True, '', ''),
        (True, '', ''),
        (True, 'uid-two', ''),
        (True, 'uid-three', ''),
    ]
    vmOne = mock.MagicMock(name='vmOne')
    vmTwo = mock.MagicMock(name='vmTwo')
    vmThree = mock.MagicMock(name='vmThree')
    MUT.VM.side_effect = [vmOne, vmTwo, vmThree]
    runner = BenchmarkRunnerModules({'run_artifacts_path': '/tmp'})
    PatchModules(monkeypatch, runner)
    ocOne = mock.MagicMock(name='ocOne')
    ocTwo = mock.MagicMock(name='ocTwo')
    ocThree = mock.MagicMock(name='ocThree')
    ocFactory = mock.MagicMock(side_effect=[ocOne, ocTwo, ocThree])
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={'count': 0}),
        ocFactory,
        mock.MagicMock(),
    )
    bundle = next(generator)

    firstBatch = bundle.vmCreate(count=1, workloadName='first')
    secondBatch = bundle.vmCreate(count=2, workloadName='second')

    assert firstBatch == [vmOne]
    assert secondBatch == [vmTwo, vmThree]
    assert bundle.vmList == [vmOne, vmTwo, vmThree]
    assert runner.brMain.call_count == 2
    generator.close()
    MUT.VM.CleanUpVMs.assert_called_once_with(
        bundle.vmList,
        [
            (ocOne, 'first-vm-first', 'uid-one'),
            (ocTwo, 'second-vm-second-0', 'uid-two'),
            (ocThree, 'second-vm-second-1', 'uid-three'),
        ],
    )


def test_FxtVMbyBR_VMcreateAllowsCLIOverrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.uuid4.return_value = 'selected-rest'
    MUT.Path.is_file.return_value = False
    MUT.VM.GetVMuid.side_effect = [
        (True, '', ''),
        (True, 'selected-uid', ''),
    ]
    runner = BenchmarkRunnerModules({'run_artifacts_path': '/tmp'})
    PatchModules(monkeypatch, runner)
    defaultOC = mock.MagicMock()
    defaultVirtCtl = mock.MagicMock()
    selectedOC = mock.MagicMock()
    selectedVirtCtl = mock.MagicMock()
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={'count': 0}),
        defaultOC,
        defaultVirtCtl,
    )
    bundle = next(generator)

    created = bundle.vmCreate(
        count=1,
        ns='selected-ns',
        cliCfg=NamedDict(
            ocCfg={'context': 'selected-context'},
            vcCfg={'kubeconfig': '/selected-config'},
        ),
        ocCLI=selectedOC,
        virtCtlCLI=selectedVirtCtl,
    )

    assert created == [MUT.VM.return_value]
    selectedOC.assert_called_once_with(
        ns='selected-ns',
        context='selected-context',
    )
    selectedVirtCtl.assert_called_once_with(
        ns='selected-ns',
        kubeconfig='/selected-config',
    )
    defaultOC.assert_not_called()
    defaultVirtCtl.assert_not_called()
    generator.close()


@pytest.mark.parametrize('argument', ['request', 'vmList', 'ownVMs'])
def test_FxtVMbyBR_VMcreateRejectsInternalKeywordReplacementBeforeOperations(
    monkeypatch: pytest.MonkeyPatch,
    argument: str,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={'count': 0}),
        ocFactory,
        virtCtlFactory,
    )
    bundle = next(generator)
    sharedVMs = bundle.vmCreate.args[1]
    sharedOwnership = bundle.vmCreate.args[2]

    with pytest.raises(TypeError, match='multiple values'):
        bundle.vmCreate(**{argument: object()})

    assert bundle.vmCreate.args[1] is sharedVMs
    assert bundle.vmCreate.args[2] is sharedOwnership
    assert bundle.vmList is sharedVMs
    assert bundle.vmList == []
    assert sharedOwnership == []
    MUT.VM.GetVMuid.assert_not_called()
    ocFactory.assert_not_called()
    virtCtlFactory.assert_not_called()
    generator.close()


def test_FxtVMbyBR_PreyieldPartialFailureCleansCreatedVMs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.uuid4.return_value = 'partial-rest'
    MUT.Path.is_file.return_value = False
    MUT.VM.GetVMuid.side_effect = [
        (True, '', ''),
        (True, '', ''),
        (True, 'uid-zero', ''),
        (True, 'uid-one', ''),
    ]
    vmZero = mock.MagicMock(name='vmZero')
    MUT.VM.side_effect = [vmZero, RuntimeError('wrapper failed')]
    runner = BenchmarkRunnerModules({'run_artifacts_path': '/tmp'})
    PatchModules(monkeypatch, runner)
    ocZero = mock.MagicMock(name='ocZero')
    ocOne = mock.MagicMock(name='ocOne')
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={'count': 2}),
        mock.MagicMock(side_effect=[ocZero, ocOne]),
        mock.MagicMock(),
    )

    with pytest.raises(RuntimeError, match='wrapper failed'):
        next(generator)

    MUT.VM.CleanUpVMs.assert_called_once_with(
        [vmZero],
        [
            (ocZero, 'windows-vm-partial-0', 'uid-zero'),
            (ocOne, 'windows-vm-partial-1', 'uid-one'),
        ],
    )


def test_FxtVMbyBR_LaterPartialFailureCleansAggregateOnClose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.uuid4.return_value = 'partial-rest'
    MUT.Path.is_file.return_value = False
    MUT.VM.GetVMuid.side_effect = [
        (True, '', ''),
        (True, '', ''),
        (True, 'uid-zero', ''),
        (True, 'uid-one', ''),
    ]
    vmZero = mock.MagicMock(name='vmZero')
    primary = RuntimeError('wrapper failed')
    MUT.VM.side_effect = [vmZero, primary]
    runner = BenchmarkRunnerModules({'run_artifacts_path': '/tmp'})
    PatchModules(monkeypatch, runner)
    ocZero = mock.MagicMock(name='ocZero')
    ocOne = mock.MagicMock(name='ocOne')
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={'count': 0}),
        mock.MagicMock(side_effect=[ocZero, ocOne]),
        mock.MagicMock(),
    )
    bundle = next(generator)

    with pytest.raises(RuntimeError) as raised:
        bundle.vmCreate(count=2)

    assert raised.value is primary
    assert bundle.vmList == [vmZero]
    MUT.VM.CleanUpVMs.assert_not_called()
    generator.close()
    MUT.VM.CleanUpVMs.assert_called_once()
    cleanupVMs, cleanupOwnership = MUT.VM.CleanUpVMs.call_args.args
    assert cleanupVMs is bundle.vmList
    assert cleanupVMs == [vmZero]
    assert cleanupOwnership == [
        (ocZero, 'windows-vm-partial-0', 'uid-zero'),
        (ocOne, 'windows-vm-partial-1', 'uid-one'),
    ]
