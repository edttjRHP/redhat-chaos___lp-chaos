from dataclasses import FrozenInstanceError
import json
import builtins
import os
from types import SimpleNamespace
from unittest import mock

import pytest

import fixtures.BenchmarkRunner as MUT
from libs.CustomTypes import NamedDict
from libs.OpenShift.OCP.CLI.OC import OCcli
import Utils


@pytest.mark.parametrize('count,scaleNodes,workerNode', [
    (2, None, 'configured-worker'), (2, '', 'configured-worker'),
    (2, "['worker-a']", None), (2, 'raw-nodes', 'configured-worker'),
    (2, ' \t', 'configured-worker'),
    (0, None, None), (0, '', None), (1, None, None), (1, '', None),
])
def test_VMcreateByBR_DirectScaleNodes_ReviewRegression(
    monkeypatch: pytest.MonkeyPatch,
    count: int,
    scaleNodes: str | None,
    workerNode: str | None,
) -> None:
    if (scaleNodes is None): monkeypatch.delenv('SCALE_NODES', raising=False)
    else: monkeypatch.setenv('SCALE_NODES', scaleNodes)
    monkeypatch.setenv('WORKER_NODE', 'ambient-worker')
    runner = BenchmarkRunnerVenv('/synthetic/artifacts')
    PatchRunner(monkeypatch, runner)
    monkeypatch.setattr(MUT, 'VM', mock.Mock())
    monkeypatch.setattr(OCcli, 'GetResUID', mock.Mock(side_effect=[
        *((True, '', ''),) * count,
        *((True, f'uid-{idx}', '') for idx in range(count)),
    ]))
    ocFactory = mock.Mock()
    brEnv = {'SCALE_NODES': 'configured-nodes'}
    if (workerNode is not None): brEnv['WORKER_NODE'] = workerNode
    cfg = MUT._DEF__FXT_CFG | NamedDict(fxtInf=NamedDict(brCfg=NamedDict(
        count=count, startVM=False, brEnv=brEnv,
    )))
    params = MUT.FuncParsVMcreateByBR(fxtCfgObj=cfg)
    if ((count > 1) and (scaleNodes in (None, ''))):
        with pytest.raises(ValueError) as caught:
            MUT.VMcreateByBR(
                None, params, ocCLI=ocFactory, virtCtlCLI=mock.Mock(),
            )
        assert (
            str(caught.value) == (
                'Env. Var. `SCALE_NODES` must set to non-empty string for '
                'scale.'
            )
        )
        runner.call.assert_not_called()
        ocFactory.assert_not_called()
        return
    result = MUT.VMcreateByBR(
        None, params, ocCLI=ocFactory, virtCtlCLI=mock.Mock(),
    )
    assert (len(result) == count)
    if not count:
        runner.call.assert_not_called()
        ocFactory.assert_not_called()
        return
    env = runner.call.call_args.kwargs['envUpd']
    assert (env['SCALE_NODES'] == (scaleNodes if (count > 1) else ''))
    assert (env['SCALE'] == (str(count) if (count > 1) else ''))
    if (workerNode is None): assert ('WORKER_NODE' not in env)
    else: assert (env['WORKER_NODE'] == workerNode)


class BenchmarkRunnerVenv:
    def __init__(self, artifactsPath: str) -> None:
        self.artifactsPath = artifactsPath
        self.venv = mock.MagicMock(name='ModInVenv')
        self.call = self.venv.return_value.Call


def PatchRunner(
    monkeypatch: pytest.MonkeyPatch,
    runner: BenchmarkRunnerVenv,
) -> None:
    monkeypatch.setattr(MUT, 'ModInVenv', runner.venv)
    monkeypatch.setenv('RUN_ARTIFACTS_PATH', runner.artifactsPath)


def test__DEF__FXT_CFG_UsesNestedWorkloadConfiguration() -> None:
    cfg = MUT._DEF__FXT_CFG
    assert (cfg.fxtInf.brCfg == NamedDict(
        count=1, startVM=True, wlName='windows', wlKind='vm', brEnv={},
        timeout=NamedDict(vmStart='15m', vmRemove='5m'),
    ))
    assert ('wlName' not in cfg)
    assert ('wlKind' not in cfg)
    merged = cfg | NamedDict(
        fxtInf=NamedDict(brCfg=NamedDict(wlKind='custom')),
    )
    assert (merged.fxtInf.brCfg.wlName == 'windows')
    assert (merged.fxtInf.brCfg.wlKind == 'custom')
    assert (cfg.fxtInf.brCfg.wlKind == 'vm')


@pytest.mark.parametrize('outcome', ['ready', 'false', 'exception', 'manual'])
@pytest.mark.parametrize('customTimeout', [None, '7m'])
def test_FxtVMbyBR_StartTracksOwnershipAndPreservesFailures(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    customTimeout: str | None,
) -> None:
    monkeypatch.setenv('SCALE_NODES', "['synthetic-worker']")
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    MUT.uuid4.return_value = 'startup-rest'
    OCcli.GetResUID.side_effect = [
        (True, '', ''), (True, '', ''),
        (True, 'uid-zero', ''), (True, 'uid-one', ''),
    ]
    runner = BenchmarkRunnerVenv('/synthetic/artifacts')
    PatchRunner(monkeypatch, runner)
    wrappers = [mock.Mock(), mock.Mock()]
    for (index, vm) in enumerate(wrappers):
        vm.namespace = 'default'
        vm.name = f'windows-vm-startup-{index}'
    MUT.VM.side_effect = wrappers
    primary = RuntimeError('synthetic startup failure')
    if (outcome == 'exception'): wrappers[0].Start.side_effect = primary
    else: wrappers[0].Start.return_value = (outcome != 'false')
    wrappers[1].Start.return_value = True
    monkeypatch.setenv('UNIT_BR_CUSTOM', 'parent')
    config = NamedDict(
        count=2, startVM=(outcome != 'manual'),
        brEnv={
            'UNIT_BR_CUSTOM': 'child',
            'SAVE_ARTIFACTS_LOCAL': 'false', 'DELETE_ALL': 'yes',
        },
    )
    if customTimeout:
        config = config | NamedDict(timeout=NamedDict(
            vmStart=customTimeout, vmRemove='11m',
        ))
    clients = [mock.Mock(), mock.Mock()]
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={'fxtInf': NamedDict(brCfg=config)}),
        mock.Mock(side_effect=clients), mock.Mock(),
    )
    if (outcome in ('false', 'exception')):
        with pytest.raises(RuntimeError) as caught:
            next(generator)
        if (outcome == 'exception'): assert (caught.value is primary)
        else: assert ('default/windows-vm-startup-0' in str(caught.value))
        tracked = wrappers[:1]
        wrappers[1].Start.assert_not_called()
    else:
        bundle = next(generator)
        assert (bundle.vmList == wrappers)
        MUT.VM.CleanUpVMs.assert_not_called()
        generator.close()
        tracked = wrappers
    MUT.VM.CleanUpVMs.assert_called_once_with(tracked, [
        (clients[0], 'VirtualMachine/windows-vm-startup-0', 'uid-zero'),
        (clients[1], 'VirtualMachine/windows-vm-startup-1', 'uid-one'),
    ], optsCleanUpRes={'optsDelRes': {
        'waitTime': ('11m' if customTimeout else '5m'),
    }})
    if (outcome == 'manual'):
        for vm in wrappers: vm.Start.assert_not_called()
    else:
        wrappers[0].Start.assert_called_once_with(
            timeout=(customTimeout or '15m'),
        )
        if (outcome == 'ready'):
            wrappers[1].Start.assert_called_once_with(
                timeout=(customTimeout or '15m'),
            )
    environment = runner.call.call_args.kwargs['envUpd']
    assert (environment['SAVE_ARTIFACTS_LOCAL'] == 'true')
    assert (environment['DELETE_ALL'] == 'no')
    assert (environment['UNIT_BR_CUSTOM'] == 'child')
    assert (os.environ['UNIT_BR_CUSTOM'] == 'parent')


def test_FuncParsVMcreateByBR_RequiresConfigAndKeepsMutableLists() -> None:
    with pytest.raises(TypeError, match='fxtCfgObj'):
        MUT.FuncParsVMcreateByBR()

    config = MUT._DEF__FXT_CFG | NamedDict(
        fxtInf=NamedDict(brCfg=NamedDict(count=0)),
    )
    first = MUT.FuncParsVMcreateByBR(fxtCfgObj=config)
    second = MUT.FuncParsVMcreateByBR(fxtCfgObj=config)
    assert (first.fxtCfgObj is config)
    assert (first.vmList is not second.vmList)
    assert (first.ownResTrk.VMs is not second.ownResTrk.VMs)
    assert (first.vmList is not first.ownResTrk.VMs)
    for fld in ('fxtCfgObj', 'vmList', 'ownResTrk'):
        with pytest.raises(FrozenInstanceError):
            setattr(first, fld, None)

    vm = object()
    ownership = (object(), 'vm-name', 'uid')
    first.vmList.append(vm)
    first.ownResTrk.VMs.append(ownership)
    assert (first.vmList == [vm])
    assert (first.ownResTrk.VMs == [ownership])
    assert (second.vmList == [])
    assert (second.ownResTrk.VMs == [])


def test_FxtVMbyBR_RetainsSetupAndIsolatesTemporaryOverrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.Path.is_file.return_value = False
    MUT.uuid4.side_effect = ['first-rest', 'second-rest', 'third-rest']
    OCcli.GetResUID.side_effect = [
        (True, '', ''), (True, 'uid-one', ''),
        (True, '', ''), (True, 'uid-two', ''),
        (True, '', ''), (True, 'uid-three', ''),
    ]
    runner = BenchmarkRunnerVenv('/tmp')
    PatchRunner(monkeypatch, runner)
    setup = {
        'ns': 'setup-ns',
        'kCfg': '/setup/kubeconfig',
        'cliCfg': NamedDict(
            ocCfg={'context': 'setup-context'},
            vcCfg={'kubeconfig': '/setup-config'},
        ),
        'fxtInf': NamedDict(
            rootPath=NamedDict(br=MUT.Path('/setup/br-venv')),
            brCfg=NamedDict(wlName='setup', count=0),
            gstCfg=NamedDict(usrCrd=NamedDict(sshUsr='setup-user')),
        ),
    }
    overrides = {
        'ns': 'call-ns',
        'kCfg': '/call/kubeconfig',
        'cliCfg': NamedDict(ocCfg={'context': 'call-context'}),
        'fxtInf': NamedDict(
            rootPath=NamedDict(br=MUT.Path('/call/br-venv')),
            brCfg=NamedDict(count=1, timeout=NamedDict(vmRemove='71s')),
            gstCfg=NamedDict(usrCrd=NamedDict(sshUsr='call-user')),
        ),
    }
    (setupBefore, overridesBefore, defaultsBefore) = json.loads(json.dumps((
        setup, overrides, MUT._DEF__FXT_CFG,
    ), default=str))
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()
    request = SimpleNamespace(param=setup)
    generator = MUT.FxtVMbyBR.__wrapped__(
        request, ocFactory, virtCtlFactory,
    )
    bundle = next(generator)
    fPars = bundle.vmCreate.args[1]
    configBefore = json.loads(json.dumps(fPars.fxtCfgObj, default=str))
    assert (bundle.vmList is fPars.vmList)
    assert (bundle.vmCreate() == [])
    request.param = {
        'ns': 'ignored-later-request',
        'fxtInf': NamedDict(brCfg=NamedDict(count=-1)),
    }

    first = bundle.vmCreate(**overrides)
    assert (bundle.vmCreate() == [])
    second = bundle.vmCreate(fxtInf=NamedDict(brCfg=NamedDict(count=1)))
    third = bundle.vmCreate(fxtInf=NamedDict(brCfg=NamedDict(count=1)))

    assert (bundle.vmList == (first + second + third))
    assert (runner.venv.call_args_list == [
        mock.call(MUT.Path('/call/br-venv')),
        mock.call(MUT.Path('/setup/br-venv')),
        mock.call(MUT.Path('/setup/br-venv')),
    ])
    environments = [
        call.kwargs['envUpd'] for call in runner.call.call_args_list
    ]
    assert ([env['KUBECONFIG'] for env in environments] == [
        '/call/kubeconfig', '/setup/kubeconfig', '/setup/kubeconfig',
    ])
    assert ([env['NAMESPACE'] for env in environments] == [
        'call-ns', 'setup-ns', 'setup-ns',
    ])
    assert (ocFactory.call_args_list == [
        mock.call(ns='call-ns', context='call-context'),
        mock.call(ns='setup-ns', context='setup-context'),
        mock.call(ns='setup-ns', context='setup-context'),
    ])
    assert (virtCtlFactory.call_args_list == [
        mock.call(ns='call-ns', kubeconfig='/setup-config'),
        mock.call(ns='setup-ns', kubeconfig='/setup-config'),
        mock.call(ns='setup-ns', kubeconfig='/setup-config'),
    ])
    assert ([call.kwargs['sshUsr'] for call in MUT.VM.call_args_list] == [
        'call-user', 'setup-user', 'setup-user',
    ])
    assert ([call.kwargs['name'] for call in MUT.VM.call_args_list] == [
        'setup-vm-first', 'setup-vm-second', 'setup-vm-third',
    ])
    assert (
        json.loads(json.dumps(fPars.fxtCfgObj, default=str)) == configBefore
    )
    assert (json.loads(json.dumps(setup, default=str)) == setupBefore)
    assert (json.loads(json.dumps(overrides, default=str)) == overridesBefore)
    assert (setup['fxtInf'].rootPath.br == MUT.Path('/setup/br-venv'))
    assert (overrides['fxtInf'].rootPath.br == MUT.Path('/call/br-venv'))
    assert (
        json.loads(json.dumps(MUT._DEF__FXT_CFG, default=str)) ==
        defaultsBefore
    )
    generator.close()
    (cleanupVMs, cleanupOwnership) = MUT.VM.CleanUpVMs.call_args.args
    assert (MUT.VM.CleanUpVMs.call_args.kwargs == {
        'optsCleanUpRes': {'optsDelRes': {'waitTime': '5m'}},
    })
    assert (cleanupVMs is bundle.vmList)
    assert (cleanupOwnership is fPars.ownResTrk.VMs)
    assert ([record[2] for record in cleanupOwnership] == [
        'uid-one', 'uid-two', 'uid-three',
    ])


@pytest.mark.parametrize('hasRequestParam', [False, True])
def test_FxtVMbyBR_DefaultSetupIsRetainedAcrossCalls(
    monkeypatch: pytest.MonkeyPatch,
    hasRequestParam: bool,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.Path.is_file.return_value = False
    MUT.uuid4.side_effect = ['initial-rest', 'later-rest']
    OCcli.GetResUID.side_effect = [
        (True, '', ''), (True, 'initial-uid', ''),
        (True, '', ''), (True, 'later-uid', ''),
    ]
    runner = BenchmarkRunnerVenv('/tmp')
    PatchRunner(monkeypatch, runner)
    request = (
        SimpleNamespace(param={}) if hasRequestParam else SimpleNamespace()
    )
    generator = MUT.FxtVMbyBR.__wrapped__(
        request, mock.MagicMock(), mock.MagicMock(),
    )
    bundle = next(generator)
    assert (len(bundle.vmCreate()) == 1)
    assert (len(bundle.vmList) == 2)
    assert (runner.call.call_count == 2)
    generator.close()


def test_VMcreateByBR_NegativeCountIsRejectedBeforeImportingRunner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
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
            MUT.FuncParsVMcreateByBR(
                fxtCfgObj=MUT._DEF__FXT_CFG,
                vmList=vmList, ownResTrk=NamedDict(VMs=ownVMs),
            ),
            ocCLI=ocFactory,
            virtCtlCLI=virtCtlFactory,
            fxtInf=NamedDict(brCfg=NamedDict(count=-1)),
        )

    assert (vmList == [])
    assert (ownVMs == [])
    ocFactory.assert_not_called()
    virtCtlFactory.assert_not_called()
    OCcli.GetResUID.assert_not_called()
    MUT.uuid4.assert_not_called()
    MUT.tempfile.mkdtemp.assert_not_called()


def test_VMcreateByBR_ZeroCountReturnsEmptyWithoutRunner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.tempfile, 'mkdtemp')
    vmList = []
    ownVMs = []
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()
    runner = BenchmarkRunnerVenv('/tmp')
    PatchRunner(monkeypatch, runner)

    result = MUT.VMcreateByBR(
        SimpleNamespace(),
        MUT.FuncParsVMcreateByBR(
            fxtCfgObj=MUT._DEF__FXT_CFG,
            vmList=vmList, ownResTrk=NamedDict(VMs=ownVMs),
        ),
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
        fxtInf=NamedDict(brCfg=NamedDict(count=0)),
    )

    assert (result == [])
    assert (vmList == [])
    assert (ownVMs == [])
    ocFactory.assert_not_called()
    virtCtlFactory.assert_not_called()
    OCcli.GetResUID.assert_not_called()
    MUT.uuid4.assert_not_called()
    MUT.tempfile.mkdtemp.assert_not_called()
    runner.call.assert_not_called()
    runner.venv.assert_not_called()


def test_VMcreateByBR_SingleVMhappyPathUsesGeneratedIdentityKey(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origImport = builtins.__import__

    def ImportWithoutBR(name: str, *args: object, **kwArgs: object) -> object:
        assert not name.startswith('benchmark_runner')
        return origImport(name, *args, **kwArgs)

    monkeypatch.setattr(builtins, '__import__', ImportWithoutBR)
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.uuid4.return_value = '12345678-abcd-0000-0000-000000000000'
    MUT.Path.is_file.return_value = True
    OCcli.GetResUID.side_effect = [
        (True, '', ''), (True, 'uid-one', ''),
    ]
    runner = BenchmarkRunnerVenv('/artifacts')
    PatchRunner(monkeypatch, runner)
    ocFactory = mock.MagicMock(name='ocFactory')
    virtCtlFactory = mock.MagicMock(name='virtCtlFactory')
    vmList = []
    ownVMs = []

    created = MUT.VMcreateByBR(
        SimpleNamespace(),
        MUT.FuncParsVMcreateByBR(
            fxtCfgObj=MUT._DEF__FXT_CFG,
            vmList=vmList, ownResTrk=NamedDict(VMs=ownVMs),
        ),
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
        cliCfg=NamedDict(ocCfg={'context': 'oc-context'}),
        fxtInf=NamedDict(
            rootPath=NamedDict(br=MUT.Path('/custom/br-venv')),
            brCfg=NamedDict(wlName='windows', wlKind='vm'),
            gstCfg=NamedDict(usrCrd=NamedDict(sshUsr='unit-user')),
        ),
    )

    assert (created == [MUT.VM.return_value])
    assert (vmList == created)
    assert (ownVMs == [
        (
            ocFactory.return_value,
            'VirtualMachine/windows-vm-12345678',
            'uid-one',
        ),
    ])
    environment = runner.call.call_args.kwargs['envUpd']
    assert (environment['WORKLOAD'] == 'windows_vm')
    assert (environment['RUN_TYPE'] == 'test_ci')
    assert (environment['NAMESPACE'] == 'default')
    assert (environment['DELETE_ALL'] == 'no')
    assert (environment['SAVE_ARTIFACTS_LOCAL'] == 'true')
    assert (environment['SCALE'] == '')
    assert (environment['SCALE_NODES'] == '')
    assert (environment['UUID'] == str(MUT.uuid4.return_value))
    assert (environment['KUBECONFIG'] == MUT._DEF__FXT_CFG.kCfg)
    assert (environment['RUN_ARTIFACTS_PATH'] == '/artifacts')
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

    runner.venv.assert_called_once_with(MUT.Path('/custom/br-venv'))
    runner.call.assert_called_once_with(
        'benchmark_runner.main.main', 'main', envUpd=environment,
    )


def test_VMcreateByBR_ScalePathNamesVMsAndUsesFallbackKey(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    monkeypatch.setenv('WORKER_NODE', 'worker-a')
    monkeypatch.setenv('SCALE_NODES', "['worker-a']")
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    Utils.AutoPatch(monkeypatch, MUT.tempfile, 'mkdtemp')
    MUT.uuid4.return_value = 'deadbeef-rest'
    MUT.Path.is_file.return_value = False
    MUT.tempfile.mkdtemp.return_value = '/tmp/run'
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
        (True, '', ''),
        (True, 'uid-0', ''),
        (True, 'uid-1', ''),
    ]
    runner = BenchmarkRunnerVenv('')
    PatchRunner(monkeypatch, runner)
    ocZero = mock.MagicMock(name='ocZero')
    ocOne = mock.MagicMock(name='ocOne')
    ocFactory = mock.MagicMock(side_effect=[ocZero, ocOne])
    virtCtlFactory = mock.MagicMock()
    vmList = []
    ownVMs = []

    created = MUT.VMcreateByBR(
        SimpleNamespace(),
        MUT.FuncParsVMcreateByBR(
            fxtCfgObj=MUT._DEF__FXT_CFG,
            vmList=vmList, ownResTrk=NamedDict(VMs=ownVMs),
        ),
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
        fxtInf=NamedDict(
            brCfg=NamedDict(wlName='windows_scale', count=2),
        ),
    )

    assert (len(created) == 2)
    assert (vmList == created)
    assert (ownVMs == [
        (ocZero, 'VirtualMachine/windows-scale-vm-deadbeef-0', 'uid-0'),
        (ocOne, 'VirtualMachine/windows-scale-vm-deadbeef-1', 'uid-1'),
    ])
    environment = runner.call.call_args.kwargs['envUpd']
    assert (environment['SCALE'] == '2')
    assert (environment['SCALE_NODES'] == "['worker-a']")
    assert (environment['RUN_ARTIFACTS_PATH'] == '/tmp/run')
    MUT.tempfile.mkdtemp.assert_called_once_with()
    assert ([item.kwargs['name'] for item in MUT.VM.call_args_list] == [
        'windows-scale-vm-deadbeef-0',
        'windows-scale-vm-deadbeef-1',
    ])
    assert all(
        (item.kwargs['sshKey'] == '~/.ssh/openshift-qe.pem')
        for item in MUT.VM.call_args_list
    )


def test_VMcreateByBR_NestedOverridesDoNotMutateDefaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.uuid4.side_effect = ['custom-rest', 'default-rest']
    MUT.Path.is_file.return_value = False
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
        (True, 'custom-uid', ''),
        (True, '', ''),
        (True, 'default-uid', ''),
    ]
    runner = BenchmarkRunnerVenv('/tmp')
    PatchRunner(monkeypatch, runner)
    defaultFxtCfg = json.loads(json.dumps(MUT._DEF__FXT_CFG, default=str))
    defaultNestedValues = (
        dict(MUT._DEF__FXT_CFG.cliCfg.ocCfg),
        dict(MUT._DEF__FXT_CFG.cliCfg.vcCfg),
        dict(MUT._DEF__FXT_CFG.fxtInf.gstCfg.usrCrd),
    )
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()

    MUT.VMcreateByBR(
        SimpleNamespace(),
        MUT.FuncParsVMcreateByBR(
            fxtCfgObj=MUT._DEF__FXT_CFG,
            vmList=[], ownResTrk=NamedDict(VMs=[]),
        ),
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
        cliCfg=NamedDict(
            ocCfg={'context': 'custom-context'},
            vcCfg={'kubeconfig': '/custom-config'},
        ),
        fxtInf=NamedDict(
            gstCfg=NamedDict(usrCrd=NamedDict(sshUsr='custom-user')),
        ),
    )
    MUT.VMcreateByBR(
        SimpleNamespace(),
        MUT.FuncParsVMcreateByBR(
            fxtCfgObj=MUT._DEF__FXT_CFG,
            vmList=[], ownResTrk=NamedDict(VMs=[]),
        ),
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
    )

    assert (
        json.loads(json.dumps(MUT._DEF__FXT_CFG, default=str)) == defaultFxtCfg
    )
    assert ((
        dict(MUT._DEF__FXT_CFG.cliCfg.ocCfg),
        dict(MUT._DEF__FXT_CFG.cliCfg.vcCfg),
        dict(MUT._DEF__FXT_CFG.fxtInf.gstCfg.usrCrd),
    ) == defaultNestedValues)
    assert (ocFactory.call_args_list == [
        mock.call(ns='default', context='custom-context'),
        mock.call(ns='default'),
    ])
    assert (virtCtlFactory.call_args_list == [
        mock.call(ns='default', kubeconfig='/custom-config'),
        mock.call(ns='default'),
    ])
    assert (MUT.VM.call_args_list[0].kwargs['sshUsr'] == 'custom-user')
    assert (MUT.VM.call_args_list[0].kwargs['sshKey'] == (
        defaultFxtCfg['fxtInf']['gstCfg']['usrCrd']['sshKey']
    ))
    assert (MUT.VM.call_args_list[1].kwargs['sshUsr'] == (
        defaultFxtCfg['fxtInf']['gstCfg']['usrCrd']['sshUsr']
    ))
    assert (MUT.VM.call_args_list[1].kwargs['sshKey'] == (
        defaultFxtCfg['fxtInf']['gstCfg']['usrCrd']['sshKey']
    ))


def test_VMcreateByBR_PreflightLookupFailureRaisesWithoutTracking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    MUT.uuid4.return_value = 'abcd-rest'
    OCcli.GetResUID.return_value = (False, '', 'lookup error')
    runner = BenchmarkRunnerVenv('/tmp')
    PatchRunner(monkeypatch, runner)
    vmList = []
    ownVMs = []

    with pytest.raises(RuntimeError, match='lookup error'):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            MUT.FuncParsVMcreateByBR(
                fxtCfgObj=MUT._DEF__FXT_CFG,
                vmList=vmList, ownResTrk=NamedDict(VMs=ownVMs),
            ),
            ocCLI=mock.MagicMock(),
            virtCtlCLI=mock.MagicMock(),
        )

    assert (vmList == [])
    assert (ownVMs == [])
    runner.venv.assert_not_called()


def test_VMcreateByBR_PreflightExistingVMIsRefused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    MUT.uuid4.return_value = 'abcd-rest'
    OCcli.GetResUID.return_value = (True, 'existing-uid', '')
    runner = BenchmarkRunnerVenv('/tmp')
    PatchRunner(monkeypatch, runner)

    with pytest.raises(RuntimeError, match='already exists'):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            MUT.FuncParsVMcreateByBR(
                fxtCfgObj=MUT._DEF__FXT_CFG,
                vmList=[], ownResTrk=NamedDict(VMs=[]),
            ),
            ocCLI=mock.MagicMock(),
            virtCtlCLI=mock.MagicMock(),
        )

    runner.venv.assert_not_called()


def test_VMcreateByBR_RunnerFailurePreservesPrimaryErrorDuringReconciliation(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv('SCALE_NODES', "['synthetic-worker']")
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    MUT.uuid4.return_value = 'abcd-rest'
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
        (True, '', ''),
        (True, '', ''),
        (False, '', 'lookup failed'),
        (True, '', ''),
        RuntimeError('lookup exploded'),
    ]
    runner = BenchmarkRunnerVenv('/tmp')
    primary = ValueError('runner failed')
    runner.call.side_effect = primary
    PatchRunner(monkeypatch, runner)

    with caplog.at_level('ERROR'), pytest.raises(ValueError) as raised:
        MUT.VMcreateByBR(
            SimpleNamespace(),
            MUT.FuncParsVMcreateByBR(
                fxtCfgObj=MUT._DEF__FXT_CFG,
                vmList=[], ownResTrk=NamedDict(VMs=[]),
            ),
            ocCLI=mock.MagicMock(),
            virtCtlCLI=mock.MagicMock(),
            fxtInf=NamedDict(brCfg=NamedDict(count=3)),
        )

    assert (raised.value is primary)
    runner.call.assert_called_once()
    assert (runner.call.call_args.args == (
        'benchmark_runner.main.main', 'main',
    ))
    assert ('Preserving benchmark-runner failure' in caplog.text)


def test_VMcreateByBR_ReconciliationLookupFailureRaisesWithoutRunnerError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    MUT.uuid4.return_value = 'abcd-rest'
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
        (False, '', 'lookup failed'),
    ]
    runner = BenchmarkRunnerVenv('/tmp')
    PatchRunner(monkeypatch, runner)

    with pytest.raises(RuntimeError, match='reconcile'):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            MUT.FuncParsVMcreateByBR(
                fxtCfgObj=MUT._DEF__FXT_CFG,
                vmList=[], ownResTrk=NamedDict(VMs=[]),
            ),
            ocCLI=mock.MagicMock(),
            virtCtlCLI=mock.MagicMock(),
        )


def test_VMcreateByBR_ReconciliationMissingVMRaisesWithoutRunnerError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    MUT.uuid4.return_value = 'abcd-rest'
    OCcli.GetResUID.side_effect = [(True, '', ''), (True, '', '')]
    runner = BenchmarkRunnerVenv('/tmp')
    PatchRunner(monkeypatch, runner)

    with pytest.raises(RuntimeError, match='did not create expected'):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            MUT.FuncParsVMcreateByBR(
                fxtCfgObj=MUT._DEF__FXT_CFG,
                vmList=[], ownResTrk=NamedDict(VMs=[]),
            ),
            ocCLI=mock.MagicMock(),
            virtCtlCLI=mock.MagicMock(),
        )


def test_FxtVMbyBR_RegistrationUsesModuleScope() -> None:
    assert (MUT.FxtVMbyBR.name == 'fxtVMbyBR')
    assert (MUT.FxtVMbyBR._fixture_function_marker.scope == 'module')


def test_FxtVMbyBR_InitialNegativeFailsBeforeYieldWithoutCleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={
            'fxtInf': NamedDict(brCfg=NamedDict(count=-1)),
        }),
        mock.MagicMock(),
        mock.MagicMock(),
    )

    with pytest.raises(ValueError, match='must not be negative'):
        next(generator)

    MUT.VM.CleanUpVMs.assert_not_called()


def test_FxtVMbyBR_InitialZeroYieldsFrozenEmptyBundleWithoutCleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.tempfile, 'mkdtemp')
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={
            'fxtInf': NamedDict(brCfg=NamedDict(count=0)),
        }),
        mock.MagicMock(),
        mock.MagicMock(),
    )

    bundle = next(generator)

    assert isinstance(bundle, MUT.FxtObjVMbyBR)
    assert issubclass(MUT.FxtObjVMbyBR, MUT.FxtObjVM)
    assert (bundle.vmList == [])
    assert (bundle.vmCreate(fxtInf=NamedDict(brCfg=NamedDict(count=0))) == [])
    with pytest.raises(FrozenInstanceError):
        bundle.vmList = []
    generator.close()
    MUT.VM.CleanUpVMs.assert_not_called()
    OCcli.GetResUID.assert_not_called()
    MUT.uuid4.assert_not_called()
    MUT.tempfile.mkdtemp.assert_not_called()


def test_FxtVMbyBR_InitialSuccessUsesSelectedConfigAndCleansOnClose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.uuid4.return_value = 'initial-rest'
    MUT.Path.is_file.return_value = False
    OCcli.GetResUID.side_effect = [
        (True, '', ''), (True, 'initial-uid', ''),
    ]
    vm = mock.MagicMock(name='initialVM')
    MUT.VM.return_value = vm
    runner = BenchmarkRunnerVenv('/tmp')
    PatchRunner(monkeypatch, runner)
    oc = mock.MagicMock(name='initialOC')
    ocFactory = mock.MagicMock(return_value=oc)
    virtCtlFactory = mock.MagicMock()
    generator = MUT.FxtVMbyBR.__wrapped__(
        request=SimpleNamespace(param={
            'cliCfg': NamedDict(
                ocCfg={'context': 'initial-context'},
                vcCfg={'kubeconfig': '/initial-config'},
            ),
            'ns': 'initial-ns',
            'fxtInf': NamedDict(
                brCfg=NamedDict(wlName='initial', count=1),
            ),
        }),
        fxtOCcli=ocFactory,
        fxtVirtCtlCLI=virtCtlFactory,
    )

    bundle = next(generator)

    assert (bundle.vmList == [vm])
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
        [(oc, 'VirtualMachine/initial-vm-initial', 'initial-uid')],
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )


def test_FxtVMbyBR_PerCallReturnsAreAggregatedAcrossBatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('SCALE_NODES', "['synthetic-worker']")
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.uuid4.side_effect = ['first-rest', 'second-rest']
    MUT.Path.is_file.return_value = False
    OCcli.GetResUID.side_effect = [
        (True, '', ''), (True, 'uid-one', ''),
        (True, '', ''),
        (True, '', ''), (True, 'uid-two', ''),
        (True, 'uid-three', ''),
    ]
    vmOne = mock.MagicMock(name='vmOne')
    vmTwo = mock.MagicMock(name='vmTwo')
    vmThree = mock.MagicMock(name='vmThree')
    MUT.VM.side_effect = [vmOne, vmTwo, vmThree]
    runner = BenchmarkRunnerVenv('/tmp')
    PatchRunner(monkeypatch, runner)
    ocOne = mock.MagicMock(name='ocOne')
    ocTwo = mock.MagicMock(name='ocTwo')
    ocThree = mock.MagicMock(name='ocThree')
    ocFactory = mock.MagicMock(side_effect=[ocOne, ocTwo, ocThree])
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={
            'fxtInf': NamedDict(brCfg=NamedDict(count=0)),
        }),
        ocFactory,
        mock.MagicMock(),
    )
    bundle = next(generator)

    firstBatch = bundle.vmCreate(
        fxtInf=NamedDict(brCfg=NamedDict(wlName='first', count=1)),
    )
    secondBatch = bundle.vmCreate(
        fxtInf=NamedDict(brCfg=NamedDict(wlName='second', count=2)),
    )

    assert (firstBatch == [vmOne])
    assert (secondBatch == [vmTwo, vmThree])
    assert (bundle.vmList == [vmOne, vmTwo, vmThree])
    assert (runner.call.call_count == 2)
    generator.close()
    MUT.VM.CleanUpVMs.assert_called_once_with(
        bundle.vmList,
        [
            (ocOne, 'VirtualMachine/first-vm-first', 'uid-one'),
            (ocTwo, 'VirtualMachine/second-vm-second-0', 'uid-two'),
            (ocThree, 'VirtualMachine/second-vm-second-1', 'uid-three'),
        ],
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )


def test_FxtVMbyBR_VMcreateAllowsCLIOverrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.uuid4.return_value = 'selected-rest'
    MUT.Path.is_file.return_value = False
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
        (True, 'selected-uid', ''),
    ]
    runner = BenchmarkRunnerVenv('/tmp')
    PatchRunner(monkeypatch, runner)
    defaultOC = mock.MagicMock()
    defaultVirtCtl = mock.MagicMock()
    selectedOC = mock.MagicMock()
    selectedVirtCtl = mock.MagicMock()
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={
            'fxtInf': NamedDict(brCfg=NamedDict(count=0)),
        }),
        defaultOC,
        defaultVirtCtl,
    )
    bundle = next(generator)

    created = bundle.vmCreate(
        ns='selected-ns',
        cliCfg=NamedDict(
            ocCfg={'context': 'selected-context'},
            vcCfg={'kubeconfig': '/selected-config'},
        ),
        ocCLI=selectedOC,
        virtCtlCLI=selectedVirtCtl,
        fxtInf=NamedDict(brCfg=NamedDict(count=1)),
    )

    assert (created == [MUT.VM.return_value])
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


@pytest.mark.parametrize('argument', ['request', 'fPars'])
def test_FxtVMbyBR_VMcreateRejectsInternalKeywordReplacementBeforeOperations(
    monkeypatch: pytest.MonkeyPatch,
    argument: str,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={
            'fxtInf': NamedDict(brCfg=NamedDict(count=0)),
        }),
        ocFactory,
        virtCtlFactory,
    )
    bundle = next(generator)
    sharedVMs = bundle.vmCreate.args[1].vmList
    sharedOwnership = bundle.vmCreate.args[1].ownResTrk.VMs

    with pytest.raises(TypeError, match='multiple values'):
        bundle.vmCreate(**{argument: object()})

    assert (bundle.vmCreate.args[1].vmList is sharedVMs)
    assert (bundle.vmCreate.args[1].ownResTrk.VMs is sharedOwnership)
    assert (bundle.vmList is sharedVMs)
    assert (bundle.vmList == [])
    assert (sharedOwnership == [])
    OCcli.GetResUID.assert_not_called()
    ocFactory.assert_not_called()
    virtCtlFactory.assert_not_called()
    generator.close()


def test_FxtVMbyBR_PreyieldPartialFailureCleansCreatedVMs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('SCALE_NODES', "['synthetic-worker']")
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.uuid4.return_value = 'partial-rest'
    MUT.Path.is_file.return_value = False
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
        (True, '', ''),
        (True, 'uid-zero', ''),
        (True, 'uid-one', ''),
    ]
    vmZero = mock.MagicMock(name='vmZero')
    MUT.VM.side_effect = [vmZero, RuntimeError('wrapper failed')]
    runner = BenchmarkRunnerVenv('/tmp')
    PatchRunner(monkeypatch, runner)
    ocZero = mock.MagicMock(name='ocZero')
    ocOne = mock.MagicMock(name='ocOne')
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={
            'fxtInf': NamedDict(brCfg=NamedDict(count=2)),
        }),
        mock.MagicMock(side_effect=[ocZero, ocOne]),
        mock.MagicMock(),
    )

    with pytest.raises(RuntimeError, match='wrapper failed'):
        next(generator)

    MUT.VM.CleanUpVMs.assert_called_once_with(
        [vmZero],
        [
            (ocZero, 'VirtualMachine/windows-vm-partial-0', 'uid-zero'),
            (ocOne, 'VirtualMachine/windows-vm-partial-1', 'uid-one'),
        ],
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )


def test_FxtVMbyBR_LaterPartialFailureCleansAggregateOnClose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('SCALE_NODES', "['synthetic-worker']")
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT, 'uuid4')
    Utils.AutoPatch(monkeypatch, MUT.Path, 'is_file')
    MUT.uuid4.return_value = 'partial-rest'
    MUT.Path.is_file.return_value = False
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
        (True, '', ''),
        (True, 'uid-zero', ''),
        (True, 'uid-one', ''),
    ]
    vmZero = mock.MagicMock(name='vmZero')
    primary = RuntimeError('wrapper failed')
    MUT.VM.side_effect = [vmZero, primary]
    runner = BenchmarkRunnerVenv('/tmp')
    PatchRunner(monkeypatch, runner)
    ocZero = mock.MagicMock(name='ocZero')
    ocOne = mock.MagicMock(name='ocOne')
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={
            'fxtInf': NamedDict(brCfg=NamedDict(count=0)),
        }),
        mock.MagicMock(side_effect=[ocZero, ocOne]),
        mock.MagicMock(),
    )
    bundle = next(generator)

    with pytest.raises(RuntimeError) as raised:
        bundle.vmCreate(fxtInf=NamedDict(brCfg=NamedDict(count=2)))

    assert (raised.value is primary)
    assert (bundle.vmList == [vmZero])
    MUT.VM.CleanUpVMs.assert_not_called()
    generator.close()
    MUT.VM.CleanUpVMs.assert_called_once()
    (cleanupVMs, cleanupOwnership) = MUT.VM.CleanUpVMs.call_args.args
    assert (MUT.VM.CleanUpVMs.call_args.kwargs == {
        'optsCleanUpRes': {'optsDelRes': {'waitTime': '5m'}},
    })
    assert (cleanupVMs is bundle.vmList)
    assert (cleanupVMs == [vmZero])
    assert (cleanupOwnership == [
        (ocZero, 'VirtualMachine/windows-vm-partial-0', 'uid-zero'),
        (ocOne, 'VirtualMachine/windows-vm-partial-1', 'uid-one'),
    ])


@pytest.mark.parametrize('cleanupFails', [False, True])
def test_FxtVMbyBR_SetupFailurePreservesPrimaryAndCleansOwnedVMs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    cleanupFails: bool,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    primary = ValueError('primary provisioning failure')
    cleanup = RuntimeError('cleanup failure')
    owned = (mock.MagicMock(), 'VirtualMachine/partial', 'uid-partial')
    if cleanupFails: MUT.VM.CleanUpVMs.side_effect = cleanup

    def FailAfterOwnership(
        request: object,
        fPars: MUT.FuncParsVMcreateByBR,
        **kwArgs: object,
    ) -> list[object]:
        fPars.ownResTrk.VMs.append(owned)
        raise primary

    monkeypatch.setattr(MUT, 'VMcreateByBR', FailAfterOwnership)
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={
            'fxtInf': NamedDict(brCfg=NamedDict(count=0)),
        }),
        mock.MagicMock(),
        mock.MagicMock(),
    )

    with pytest.raises(ValueError) as raised:
        next(generator)

    assert (raised.value is primary)
    MUT.VM.CleanUpVMs.assert_called_once_with(
        [], [owned], optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )
    if cleanupFails:
        assert (
            'Could not clean up fixture VMs: cleanup failure' in caplog.text
        )
    else:
        assert ('Could not clean up fixture VMs' not in caplog.text)


@pytest.mark.parametrize('teardown', ['resume', 'close'])
@pytest.mark.parametrize('cleanupFails', [False, True])
def test_FxtVMbyBR_TeardownSurfacesCleanupFailureWithoutPrimary(
    monkeypatch: pytest.MonkeyPatch,
    teardown: str,
    cleanupFails: bool,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    cleanup = RuntimeError('cleanup failure')
    if cleanupFails: MUT.VM.CleanUpVMs.side_effect = cleanup
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={
            'fxtInf': NamedDict(brCfg=NamedDict(count=0)),
        }),
        mock.MagicMock(),
        mock.MagicMock(),
    )
    bundle = next(generator)
    vm = mock.MagicMock()
    bundle.vmList.append(vm)
    owned = bundle.vmCreate.args[1].ownResTrk.VMs
    finish = generator.close if (teardown == 'close') else (
        lambda: next(generator)
    )

    if cleanupFails:
        with pytest.raises(RuntimeError) as raised:
            finish()
        assert (raised.value is cleanup)
    elif (teardown == 'resume'):
        with pytest.raises(StopIteration): finish()
    else:
        finish()

    MUT.VM.CleanUpVMs.assert_called_once_with(
        bundle.vmList, owned,
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )
    assert (bundle.vmList == [vm])


def test_FxtVMbyBR_TestFailurePreservesPrimaryWhenCleanupFails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    primary = ValueError('primary test failure')
    MUT.VM.CleanUpVMs.side_effect = RuntimeError('cleanup failure')
    generator = MUT.FxtVMbyBR.__wrapped__(
        SimpleNamespace(param={
            'fxtInf': NamedDict(brCfg=NamedDict(count=0)),
        }),
        mock.MagicMock(),
        mock.MagicMock(),
    )
    bundle = next(generator)
    vm = mock.MagicMock()
    bundle.vmList.append(vm)
    owned = bundle.vmCreate.args[1].ownResTrk.VMs

    with pytest.raises(ValueError) as raised:
        generator.throw(primary)

    assert (raised.value is primary)
    MUT.VM.CleanUpVMs.assert_called_once_with(
        bundle.vmList, owned,
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )
    assert ('Could not clean up fixture VMs: cleanup failure' in caplog.text)
