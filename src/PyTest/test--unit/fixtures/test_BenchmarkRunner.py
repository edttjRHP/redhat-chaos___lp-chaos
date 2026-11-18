from collections import deque
from collections.abc import Callable
from dataclasses import FrozenInstanceError
import json
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import get_args, get_origin
from unittest import mock

import pytest

import fixtures.BenchmarkRunner as MUT

# Capture the real import-time objects before PatchModule replaces module-local
# bindings. Tests of the literal production default use this reference only.
SOURCE_DEFAULT_CONFIG = MUT._DEF__FXT_CFG


def JSONText(value: object) -> str:
    '''Serialize raw test input without interpreting its application policy.'''
    return json.dumps(value, separators=(',', ':'))


class TimeoutConfig(SimpleNamespace):
    '''Plain explicit timeout fields with the two source-supported lookups.'''

    def __getitem__(self, key: str) -> int:
        if (key not in ('brExecOH', 'brExecVM')): raise KeyError(key)

        return getattr(self, key)


class CaseConfig(SimpleNamespace):
    '''Explicit values; union returns a queued result, never merges.'''

    def __init__(self, configScript: 'ConfigScript', **values: object) -> None:
        super().__init__(**values)
        self._configScript = configScript

    def __or__(self, rawConfig: object) -> 'CaseConfig':
        return self._configScript.Merge(self, rawConfig)


class ConfigScript:
    '''Record factory/operator inputs; return queued values only.'''

    def __init__(self) -> None:
        self.factoryCalls = []
        self.mergeCalls: list[tuple[object, object]] = []
        self.expectedMerges: deque[tuple[object, object]] = deque()
        self.factoryResults: deque[object] = deque()
        self.mergedConfigs: deque[CaseConfig] = deque()

    def QueueFactoryResults(self, *results: object) -> None:
        self.factoryResults.extend(results)

    def QueueMergedConfigs(self, *configs: CaseConfig) -> None:
        self.mergedConfigs.extend(configs)

    def Factory(self, *args: object, **kwargs: object) -> object:
        self.factoryCalls.append((args, kwargs))
        if not self.factoryResults:
            raise AssertionError('Unqueued config-factory call')
        return self.factoryResults.popleft()

    def Merge(self, left: object, right: object) -> CaseConfig:
        self.mergeCalls.append((left, right))
        if not self.expectedMerges:
            raise AssertionError('Unqueued config-operator operands')
        (expectedLeft, expectedRight) = self.expectedMerges.popleft()
        assert (left is expectedLeft)
        assert (right is expectedRight)
        if not self.mergedConfigs:
            raise AssertionError('Unqueued config-operator call')
        return self.mergedConfigs.popleft()

    def Config(
        self,
        *,
        count: int = 1,
        startVM: bool = True,
        wlName: str = 'windows',
        wlKind: str = 'vm',
        brEnv: dict[str, str] | None = None,
        brExecOH: int = 30,
        brExecVM: int = 5,
        vmStart: str = '15m',
        vmRemove: str = '5m',
        rootPathBR: str = '/synthetic/vendor/BenchmarkRunner',
        kCfg: str = '/synthetic/kubeconfig',
        ns: str = 'default',
        ocCfg: dict[str, object] | None = None,
        vcCfg: dict[str, object] | None = None,
        sshUsr: str = 'qa-usr',
        sshKey: str = '~/.ssh/openshift-qe.pem',
        testPars: dict[str, object] | None = None,
    ) -> CaseConfig:
        '''Build enumerated fields from scalars; no defaults merge is done.'''
        brConfig = SimpleNamespace(
            count=count,
            startVM=startVM,
            wlName=wlName,
            wlKind=wlKind,
            brEnv={} if (brEnv is None) else brEnv,
            timeout=TimeoutConfig(
                brExecOH=brExecOH,
                brExecVM=brExecVM,
                vmStart=vmStart,
                vmRemove=vmRemove,
            ),
        )
        return CaseConfig(
            self,
            kCfg=kCfg,
            cliCfg=SimpleNamespace(
                ocCfg={} if (ocCfg is None) else ocCfg,
                vcCfg={} if (vcCfg is None) else vcCfg,
            ),
            ns=ns,
            fxtInf=SimpleNamespace(
                rootPath=SimpleNamespace(br=rootPathBR),
                brCfg=brConfig,
                gstCfg=SimpleNamespace(
                    usrCrd=SimpleNamespace(sshUsr=sshUsr, sshKey=sshKey),
                ),
            ),
            testPars={} if (testPars is None) else testPars,
        )


def QueueMock(name: str) -> tuple[mock.Mock, deque[object]]:
    '''Create a strict mock whose only results are explicitly queued values.'''
    results: deque[object] = deque()

    def NextResult(*args: object, **kwargs: object) -> object:
        if not results:
            raise AssertionError(f'Unexpected unqueued call: {name}')
        result = results.popleft()
        if isinstance(result, BaseException): raise result

        return result

    return mock.Mock(side_effect=NextResult, name=name), results


def MakePathBinding() -> tuple[type[PurePosixPath], list[str], deque[bool]]:
    '''Make a per-test lexical path class with queued is_file observations.'''
    probes: list[str] = []
    fileResults: deque[bool] = deque()

    class CasePath(PurePosixPath):
        def is_file(self) -> bool:
            probes.append(str(self))
            if not fileResults:
                raise AssertionError('Unqueued lexical path probe')
            return fileResults.popleft()

    return CasePath, probes, fileResults


def MakeRunner() -> SimpleNamespace:
    '''Return a canned venv result; no virtual environment is opened.'''
    return SimpleNamespace(Call=mock.Mock(name='ModInVenv.Call'))


def PatchModule(
    monkeypatch: pytest.MonkeyPatch,
    *,
    configScript: ConfigScript,
    initialConfig: CaseConfig,
) -> SimpleNamespace:
    '''Patch only the module's imported bindings with local doubles.'''
    if (initialConfig._configScript is not configScript):
        raise AssertionError('Initial config must use this test config script')

    (localPath, pathProbes, fileResults) = MakePathBinding()
    localOS = SimpleNamespace(environ={})
    localTempfile = SimpleNamespace()
    localSys = SimpleNamespace()

    namedDictFactory = mock.Mock(side_effect=configScript.Factory)

    (tempFactory, tempResults) = QueueMock('tempfile.mkdtemp')
    (excInfoMock, excInfoResults) = QueueMock('sys.exc_info')
    (vmFactory, vmResults) = QueueMock('VM constructor')
    (cleanupVMs, cleanupResults) = QueueMock('VM.CleanUpVMs')
    (getResUID, uidResults) = QueueMock('OCcli.GetResUID')
    (ocFactory, ocClientResults) = QueueMock('OC client factory')
    (
        virtCtlFactory,
        virtCtlClientResults,
    ) = QueueMock('VirtCtl client factory')
    (uuidFactory, uuidResults) = QueueMock('uuid4')
    (runnerFactory, runnerResults) = QueueMock('ModInVenv')

    localTempfile.mkdtemp = tempFactory
    localSys.exc_info = excInfoMock
    vmFactory.CleanUpVMs = cleanupVMs
    localOCcli = SimpleNamespace(GetResUID=getResUID)
    localLogs = mock.Mock(name='BenchmarkRunner.logs')

    moduleBindings = {
        'Path': localPath,
        'os': localOS,
        'sys': localSys,
        'tempfile': localTempfile,
        'NamedDict': namedDictFactory,
        '_DEF__FXT_CFG': initialConfig,
        'VM': vmFactory,
        'OCcli': localOCcli,
        'ModInVenv': runnerFactory,
        'uuid4': uuidFactory,
        'logs': localLogs,
    }
    for (name, value) in moduleBindings.items():
        monkeypatch.setattr(MUT, name, value)

    return SimpleNamespace(
        configScript=configScript,
        initialConfig=initialConfig,
        pathClass=localPath,
        pathProbes=pathProbes,
        fileResults=fileResults,
        osProxy=localOS,
        tempfileProxy=localTempfile,
        sysProxy=localSys,
        namedDictFactory=namedDictFactory,
        namedDictResults=configScript.factoryResults,
        tempResults=tempResults,
        excInfoMock=excInfoMock,
        excInfoResults=excInfoResults,
        vmFactory=vmFactory,
        vmResults=vmResults,
        cleanupVMs=cleanupVMs,
        cleanupResults=cleanupResults,
        ocModule=localOCcli,
        getResUID=getResUID,
        uidResults=uidResults,
        ocFactory=ocFactory,
        ocClientResults=ocClientResults,
        virtCtlFactory=virtCtlFactory,
        virtCtlClientResults=virtCtlClientResults,
        uuidFactory=uuidFactory,
        uuidResults=uuidResults,
        runnerFactory=runnerFactory,
        runnerResults=runnerResults,
        logs=localLogs,
    )


@pytest.fixture(name='moduleBoundary')
def ModuleBoundary(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    '''Return the module-local test doubles and empty result queues.'''
    configScript = ConfigScript()
    initialConfig = configScript.Config()
    return PatchModule(
        monkeypatch,
        configScript=configScript,
        initialConfig=initialConfig,
    )


# Factory/operator calls retain their raw inputs in factoryCalls and
# mergeCalls; neither path performs or approximates recursive merge policy.


def QueueDirectConfig(
    moduleBoundary: SimpleNamespace,
    effectiveConfig: CaseConfig,
    baseConfig: CaseConfig | None = None,
) -> SimpleNamespace:
    '''Queue the canned result for one factory call and one config operator.'''
    factoryResult = SimpleNamespace(case='preconstructed-config-override')
    moduleBoundary.configScript.QueueFactoryResults(factoryResult)
    if (baseConfig is None): baseConfig = moduleBoundary.initialConfig

    moduleBoundary.configScript.expectedMerges.append(
        (baseConfig, factoryResult),
    )
    moduleBoundary.configScript.QueueMergedConfigs(effectiveConfig)
    return factoryResult


def MakeFuncPars(
    fxtCfgObj: CaseConfig,
    vmList: list[object],
    ownVMs: list[object],
) -> MUT.FuncParsVMcreateByBR:
    '''Supply mutable lists explicitly and keep them independent.'''
    return MUT.FuncParsVMcreateByBR(
        fxtCfgObj=fxtCfgObj, vmList=vmList,
        ownResTrk=SimpleNamespace(VMs=ownVMs),
    )


def ConfigSnapshot(config: CaseConfig) -> tuple[object, ...]:
    '''Capture enumerated values without serialization or a merge operation.'''
    return (
        config.kCfg,
        config.ns,
        dict(config.cliCfg.ocCfg),
        dict(config.cliCfg.vcCfg),
        config.fxtInf.rootPath.br,
        config.fxtInf.brCfg.count,
        config.fxtInf.brCfg.startVM,
        config.fxtInf.brCfg.wlName,
        config.fxtInf.brCfg.wlKind,
        dict(config.fxtInf.brCfg.brEnv),
        config.fxtInf.brCfg.timeout.brExecOH,
        config.fxtInf.brCfg.timeout.brExecVM,
        config.fxtInf.brCfg.timeout.vmStart,
        config.fxtInf.brCfg.timeout.vmRemove,
        config.fxtInf.gstCfg.usrCrd.sshUsr,
        config.fxtInf.gstCfg.usrCrd.sshKey,
        dict(config.testPars),
    )


def test__DEF__FXT_CFG_UsesNestedWorkloadConfiguration(
    moduleBoundary: SimpleNamespace,
) -> None:
    config = SOURCE_DEFAULT_CONFIG
    brConfig = config.fxtInf.brCfg
    timeout = brConfig.timeout
    userConfig = config.fxtInf.gstCfg.usrCrd

    assert (brConfig.count == 1)
    assert (brConfig.startVM is True)
    assert (brConfig.wlName == 'windows')
    assert (brConfig.wlKind == 'vm')
    assert (brConfig.brEnv == {})
    assert (timeout.brExecOH == 30)
    assert (timeout.brExecVM == 5)
    assert (timeout.vmStart == '15m')
    assert (timeout.vmRemove == '5m')
    assert (config.cliCfg.ocCfg == {})
    assert (config.cliCfg.vcCfg == {})
    assert (userConfig.sshUsr == 'qa-usr')
    assert (userConfig.sshKey == '~/.ssh/openshift-qe.pem')
    assert (config.testPars == {})
    assert ('wlName' not in config)
    assert ('wlKind' not in config)

    # NamedDict merge policy belongs to libs.CustomTypes, not this module.


def test_FuncParsVMcreateByBR_RequiresConfigAndKeepsMutableLists(
    moduleBoundary: SimpleNamespace,
) -> None:
    with pytest.raises(TypeError, match='fxtCfgObj'):
        MUT.FuncParsVMcreateByBR()

    config = moduleBoundary.configScript.Config(count=0)
    firstOwnership = SimpleNamespace(VMs=[])
    secondOwnership = SimpleNamespace(VMs=[])
    moduleBoundary.namedDictResults.extend([firstOwnership, secondOwnership])
    first = MUT.FuncParsVMcreateByBR(fxtCfgObj=config)
    second = MUT.FuncParsVMcreateByBR(fxtCfgObj=config)

    assert (first.fxtCfgObj is config)
    assert (first.vmList is not second.vmList)
    assert (first.ownResTrk is firstOwnership)
    assert (second.ownResTrk is secondOwnership)
    assert (first.ownResTrk.VMs is not second.ownResTrk.VMs)
    assert (first.vmList is not first.ownResTrk.VMs)
    assert (
        moduleBoundary.namedDictFactory.call_args_list
        == [mock.call(VMs=[]), mock.call(VMs=[])]
    )

    ownBR = MUT.FuncParsVMcreateByBR.__annotations__['ownResTrk']
    namedDictType = get_origin(ownBR)
    assert (namedDictType.__name__ == 'NamedDict')
    (keyType, valueType) = get_args(ownBR)
    assert (keyType is str)
    assert (get_origin(valueType) is list)
    (recordType,) = get_args(valueType)
    assert (get_origin(recordType) is tuple)
    (clientType, refType, uidType) = get_args(recordType)
    assert (clientType.__name__ == 'OCcli')
    assert (clientType.__module__ == 'libs.OpenShift.OCP.CLI.OC')
    assert ((refType, uidType) == (str, str))

    for field in ('fxtCfgObj', 'vmList', 'ownResTrk'):
        with pytest.raises(FrozenInstanceError):
            setattr(first, field, None)

    vm = object()
    ownership = (object(), 'vm-name', 'uid')
    first.vmList.append(vm)
    first.ownResTrk.VMs.append(ownership)

    assert (first.vmList == [vm])
    assert (first.ownResTrk.VMs == [ownership])
    assert (second.vmList == [])
    assert (second.ownResTrk.VMs == [])


@pytest.mark.parametrize('count,scaleNodes,workerNode', [
    (2, None, 'configured-worker'),
    (2, '', 'configured-worker'),
    (2, '[\'worker-a\']', None),
    (2, 'raw-nodes', 'configured-worker'),
    (2, ' \t', 'configured-worker'),
    (0, None, None),
    (0, '', None),
    (1, None, None),
    (1, '', None),
])
def test_VMcreateByBR_DirectScaleNodes_ReviewRegression(
    moduleBoundary: SimpleNamespace,
    count: int,
    scaleNodes: str | None,
    workerNode: str | None,
) -> None:
    localEnvironment = moduleBoundary.osProxy.environ
    localEnvironment.update({
        'RUN_ARTIFACTS_PATH': '/synthetic/artifacts',
        'WORKER_NODE': 'ambient-worker',
    })
    if (scaleNodes is not None): localEnvironment['SCALE_NODES'] = scaleNodes


    brEnvironment = {'SCALE_NODES': 'configured-nodes'}
    if (workerNode is not None): brEnvironment['WORKER_NODE'] = workerNode

    rawBrConfig = {'count': count, 'startVM': False, 'brEnv': brEnvironment}
    rawOverrides = {'fxtInf': {'brCfg': rawBrConfig}}
    effectiveConfig = moduleBoundary.configScript.Config(
        count=count, startVM=False,
        brEnv=brEnvironment,
    )
    QueueDirectConfig(moduleBoundary, effectiveConfig)
    vmList: list[object] = []
    ownVMs: list[object] = []
    fPars = MakeFuncPars(moduleBoundary.initialConfig, vmList, ownVMs)
    runner = MakeRunner()

    if (count > 0): moduleBoundary.uuidResults.append('scale-rest')

    if ((count > 0) and not ((count > 1) and (scaleNodes in (None, '')))):
        ocClients = [SimpleNamespace() for _ in range(count)]
        virtClients = [
            SimpleNamespace(name=f'virt-{index}') for index in range(count)
        ]
        vmObjects = [SimpleNamespace() for _ in range(count)]
        uidResults = (
            [(True, '', '') for _ in range(count)] +
            [(True, f'uid-{index}', '') for index in range(count)]
        )
        moduleBoundary.ocClientResults.extend(ocClients)
        moduleBoundary.virtCtlClientResults.extend(virtClients)
        moduleBoundary.vmResults.extend(vmObjects)
        moduleBoundary.uidResults.extend(uidResults)
        moduleBoundary.runnerResults.append(runner)
        moduleBoundary.fileResults.append(False)
        moduleBoundary.excInfoResults.append((None, None, None))

    if ((count > 1) and (scaleNodes in (None, ''))):
        with pytest.raises(ValueError) as caught:
            MUT.VMcreateByBR(
                SimpleNamespace(),
                fPars,
                ocCLI=moduleBoundary.ocFactory,
                virtCtlCLI=moduleBoundary.virtCtlFactory,
                fxtInf={'brCfg': rawBrConfig},
            )
        assert (str(caught.value) == (
            'Env. Var. `SCALE_NODES` must set to non-empty string for '
            'scale.'
        ))
        runner.Call.assert_not_called()
        moduleBoundary.runnerFactory.assert_not_called()
        moduleBoundary.ocFactory.assert_not_called()
        return

    created = MUT.VMcreateByBR(
        SimpleNamespace(),
        fPars,
        ocCLI=moduleBoundary.ocFactory,
        virtCtlCLI=moduleBoundary.virtCtlFactory,
        fxtInf={'brCfg': rawBrConfig},
    )

    assert (len(created) == count)
    assert (vmList == created)
    assert (len(ownVMs) == count)
    if not count:
        runner.Call.assert_not_called()
        moduleBoundary.runnerFactory.assert_not_called()
        moduleBoundary.ocFactory.assert_not_called()
        moduleBoundary.virtCtlFactory.assert_not_called()
        moduleBoundary.getResUID.assert_not_called()
        moduleBoundary.uuidFactory.assert_not_called()
        moduleBoundary.tempfileProxy.mkdtemp.assert_not_called()
        assert (vmList == [])
        assert (ownVMs == [])
        return

    environment = runner.Call.call_args.kwargs['envUpd']
    expectedScaleNodes = scaleNodes if (count > 1) else ''
    assert (environment['SCALE_NODES'] == expectedScaleNodes)
    assert (environment['SCALE'] == (str(count) if (count > 1) else ''))

    if (workerNode is None): assert ('WORKER_NODE' not in environment)
    else: assert (environment['WORKER_NODE'] == workerNode)
    assert (moduleBoundary.configScript.factoryCalls == [
        ((rawOverrides,), {}),
    ])
    assert (brEnvironment == {
        'SCALE_NODES': 'configured-nodes',
        **({'WORKER_NODE': workerNode} if (workerNode is not None) else {}),
    })


# The import-hook guard from the original single-VM test is intentionally not
# installed on builtins: the SUT's runner is a queued local double, and the
# exact in-venv target is asserted on its Call mock instead.
def test_VMcreateByBR_SingleVMhappyPathUsesGeneratedIdentityKey(
    moduleBoundary: SimpleNamespace,
) -> None:
    localEnvironment = moduleBoundary.osProxy.environ
    localEnvironment['RUN_ARTIFACTS_PATH'] = '/artifacts'
    rootPath = moduleBoundary.pathClass('/custom/br-venv')
    rawCliConfig = {'ocCfg': {'context': 'oc-context'}}
    rawFxtInf = {
        'rootPath': {'br': rootPath},
        'brCfg': {'wlName': 'windows', 'wlKind': 'vm'},
        'gstCfg': {'usrCrd': {'sshUsr': 'unit-user'}},
    }
    rawOverrides = {'cliCfg': rawCliConfig, 'fxtInf': rawFxtInf}
    effectiveConfig = moduleBoundary.configScript.Config(
        wlName='windows',
        wlKind='vm',
        rootPathBR=rootPath,
        kCfg=moduleBoundary.initialConfig.kCfg,
        ns='default',
        ocCfg={'context': 'oc-context'},
        sshUsr='unit-user',
    )
    QueueDirectConfig(moduleBoundary, effectiveConfig)

    oc = SimpleNamespace(name='oc-single')
    virtCtl = SimpleNamespace(name='virtctl-single')
    vm = mock.Mock(name='vm-single')
    runner = MakeRunner()
    moduleBoundary.ocClientResults.append(oc)
    moduleBoundary.virtCtlClientResults.append(virtCtl)
    moduleBoundary.vmResults.append(vm)

    moduleBoundary.uidResults.extend([(True, '', ''), (True, 'uid-one', '')])
    moduleBoundary.uuidResults.append('12345678-abcd-0000-0000-000000000000')
    moduleBoundary.runnerResults.append(runner)
    moduleBoundary.fileResults.append(True)
    moduleBoundary.excInfoResults.append((None, None, None))
    vmList: list[object] = []
    ownVMs: list[object] = []
    fPars = MakeFuncPars(moduleBoundary.initialConfig, vmList, ownVMs)

    created = MUT.VMcreateByBR(
        SimpleNamespace(),
        fPars,
        ocCLI=moduleBoundary.ocFactory,
        virtCtlCLI=moduleBoundary.virtCtlFactory,
        **rawOverrides,
    )

    assert (created == [vm])
    assert (vmList == created)
    assert (ownVMs == [(oc, 'VirtualMachine/windows-vm-12345678', 'uid-one')])
    environment = runner.Call.call_args.kwargs['envUpd']
    assert (environment['WORKLOAD'] == 'windows_vm')
    assert (environment['RUN_TYPE'] == 'test_ci')
    assert (environment['NAMESPACE'] == 'default')
    assert (environment['DELETE_ALL'] == 'no')
    assert (environment['SAVE_ARTIFACTS_LOCAL'] == 'true')
    assert (environment['SCALE'] == '')
    assert (environment['SCALE_NODES'] == '')
    assert (environment['UUID'] == '12345678-abcd-0000-0000-000000000000')
    assert (environment['KUBECONFIG'] == moduleBoundary.initialConfig.kCfg)
    assert (environment['RUN_ARTIFACTS_PATH'] == '/artifacts')
    assert (moduleBoundary.ocFactory.call_args_list == [
        mock.call(
            ns='default',
            context='oc-context',
            kubeconfig=moduleBoundary.initialConfig.kCfg,
        ),
    ])
    assert (moduleBoundary.virtCtlFactory.call_args_list == [
        mock.call(
            ns='default',
            kubeconfig=moduleBoundary.initialConfig.kCfg,
        ),
    ])
    moduleBoundary.vmFactory.assert_called_once_with(
        name='windows-vm-12345678',
        ns='default',
        oc=oc,
        virtCtl=virtCtl,
        sshUsr='unit-user',
        sshKey='/artifacts/ssh/vm_key',
    )
    moduleBoundary.runnerFactory.assert_called_once_with(rootPath, execTO=2100)
    runner.Call.assert_called_once_with(
        'benchmark_runner.main.main',
        'main',
        envUpd=environment,
    )
    assert (moduleBoundary.pathProbes == ['/artifacts/ssh/vm_key'])
    assert (moduleBoundary.configScript.factoryCalls == [
        ((rawOverrides,), {}),
    ])


def test_VMcreateByBR_ScalePathNamesVMsAndUsesFallbackKey(
    moduleBoundary: SimpleNamespace,
) -> None:
    localEnvironment = moduleBoundary.osProxy.environ
    localEnvironment.update({
        'WORKER_NODE': 'worker-a',
        'SCALE_NODES': '[\'worker-a\']',
        'RUN_ARTIFACTS_PATH': '',
    })
    rawBrConfig = {'wlName': 'windows_scale', 'count': 2}
    rawOverrides = {'fxtInf': {'brCfg': rawBrConfig}}
    effectiveConfig = moduleBoundary.configScript.Config(
        count=2, wlName='windows_scale',
    )
    QueueDirectConfig(moduleBoundary, effectiveConfig)

    ocClients = [
        SimpleNamespace(name='oc-zero'),
        SimpleNamespace(name='oc-one'),
    ]
    virtClients = [
        SimpleNamespace(name='virt-zero'),
        SimpleNamespace(name='virt-one'),
    ]
    vmObjects = [mock.Mock(name='vm-zero'), mock.Mock(name='vm-one')]
    for vm in vmObjects:
        vm.Start.return_value = True
    runner = MakeRunner()
    moduleBoundary.ocClientResults.extend(ocClients)
    moduleBoundary.virtCtlClientResults.extend(virtClients)
    moduleBoundary.vmResults.extend(vmObjects)
    moduleBoundary.uidResults.extend([
        (True, '', ''),
        (True, '', ''),
        (True, 'uid-0', ''),
        (True, 'uid-1', ''),
    ])
    moduleBoundary.uuidResults.append('deadbeef-rest')
    moduleBoundary.tempResults.append('/tmp/run')
    moduleBoundary.runnerResults.append(runner)
    moduleBoundary.fileResults.append(False)
    moduleBoundary.excInfoResults.append((None, None, None))
    vmList: list[object] = []
    ownVMs: list[object] = []
    fPars = MakeFuncPars(moduleBoundary.initialConfig, vmList, ownVMs)

    created = MUT.VMcreateByBR(
        SimpleNamespace(),
        fPars,
        ocCLI=moduleBoundary.ocFactory,
        virtCtlCLI=moduleBoundary.virtCtlFactory,
        **rawOverrides,
    )

    assert (len(created) == 2)
    assert (vmList == created)
    assert (ownVMs == [
        (ocClients[0], 'VirtualMachine/windows-scale-vm-deadbeef-0', 'uid-0'),
        (ocClients[1], 'VirtualMachine/windows-scale-vm-deadbeef-1', 'uid-1'),
    ])
    environment = runner.Call.call_args.kwargs['envUpd']
    assert (environment['SCALE'] == '2')
    assert (environment['SCALE_NODES'] == '[\'worker-a\']')
    assert (environment['RUN_ARTIFACTS_PATH'] == '/tmp/run')
    moduleBoundary.tempfileProxy.mkdtemp.assert_called_once_with()
    assert ([
        call.kwargs['name']
        for call in moduleBoundary.vmFactory.call_args_list
    ] == [
        'windows-scale-vm-deadbeef-0',
        'windows-scale-vm-deadbeef-1',
    ])
    assert (all(
        (call.kwargs['sshKey'] == '~/.ssh/openshift-qe.pem')
        for call in moduleBoundary.vmFactory.call_args_list
    ))
    assert (moduleBoundary.configScript.factoryCalls == [
        ((rawOverrides,), {}),
    ])


def test_VMcreateByBR_NestedOverridesDoNotMutateDefaults(
    moduleBoundary: SimpleNamespace,
) -> None:
    configDefaults = ConfigSnapshot(moduleBoundary.initialConfig)
    rawCustomOverrides = {
        'cliCfg': {
            'ocCfg': {'context': 'custom-context'},
            'vcCfg': {'kubeconfig': '/custom-config'},
        },
        'fxtInf': {'gstCfg': {'usrCrd': {'sshUsr': 'custom-user'}}},
    }
    rawCustomBefore = JSONText(rawCustomOverrides)
    rawDefaultOverrides: dict[str, object] = {}
    customConfig = moduleBoundary.configScript.Config(
        ocCfg={'context': 'custom-context'},
        vcCfg={'kubeconfig': '/custom-config'},
        sshUsr='custom-user',
    )
    defaultConfig = moduleBoundary.configScript.Config()
    QueueDirectConfig(moduleBoundary, customConfig)
    QueueDirectConfig(moduleBoundary, defaultConfig)

    ocCustom = SimpleNamespace(name='oc-custom')
    ocDefault = SimpleNamespace(name='oc-default')
    virtCustom = SimpleNamespace(name='virt-custom')
    virtDefault = SimpleNamespace(name='virt-default')
    vmCustom = mock.Mock(name='vm-custom')
    vmDefault = mock.Mock(name='vm-default')
    vmCustom.Start.return_value = True
    vmDefault.Start.return_value = True
    runner = MakeRunner()
    moduleBoundary.ocClientResults.extend([ocCustom, ocDefault])
    moduleBoundary.virtCtlClientResults.extend([virtCustom, virtDefault])
    moduleBoundary.vmResults.extend([vmCustom, vmDefault])
    moduleBoundary.uidResults.extend([
        (True, '', ''),
        (True, 'custom-uid', ''),
        (True, '', ''),
        (True, 'default-uid', ''),
    ])
    moduleBoundary.uuidResults.extend(['custom-rest', 'default-rest'])
    moduleBoundary.tempResults.extend([
        '/synthetic/custom-run',
        '/synthetic/default-run',
    ])
    moduleBoundary.runnerResults.extend([runner, runner])
    moduleBoundary.fileResults.extend([False, False])
    moduleBoundary.excInfoResults.extend([
        (None, None, None),
        (None, None, None),
    ])
    customPars = MakeFuncPars(moduleBoundary.initialConfig, [], [])
    defaultPars = MakeFuncPars(moduleBoundary.initialConfig, [], [])

    MUT.VMcreateByBR(
        SimpleNamespace(),
        customPars,
        ocCLI=moduleBoundary.ocFactory,
        virtCtlCLI=moduleBoundary.virtCtlFactory,
        **rawCustomOverrides,
    )
    MUT.VMcreateByBR(
        SimpleNamespace(),
        defaultPars,
        ocCLI=moduleBoundary.ocFactory,
        virtCtlCLI=moduleBoundary.virtCtlFactory,
        **rawDefaultOverrides,
    )

    assert (ConfigSnapshot(moduleBoundary.initialConfig) == configDefaults)
    assert (JSONText(rawCustomOverrides) == rawCustomBefore)
    assert (moduleBoundary.configScript.factoryCalls == [
        ((rawCustomOverrides,), {}),
        ((rawDefaultOverrides,), {}),
    ])
    assert (moduleBoundary.ocFactory.call_args_list == [
        mock.call(
            ns='default',
            context='custom-context',
            kubeconfig=moduleBoundary.initialConfig.kCfg,
        ),
        mock.call(
            ns='default',
            kubeconfig=moduleBoundary.initialConfig.kCfg,
        ),
    ])
    assert (moduleBoundary.virtCtlFactory.call_args_list == [
        mock.call(
            ns='default',
            kubeconfig=moduleBoundary.initialConfig.kCfg,
        ),
        mock.call(
            ns='default',
            kubeconfig=moduleBoundary.initialConfig.kCfg,
        ),
    ])
    assert (moduleBoundary.vmFactory.call_args_list[0].kwargs['sshUsr'] == (
        'custom-user'
    ))
    assert (moduleBoundary.vmFactory.call_args_list[0].kwargs['sshKey'] == (
        '~/.ssh/openshift-qe.pem'
    ))
    assert (moduleBoundary.vmFactory.call_args_list[1].kwargs['sshUsr'] == (
        'qa-usr'
    ))
    runArtifactPaths = [
        call.kwargs['envUpd']['RUN_ARTIFACTS_PATH']
        for call in runner.Call.call_args_list
    ]
    assert (runArtifactPaths == [
        '/synthetic/custom-run',
        '/synthetic/default-run',
    ])
    assert (
        moduleBoundary.tempfileProxy.mkdtemp.call_args_list ==
        [mock.call(), mock.call()]
    )
    assert (moduleBoundary.vmFactory.call_args_list[1].kwargs['sshKey'] == (
        '~/.ssh/openshift-qe.pem'
    ))


def test_VMcreateByBR_NegativeCountIsRejectedBeforeImportingRunner(
    moduleBoundary: SimpleNamespace,
) -> None:
    rawBrConfig = {'count': -1}
    rawOverrides = {'fxtInf': {'brCfg': rawBrConfig}}
    effectiveConfig = moduleBoundary.configScript.Config(count=-1)
    QueueDirectConfig(moduleBoundary, effectiveConfig)
    vmList: list[object] = []
    ownVMs: list[object] = []
    fPars = MakeFuncPars(moduleBoundary.initialConfig, vmList, ownVMs)

    with pytest.raises(ValueError, match='must not be negative'):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            fPars,
            ocCLI=moduleBoundary.ocFactory,
            virtCtlCLI=moduleBoundary.virtCtlFactory,
            **rawOverrides,
        )

    assert (vmList == [])
    assert (ownVMs == [])
    moduleBoundary.ocFactory.assert_not_called()
    moduleBoundary.virtCtlFactory.assert_not_called()
    moduleBoundary.getResUID.assert_not_called()
    moduleBoundary.uuidFactory.assert_not_called()
    moduleBoundary.tempfileProxy.mkdtemp.assert_not_called()
    moduleBoundary.vmFactory.assert_not_called()
    moduleBoundary.runnerFactory.assert_not_called()
    assert (moduleBoundary.configScript.factoryCalls == [
        ((rawOverrides,), {}),
    ])


def test_VMcreateByBR_ZeroCountReturnsEmptyWithoutRunner(
    moduleBoundary: SimpleNamespace,
) -> None:
    rawBrConfig = {'count': 0}
    rawOverrides = {'fxtInf': {'brCfg': rawBrConfig}}
    effectiveConfig = moduleBoundary.configScript.Config(count=0)
    QueueDirectConfig(moduleBoundary, effectiveConfig)
    vmList: list[object] = []
    ownVMs: list[object] = []
    fPars = MakeFuncPars(moduleBoundary.initialConfig, vmList, ownVMs)

    result = MUT.VMcreateByBR(
        SimpleNamespace(),
        fPars,
        ocCLI=moduleBoundary.ocFactory,
        virtCtlCLI=moduleBoundary.virtCtlFactory,
        **rawOverrides,
    )

    assert (result == [])
    assert (vmList == [])
    assert (ownVMs == [])
    moduleBoundary.ocFactory.assert_not_called()
    moduleBoundary.virtCtlFactory.assert_not_called()
    moduleBoundary.getResUID.assert_not_called()
    moduleBoundary.uuidFactory.assert_not_called()
    moduleBoundary.tempfileProxy.mkdtemp.assert_not_called()
    moduleBoundary.runnerFactory.assert_not_called()


def test_VMcreateByBR_PreflightLookupFailureRaisesWithoutTracking(
    moduleBoundary: SimpleNamespace,
) -> None:
    moduleBoundary.osProxy.environ['RUN_ARTIFACTS_PATH'] = '/tmp'
    moduleBoundary.uuidResults.append('abcd-rest')
    oc = SimpleNamespace(name='oc-preflight-failure')
    moduleBoundary.ocClientResults.append(oc)
    moduleBoundary.uidResults.append((False, '', 'lookup error'))
    effectiveConfig = moduleBoundary.configScript.Config()
    rawOverrides: dict[str, object] = {}
    QueueDirectConfig(moduleBoundary, effectiveConfig)
    vmList: list[object] = []
    ownVMs: list[object] = []
    fPars = MakeFuncPars(moduleBoundary.initialConfig, vmList, ownVMs)

    with pytest.raises(RuntimeError, match='lookup error'):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            fPars,
            ocCLI=moduleBoundary.ocFactory,
            virtCtlCLI=moduleBoundary.virtCtlFactory,
            **rawOverrides,
        )

    assert (vmList == [])
    assert (ownVMs == [])
    moduleBoundary.runnerFactory.assert_not_called()
    moduleBoundary.vmFactory.assert_not_called()
    moduleBoundary.virtCtlFactory.assert_not_called()
    assert (moduleBoundary.configScript.factoryCalls == [
        ((rawOverrides,), {}),
    ])


def test_VMcreateByBR_PreflightExistingVMIsRefused(
    moduleBoundary: SimpleNamespace,
) -> None:
    moduleBoundary.osProxy.environ['RUN_ARTIFACTS_PATH'] = '/tmp'
    moduleBoundary.uuidResults.append('abcd-rest')

    moduleBoundary.ocClientResults.append(SimpleNamespace(name='oc-existing'))
    moduleBoundary.uidResults.append((True, 'existing-uid', ''))
    rawOverrides: dict[str, object] = {}
    QueueDirectConfig(moduleBoundary, moduleBoundary.configScript.Config())
    fPars = MakeFuncPars(moduleBoundary.initialConfig, [], [])

    with pytest.raises(RuntimeError, match='already exists'):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            fPars,
            ocCLI=moduleBoundary.ocFactory,
            virtCtlCLI=moduleBoundary.virtCtlFactory,
            **rawOverrides,
        )

    moduleBoundary.runnerFactory.assert_not_called()
    moduleBoundary.vmFactory.assert_not_called()


def test_VMcreateByBR_RunnerFailurePreservesPrimaryErrorDuringReconciliation(
    moduleBoundary: SimpleNamespace,
) -> None:
    moduleBoundary.osProxy.environ.update({
        'SCALE_NODES': '[\'synthetic-worker\']',
        'RUN_ARTIFACTS_PATH': '/tmp',
    })
    rawBrConfig = {'count': 3}
    rawOverrides = {'fxtInf': {'brCfg': rawBrConfig}}
    effectiveConfig = moduleBoundary.configScript.Config(count=3)
    QueueDirectConfig(moduleBoundary, effectiveConfig)
    moduleBoundary.uuidResults.append('abcd-rest')
    moduleBoundary.ocClientResults.extend([
        SimpleNamespace(name='oc-zero'),
        SimpleNamespace(name='oc-one'),
        SimpleNamespace(name='oc-two'),
    ])
    moduleBoundary.uidResults.extend([
        (True, '', ''),
        (True, '', ''),
        (True, '', ''),
        (False, '', 'lookup failed'),
        (True, '', ''),
        RuntimeError('lookup exploded'),
    ])
    runner = MakeRunner()
    primary = ValueError('runner failed')
    runner.Call.side_effect = primary
    moduleBoundary.runnerResults.append(runner)
    moduleBoundary.excInfoResults.append((ValueError, primary, None))
    fPars = MakeFuncPars(moduleBoundary.initialConfig, [], [])

    with pytest.raises(ValueError) as raised:
        MUT.VMcreateByBR(
            SimpleNamespace(),
            fPars,
            ocCLI=moduleBoundary.ocFactory,
            virtCtlCLI=moduleBoundary.virtCtlFactory,
            **rawOverrides,
        )

    assert (raised.value is primary)
    runner.Call.assert_called_once_with(
        'benchmark_runner.main.main',
        'main',
        envUpd=runner.Call.call_args.kwargs['envUpd'],
    )
    assert ('Preserving benchmark-runner failure' in (
        moduleBoundary.logs.error.call_args_list[0].args[0]
    ))
    assert ('Preserving benchmark-runner failure' in (
        moduleBoundary.logs.error.call_args_list[1].args[0]
    ))
    assert (fPars.vmList == [])
    assert (fPars.ownResTrk.VMs == [])


def test_VMcreateByBR_ReconciliationLookupFailureRaisesWithoutRunnerError(
    moduleBoundary: SimpleNamespace,
) -> None:
    moduleBoundary.osProxy.environ['RUN_ARTIFACTS_PATH'] = '/tmp'
    moduleBoundary.uuidResults.append('abcd-rest')
    moduleBoundary.ocClientResults.append(
        SimpleNamespace(name='oc-reconcile-failure'),
    )
    moduleBoundary.uidResults.extend([
        (True, '', ''),
        (False, '', 'lookup failed'),
    ])
    runner = MakeRunner()
    moduleBoundary.runnerResults.append(runner)
    moduleBoundary.excInfoResults.append((None, None, None))
    QueueDirectConfig(moduleBoundary, moduleBoundary.configScript.Config())
    fPars = MakeFuncPars(moduleBoundary.initialConfig, [], [])

    with pytest.raises(RuntimeError, match='reconcile'):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            fPars,
            ocCLI=moduleBoundary.ocFactory,
            virtCtlCLI=moduleBoundary.virtCtlFactory,
        )

    runner.Call.assert_called_once()
    assert (fPars.vmList == [])
    assert (fPars.ownResTrk.VMs == [])


def test_VMcreateByBR_ReconciliationMissingVMRaisesWithoutRunnerError(
    moduleBoundary: SimpleNamespace,
) -> None:
    moduleBoundary.osProxy.environ['RUN_ARTIFACTS_PATH'] = '/tmp'
    moduleBoundary.uuidResults.append('abcd-rest')

    moduleBoundary.ocClientResults.append(SimpleNamespace(name='oc-missing'))
    moduleBoundary.uidResults.extend([(True, '', ''), (True, '', '')])
    runner = MakeRunner()
    moduleBoundary.runnerResults.append(runner)
    moduleBoundary.excInfoResults.append((None, None, None))
    QueueDirectConfig(moduleBoundary, moduleBoundary.configScript.Config())
    fPars = MakeFuncPars(moduleBoundary.initialConfig, [], [])

    with pytest.raises(RuntimeError, match='did not create expected'):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            fPars,
            ocCLI=moduleBoundary.ocFactory,
            virtCtlCLI=moduleBoundary.virtCtlFactory,
        )

    runner.Call.assert_called_once()
    assert (fPars.vmList == [])
    assert (fPars.ownResTrk.VMs == [])


@pytest.mark.parametrize(('count', 'overhead', 'perVM'), [
    (0, 30, 5),
    (1, 30, 5),
    (4, 30, 5),
    (2, 1, 2),
    (0, 0, 0),
])
def test_VMcreateByBR_ScalesExecutionBudgetAndConvertsMinutes(
    moduleBoundary: SimpleNamespace,
    count: int,
    overhead: int,
    perVM: int,
) -> None:
    moduleBoundary.osProxy.environ.update({
        'SCALE_NODES': '[\'synthetic-worker\']',
        'RUN_ARTIFACTS_PATH': '/tmp',
    })
    rawBrConfig = {
        'count': count,
        'timeout': {'brExecOH': overhead, 'brExecVM': perVM},
    }
    rawOverrides = {'fxtInf': {'brCfg': rawBrConfig}}
    effectiveConfig = moduleBoundary.configScript.Config(
        count=count,
        brExecOH=overhead,
        brExecVM=perVM,
    )
    QueueDirectConfig(moduleBoundary, effectiveConfig)
    defaults = ConfigSnapshot(moduleBoundary.initialConfig)

    if count:
        moduleBoundary.uuidResults.append('budget-rest')
        moduleBoundary.ocClientResults.extend([
            SimpleNamespace(name=f'oc-{index}') for index in range(count)
        ])
        moduleBoundary.uidResults.extend(
            [(True, '', '') for _ in range((2 * count))],
        )
        runner = MakeRunner()
        primary = TimeoutError('stop after recording runner budget')
        runner.Call.side_effect = primary
        moduleBoundary.runnerResults.append(runner)
        moduleBoundary.excInfoResults.append((TimeoutError, primary, None))

    else: runner = MakeRunner()

    fPars = MakeFuncPars(moduleBoundary.initialConfig, [], [])
    if count:
        with pytest.raises(TimeoutError) as raised:
            MUT.VMcreateByBR(
                SimpleNamespace(),
                fPars,
                ocCLI=moduleBoundary.ocFactory,
                virtCtlCLI=moduleBoundary.virtCtlFactory,
                **rawOverrides,
            )
        assert (raised.value is primary)
        runnerPath = moduleBoundary.initialConfig.fxtInf.rootPath.br
        expectedBudget = 60 * (overhead + (count * perVM))
        moduleBoundary.runnerFactory.assert_called_once_with(
            runnerPath,
            execTO=expectedBudget,
        )
        assert (type(
            moduleBoundary.runnerFactory.call_args.kwargs['execTO']
        ) is int)
        runner.Call.assert_called_once()
    else:
        assert (MUT.VMcreateByBR(
            SimpleNamespace(),
            fPars,
            ocCLI=moduleBoundary.ocFactory,
            virtCtlCLI=moduleBoundary.virtCtlFactory,
            **rawOverrides,
        ) == [])
        moduleBoundary.runnerFactory.assert_not_called()
        moduleBoundary.ocFactory.assert_not_called()
    assert (ConfigSnapshot(moduleBoundary.initialConfig) == defaults)


@pytest.mark.parametrize('field', ['brExecOH', 'brExecVM'])
@pytest.mark.parametrize('value', [
    0,
    -1,
    True,
    False,
    1.5,
    0.25,
    '5',
    None,
    float('nan'),
    float('inf'),
    float('-inf'),
])
def test_VMcreateByBR_RejectsInvalidExecutionAllowanceBeforeOperations(
    moduleBoundary: SimpleNamespace,
    field: str,
    value: object,
) -> None:
    moduleBoundary.osProxy.environ['RUN_ARTIFACTS_PATH'] = '/tmp'
    rawBrConfig = {'timeout': {field: value}}
    rawOverrides = {'fxtInf': {'brCfg': rawBrConfig}}
    brExecOH = value if (field == 'brExecOH') else 30
    brExecVM = value if (field == 'brExecVM') else 5
    effectiveConfig = moduleBoundary.configScript.Config(
        brExecOH=brExecOH,
        brExecVM=brExecVM,
    )
    QueueDirectConfig(moduleBoundary, effectiveConfig)
    fPars = MakeFuncPars(moduleBoundary.initialConfig, [], [])

    with pytest.raises(
        ValueError,
        match=f'The `{field}` must be a positive integer',
    ):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            fPars,
            ocCLI=moduleBoundary.ocFactory,
            virtCtlCLI=moduleBoundary.virtCtlFactory,
            **rawOverrides,
        )

    moduleBoundary.runnerFactory.assert_not_called()
    moduleBoundary.ocFactory.assert_not_called()
    moduleBoundary.getResUID.assert_not_called()
    moduleBoundary.uuidFactory.assert_not_called()
    moduleBoundary.tempfileProxy.mkdtemp.assert_not_called()
    moduleBoundary.vmFactory.assert_not_called()


def test_VMcreateByBR_RejectsZeroTotalExecutionBudget(
    moduleBoundary: SimpleNamespace,
) -> None:
    rawBrConfig = {'timeout': {'brExecOH': 0, 'brExecVM': 0}}
    rawOverrides = {'fxtInf': {'brCfg': rawBrConfig}}
    effectiveConfig = moduleBoundary.configScript.Config(
        brExecOH=0,
        brExecVM=0,
    )
    QueueDirectConfig(moduleBoundary, effectiveConfig)
    fPars = MakeFuncPars(moduleBoundary.initialConfig, [], [])

    with pytest.raises(
        ValueError,
        match='The `brExecOH` must be a positive integer',
    ):
        MUT.VMcreateByBR(
            SimpleNamespace(),
            fPars,
            ocCLI=moduleBoundary.ocFactory,
            virtCtlCLI=moduleBoundary.virtCtlFactory,
            **rawOverrides,
        )

    moduleBoundary.runnerFactory.assert_not_called()
    moduleBoundary.ocFactory.assert_not_called()
    moduleBoundary.getResUID.assert_not_called()
    moduleBoundary.uuidFactory.assert_not_called()
    moduleBoundary.vmFactory.assert_not_called()


def BindVMcreateByBR(
    target: Callable[..., object],
    request: object,
    fPars: MUT.FuncParsVMcreateByBR,
    *,
    ocCLI: Callable[..., object],
    virtCtlCLI: Callable[..., object],
) -> Callable[..., object]:
    '''Bind only the fixture's fixed VMcreateByBR arguments.'''
    boundOC = ocCLI
    boundVirtCtl = virtCtlCLI

    def Invoke(
        *args: object,
        ocCLI: Callable[..., object] = boundOC,
        virtCtlCLI: Callable[..., object] = boundVirtCtl,
        **fxtCfgArgs: object,
    ) -> object:
        return target(
            request,
            fPars,
            *args,
            ocCLI=ocCLI,
            virtCtlCLI=virtCtlCLI,
            **fxtCfgArgs,
        )

    return Invoke


def PatchFixturePartial(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    '''Replace only BenchmarkRunner's module-local partial binding.'''
    partialFactory = mock.Mock(side_effect=BindVMcreateByBR)
    localFunctools = SimpleNamespace(partial=partialFactory)
    monkeypatch.setattr(MUT, 'functools', localFunctools)
    return partialFactory


def QueueFixtureConfigAndOwnership(
    moduleBoundary: SimpleNamespace,
    effectiveConfig: CaseConfig,
) -> None:
    '''Queue the fixture config merge and its explicit ownership tracker.'''
    QueueDirectConfig(moduleBoundary, effectiveConfig)

    moduleBoundary.configScript.QueueFactoryResults(SimpleNamespace(VMs=[]))


def QueueFixtureInitialConfig(
    moduleBoundary: SimpleNamespace,
    effectiveConfig: CaseConfig,
) -> None:
    '''Queue fixture config, tracker, and real create merge.'''
    QueueFixtureConfigAndOwnership(moduleBoundary, effectiveConfig)
    QueueDirectConfig(
        moduleBoundary, effectiveConfig, baseConfig=effectiveConfig,
    )


def QueueExcInfo(
    moduleBoundary: SimpleNamespace,
    *activeErrors: BaseException | None,
) -> None:
    '''Queue module-local sys.exc_info values for generator exits.'''
    moduleBoundary.excInfoResults.extend(
        (
            type(error) if (error is not None) else None,
            error,
            None,
        )
        for error in activeErrors
    )


def QueueVMcreateByBRInputs(
    moduleBoundary: SimpleNamespace,
    *,
    invocationUUID: str,
    ocClients: list[object],
    virtCtlClients: list[object],
    vmObjects: list[object],
    uidResults: list[object],
    runner: object,
    keyFileExists: bool = False,
) -> None:
    '''Queue all imported dependencies for one real nonzero BR batch.'''
    moduleBoundary.uuidResults.append(invocationUUID)
    moduleBoundary.ocClientResults.extend(ocClients)
    moduleBoundary.virtCtlClientResults.extend(virtCtlClients)
    moduleBoundary.vmResults.extend(vmObjects)
    moduleBoundary.uidResults.extend(uidResults)
    moduleBoundary.runnerResults.append(runner)
    moduleBoundary.fileResults.append(keyFileExists)


def MakeLifecycleVMcreateByBR(
    calls: list[tuple[object, ...]],
    outcomes: deque[object],
    stateAdds: deque[tuple[list[object], list[object]]],
) -> Callable[..., object]:
    '''Record inputs and apply queued lifecycle state only.'''
    def Create(
        request: object,
        fPars: MUT.FuncParsVMcreateByBR,
        *,
        ocCLI: Callable[..., object],
        virtCtlCLI: Callable[..., object],
        **fxtCfgArgs: object,
    ) -> object:
        calls.append((request, fPars, ocCLI, virtCtlCLI, fxtCfgArgs))
        if (not outcomes or not stateAdds):
            raise AssertionError('Unqueued lifecycle VMcreateByBR call')
        (vmAdds, ownershipAdds) = stateAdds.popleft()
        fPars.vmList.extend(vmAdds)
        fPars.ownResTrk.VMs.extend(ownershipAdds)
        outcome = outcomes.popleft()
        if isinstance(outcome, BaseException): raise outcome

        return outcome

    return Create


def test_FxtVMbyBR_RegistrationUsesModuleScope() -> None:
    assert (MUT.FxtVMbyBR.name == 'fxtVMbyBR')
    assert (MUT.FxtVMbyBR._fixture_function_marker.scope == 'module')


def test_FxtVMbyBR_InitialNegativeFailsBeforeYieldWithoutCleanup(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    effectiveConfig = moduleBoundary.configScript.Config(count=-1)
    QueueFixtureInitialConfig(moduleBoundary, effectiveConfig)
    request = SimpleNamespace(param={'fxtInf': {'brCfg': {'count': -1}}})
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory
    QueueExcInfo(moduleBoundary, ValueError('active setup error'))

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    with pytest.raises(ValueError) as raised:
        next(generator)

    assert (str(raised.value) == 'VM count must not be negative.')
    fPars = partialFactory.call_args.args[2]
    partialFactory.assert_called_once_with(
        MUT.VMcreateByBR,
        request,
        fPars,
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
    )
    assert (fPars.fxtCfgObj is effectiveConfig)
    assert (fPars.vmList == [])
    assert (fPars.ownResTrk.VMs == [])
    moduleBoundary.getResUID.assert_not_called()
    moduleBoundary.uuidFactory.assert_not_called()
    moduleBoundary.runnerFactory.assert_not_called()
    moduleBoundary.cleanupVMs.assert_not_called()


def test_FxtVMbyBR_InitialZeroYieldsFrozenEmptyBundleWithoutCleanup(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    effectiveConfig = moduleBoundary.configScript.Config(count=0)
    QueueFixtureInitialConfig(moduleBoundary, effectiveConfig)
    QueueDirectConfig(
        moduleBoundary, effectiveConfig, baseConfig=effectiveConfig,
    )
    request = SimpleNamespace(param={'fxtInf': {'brCfg': {'count': 0}}})
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory
    QueueExcInfo(moduleBoundary, GeneratorExit())

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    bundle = next(generator)

    assert (isinstance(bundle, MUT.FxtObjVMbyBR))
    assert (issubclass(MUT.FxtObjVMbyBR, MUT.FxtObjVM))
    assert (bundle.vmList == [])
    assert (bundle.vmCreate() == [])
    with pytest.raises(FrozenInstanceError):
        bundle.vmList = []
    fPars = partialFactory.call_args.args[2]
    partialFactory.assert_called_once_with(
        MUT.VMcreateByBR,
        request,
        fPars,
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
    )
    assert (bundle.vmList is fPars.vmList)
    assert (fPars.ownResTrk.VMs == [])
    generator.close()
    moduleBoundary.cleanupVMs.assert_not_called()
    moduleBoundary.getResUID.assert_not_called()
    moduleBoundary.uuidFactory.assert_not_called()
    moduleBoundary.tempfileProxy.mkdtemp.assert_not_called()
    moduleBoundary.runnerFactory.assert_not_called()
    moduleBoundary.vmFactory.assert_not_called()
    ocFactory.assert_not_called()
    virtCtlFactory.assert_not_called()


def test_FxtVMbyBR_InitialSuccessUsesSelectedConfigAndCleansOnClose(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    config = moduleBoundary.configScript.Config(
        count=1,
        wlName='initial',
        kCfg='/initial/kubeconfig',
        ns='initial-ns',
        ocCfg={'context': 'initial-context'},
        vcCfg={'kubeconfig': '/initial-config'},
    )
    QueueFixtureInitialConfig(moduleBoundary, config)
    request = SimpleNamespace(param={
        'ns': 'initial-ns',
        'kCfg': '/initial/kubeconfig',
        'fxtInf': {'brCfg': {'wlName': 'initial', 'count': 1}},
    })
    before = ConfigSnapshot(config)
    oc = SimpleNamespace(name='initial OC client')
    virtCtl = SimpleNamespace(name='initial virtctl client')
    vm = mock.Mock(name='initial VM')
    runner = MakeRunner()
    moduleBoundary.osProxy.environ['RUN_ARTIFACTS_PATH'] = '/artifacts'
    QueueVMcreateByBRInputs(
        moduleBoundary,
        invocationUUID='initial-rest',
        ocClients=[oc],
        virtCtlClients=[virtCtl],
        vmObjects=[vm],
        uidResults=[(True, '', ''), (True, 'initial-uid', '')],
        runner=runner,
    )
    moduleBoundary.excInfoResults.append((None, None, None))
    moduleBoundary.cleanupResults.append(None)
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory
    QueueExcInfo(moduleBoundary, GeneratorExit())

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    bundle = next(generator)

    fPars = partialFactory.call_args.args[2]
    assert (bundle.vmList is fPars.vmList)
    assert (fPars.fxtCfgObj is config)
    assert (bundle.vmList == [vm])
    assert (vm.Start.call_args.kwargs == {'timeout': '15m'})
    ocFactory.assert_called_once_with(
        ns='initial-ns',
        context='initial-context',
        kubeconfig='/initial/kubeconfig',
    )
    virtCtlFactory.assert_called_once_with(
        ns='initial-ns',
        kubeconfig='/initial/kubeconfig',
    )
    moduleBoundary.runnerFactory.assert_called_once_with(
        config.fxtInf.rootPath.br,
        execTO=2100,
    )
    runner.Call.assert_called_once_with(
        'benchmark_runner.main.main',
        'main',
        envUpd=mock.ANY,
    )
    partialFactory.assert_called_once_with(
        MUT.VMcreateByBR,
        request,
        fPars,
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
    )
    generator.close()
    moduleBoundary.cleanupVMs.assert_called_once_with(
        bundle.vmList,
        [(oc, 'VirtualMachine/initial-vm-initial', 'initial-uid')],
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )
    assert (ConfigSnapshot(config) == before)


@pytest.mark.parametrize('outcome', ['ready', 'false', 'exception', 'manual'])
@pytest.mark.parametrize('customTimeout', [None, '7m'])
def test_FxtVMbyBR_StartTracksOwnershipAndPreservesFailures(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
    outcome: str,
    customTimeout: str | None,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    primary = RuntimeError('synthetic startup failure')
    config = moduleBoundary.configScript.Config(
        count=2, startVM=(outcome != 'manual'),
        brEnv={
            'UNIT_BR_CUSTOM': 'child',
            'SAVE_ARTIFACTS_LOCAL': 'false',
            'DELETE_ALL': 'yes',
        },
        vmStart=(customTimeout or '15m'),
        vmRemove='11m' if (customTimeout is not None) else '5m',
    )
    QueueFixtureInitialConfig(moduleBoundary, config)
    request = SimpleNamespace(param={'fxtInf': {'brCfg': {'count': 2}}})
    localEnvironment = moduleBoundary.osProxy.environ
    localEnvironment.update({
        'SCALE_NODES': '[\'synthetic-worker\']',
        'UNIT_BR_CUSTOM': 'parent',
        'RUN_ARTIFACTS_PATH': '/artifacts',
    })
    environmentBefore = dict(localEnvironment)
    wrappers = [
        mock.Mock(name='startup VM zero'),
        mock.Mock(name='startup VM one'),
    ]

    if (outcome == 'exception'): wrappers[0].Start.side_effect = primary
    elif (outcome == 'false'): wrappers[0].Start.return_value = False
    else: wrappers[0].Start.return_value = True
    wrappers[1].Start.return_value = True
    clients = [
        SimpleNamespace(name='startup OC zero'),
        SimpleNamespace(name='startup OC one'),
    ]
    virtClients = [
        SimpleNamespace(name='startup virtctl zero'),
        SimpleNamespace(name='startup virtctl one'),
    ]
    runner = MakeRunner()
    QueueVMcreateByBRInputs(
        moduleBoundary,
        invocationUUID='startup-rest',
        ocClients=clients,
        virtCtlClients=virtClients,
        vmObjects=wrappers,
        uidResults=[
            (True, '', ''), (True, '', ''),
            (True, 'uid-zero', ''), (True, 'uid-one', ''),
        ],
        runner=runner,
    )
    moduleBoundary.excInfoResults.append((None, None, None))
    moduleBoundary.cleanupResults.append(None)

    if (outcome == 'exception'): QueueExcInfo(moduleBoundary, primary)
    elif (outcome == 'false'):
        QueueExcInfo(moduleBoundary, RuntimeError('active false-start error'))
    else: QueueExcInfo(moduleBoundary, GeneratorExit())
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    if (outcome in ('false', 'exception')):
        with pytest.raises(RuntimeError) as raised:
            next(generator)

        if (outcome == 'exception'): assert (raised.value is primary)
        else: assert ('default/windows-vm-startup-0' in str(raised.value))
        trackedVMs = wrappers[:1]
        wrappers[1].Start.assert_not_called()
    else:
        bundle = next(generator)
        assert (bundle.vmList == wrappers)
        trackedVMs = wrappers
        moduleBoundary.cleanupVMs.assert_not_called()
        generator.close()

    fPars = partialFactory.call_args.args[2]
    expectedWait = '11m' if (customTimeout is not None) else '5m'
    ownership = [
        (clients[0], 'VirtualMachine/windows-vm-startup-0', 'uid-zero'),
        (clients[1], 'VirtualMachine/windows-vm-startup-1', 'uid-one'),
    ]
    moduleBoundary.cleanupVMs.assert_called_once_with(
        trackedVMs,
        ownership,
        optsCleanUpRes={'optsDelRes': {'waitTime': expectedWait}},
    )
    if (outcome == 'manual'):
        wrappers[0].Start.assert_not_called()
        wrappers[1].Start.assert_not_called()
    else:
        wrappers[0].Start.assert_called_once_with(
            timeout=(customTimeout or '15m'),
        )
        if (outcome == 'ready'):
            wrappers[1].Start.assert_called_once_with(
                timeout=(customTimeout or '15m'),
            )
    environment = runner.Call.call_args.kwargs['envUpd']
    assert (environment['SAVE_ARTIFACTS_LOCAL'] == 'true')
    assert (environment['DELETE_ALL'] == 'no')
    assert (environment['UNIT_BR_CUSTOM'] == 'child')
    assert (environment['SCALE_NODES'] == '[\'synthetic-worker\']')
    assert (localEnvironment == environmentBefore)
    partialFactory.assert_called_once_with(
        MUT.VMcreateByBR,
        request,
        fPars,
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
    )

    assert (fPars.fxtCfgObj is config)
    assert (fPars.vmList == trackedVMs)
    assert (fPars.ownResTrk.VMs == ownership)


def test_FxtVMbyBR_RetainsSetupAndIsolatesTemporaryOverrides(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    setup = {
        'ns': 'setup-ns',
        'kCfg': '/setup/kubeconfig',
        'cliCfg': {
            'ocCfg': {
                'context': 'setup-context',
                'kubeconfig': '/setup-oc-config',
            },
            'vcCfg': {'kubeconfig': '/setup-config'},
        },
        'fxtInf': {
            'rootPath': {'br': '/setup/br-venv'},
            'brCfg': {'wlName': 'setup', 'count': 0},
            'gstCfg': {'usrCrd': {'sshUsr': 'setup-user'}},
        },
    }
    overrides = {
        'ns': 'call-ns',
        'kCfg': '/call/kubeconfig',
        'cliCfg': {'ocCfg': {'context': 'call-context'}},
        'fxtInf': {
            'rootPath': {'br': '/call/br-venv'},
            'brCfg': {'count': 1, 'timeout': {'vmRemove': '71s'}},
            'gstCfg': {'usrCrd': {'sshUsr': 'call-user'}},
        },
    }
    request = SimpleNamespace(param=setup)
    setupBefore = json.loads(json.dumps(setup, default=str))
    overridesBefore = json.loads(json.dumps(overrides, default=str))
    setupConfig = moduleBoundary.configScript.Config(
        count=0,
        rootPathBR='/setup/br-venv',
        kCfg='/setup/kubeconfig',
        ns='setup-ns',
        ocCfg={
            'context': 'setup-context',
            'kubeconfig': '/setup-oc-config',
        },
        vcCfg={'kubeconfig': '/setup-config'},
        sshUsr='setup-user',
    )
    QueueFixtureConfigAndOwnership(moduleBoundary, setupConfig)
    vmOne = SimpleNamespace(name='first VM')
    vmTwo = SimpleNamespace(name='second VM')
    vmThree = SimpleNamespace(name='third VM')
    ownedOne = (object(), 'VirtualMachine/first', 'uid-one')
    ownedTwo = (object(), 'VirtualMachine/second', 'uid-two')
    ownedThree = (object(), 'VirtualMachine/third', 'uid-three')
    calls: list[tuple[object, ...]] = []
    outcomes = deque([[], [], [vmOne], [], [vmTwo], [vmThree]])
    stateAdds = deque([
        ([], []), ([], []), ([vmOne], [ownedOne]),
        ([], []), ([vmTwo], [ownedTwo]), ([vmThree], [ownedThree]),
    ])
    target = MakeLifecycleVMcreateByBR(calls, outcomes, stateAdds)
    monkeypatch.setattr(MUT, 'VMcreateByBR', target)
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory
    moduleBoundary.cleanupResults.append(None)
    QueueExcInfo(moduleBoundary, GeneratorExit())

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    bundle = next(generator)
    fPars = partialFactory.call_args.args[2]
    configBefore = ConfigSnapshot(fPars.fxtCfgObj)
    assert (bundle.vmList is fPars.vmList)
    assert (bundle.vmCreate() == [])
    request.param = {'fxtInf': {'brCfg': {'count': -1}}}
    first = bundle.vmCreate(**overrides)
    assert (bundle.vmCreate() == [])
    second = bundle.vmCreate(
        fxtInf={'brCfg': {'wlName': 'second', 'count': 1}},
    )
    third = bundle.vmCreate(fxtInf={'brCfg': {'wlName': 'third', 'count': 1}})

    assert (first == [vmOne])
    assert (second == [vmTwo])
    assert (third == [vmThree])
    assert (bundle.vmList == [vmOne, vmTwo, vmThree])
    assert (fPars.fxtCfgObj is setupConfig)
    assert (ConfigSnapshot(fPars.fxtCfgObj) == configBefore)
    assert (json.loads(json.dumps(setup, default=str)) == setupBefore)
    assert (json.loads(json.dumps(overrides, default=str)) == overridesBefore)
    assert (calls[2][4] == overrides)
    assert (calls[3][4] == {})
    assert (all((call[0] is request) for call in calls))
    assert (all((call[1] is fPars) for call in calls))
    assert (all((call[2] is ocFactory) for call in calls))
    assert (all((call[3] is virtCtlFactory) for call in calls))
    partialFactory.assert_called_once_with(
        target,
        request,
        fPars,
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
    )
    generator.close()
    moduleBoundary.cleanupVMs.assert_called_once_with(
        bundle.vmList,
        [ownedOne, ownedTwo, ownedThree],
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )


@pytest.mark.parametrize('hasRequestParam', [False, True])
def test_FxtVMbyBR_DefaultSetupIsRetainedAcrossCalls(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
    hasRequestParam: bool,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    QueueFixtureConfigAndOwnership(
        moduleBoundary,
        moduleBoundary.initialConfig,
    )
    request = (
        SimpleNamespace(param={}) if hasRequestParam else SimpleNamespace()
    )
    first = SimpleNamespace(name='initial default VM')
    second = SimpleNamespace(name='later default VM')
    ownedFirst = (object(), 'VirtualMachine/initial', 'uid-initial')
    ownedSecond = (object(), 'VirtualMachine/later', 'uid-later')
    calls: list[tuple[object, ...]] = []
    target = MakeLifecycleVMcreateByBR(
        calls,
        deque([[first], [second]]),
        deque([([first], [ownedFirst]), ([second], [ownedSecond])]),
    )
    monkeypatch.setattr(MUT, 'VMcreateByBR', target)
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory
    moduleBoundary.cleanupResults.append(None)
    QueueExcInfo(moduleBoundary, GeneratorExit())

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    bundle = next(generator)
    fPars = partialFactory.call_args.args[2]
    assert (bundle.vmList == [first])
    assert (bundle.vmCreate() == [second])
    assert (bundle.vmList == [first, second])
    assert (all((call[0] is request) for call in calls))
    assert (all((call[1] is fPars) for call in calls))
    assert (all((call[2] is ocFactory) for call in calls))
    assert (all((call[3] is virtCtlFactory) for call in calls))
    partialFactory.assert_called_once_with(
        target,
        request,
        fPars,
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
    )
    generator.close()
    moduleBoundary.cleanupVMs.assert_called_once_with(
        bundle.vmList,
        [ownedFirst, ownedSecond],
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )


def test_FxtVMbyBR_PerCallReturnsAreAggregatedAcrossBatches(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    config = moduleBoundary.configScript.Config(count=0)
    QueueFixtureConfigAndOwnership(moduleBoundary, config)
    request = SimpleNamespace(param={'fxtInf': {'brCfg': {'count': 0}}})
    vmOne = SimpleNamespace(name='first batch VM')
    vmTwo = SimpleNamespace(name='second batch VM zero')
    vmThree = SimpleNamespace(name='second batch VM one')
    ownedOne = (object(), 'VirtualMachine/first-vm-first', 'uid-one')
    ownedTwo = (object(), 'VirtualMachine/second-vm-second-0', 'uid-two')
    ownedThree = (object(), 'VirtualMachine/second-vm-second-1', 'uid-three')
    calls: list[tuple[object, ...]] = []
    target = MakeLifecycleVMcreateByBR(
        calls,
        deque([[], [vmOne], [vmTwo, vmThree]]),
        deque([
            ([], []),
            ([vmOne], [ownedOne]),
            ([vmTwo, vmThree], [ownedTwo, ownedThree]),
        ]),
    )
    monkeypatch.setattr(MUT, 'VMcreateByBR', target)
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory
    moduleBoundary.cleanupResults.append(None)
    QueueExcInfo(moduleBoundary, GeneratorExit())

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    bundle = next(generator)
    firstBatch = bundle.vmCreate(
        fxtInf={'brCfg': {'wlName': 'first', 'count': 1}},
    )
    secondBatch = bundle.vmCreate(
        fxtInf={'brCfg': {'wlName': 'second', 'count': 2}},
    )

    assert (firstBatch == [vmOne])
    assert (secondBatch == [vmTwo, vmThree])
    assert (bundle.vmList == [vmOne, vmTwo, vmThree])
    fPars = partialFactory.call_args.args[2]
    assert (all((call[0] is request) for call in calls))
    assert (all((call[1] is fPars) for call in calls))
    assert (all((call[2] is ocFactory) for call in calls))
    assert (all((call[3] is virtCtlFactory) for call in calls))
    partialFactory.assert_called_once_with(
        target,
        request,
        fPars,
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
    )
    generator.close()
    moduleBoundary.cleanupVMs.assert_called_once_with(
        bundle.vmList,
        [ownedOne, ownedTwo, ownedThree],
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )


def test_FxtVMbyBR_VMcreateAllowsCLIOverrides(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    initialConfig = moduleBoundary.configScript.Config(count=0)
    selectedConfig = moduleBoundary.configScript.Config(
        count=1,
        wlName='selected',
        kCfg='/selected/kubeconfig',
        ns='selected-ns',
        ocCfg={'context': 'selected-context'},
        vcCfg={'kubeconfig': '/selected-config'},
    )
    QueueFixtureInitialConfig(moduleBoundary, initialConfig)
    QueueDirectConfig(moduleBoundary, selectedConfig, baseConfig=initialConfig)
    request = SimpleNamespace(param={'fxtInf': {'brCfg': {'count': 0}}})
    moduleBoundary.osProxy.environ['RUN_ARTIFACTS_PATH'] = '/artifacts'
    defaultOC = mock.Mock(name='default OC client factory')
    defaultVirtCtl = mock.Mock(name='default virtctl factory')
    oc = SimpleNamespace(name='selected OC client')
    virtCtl = SimpleNamespace(name='selected virtctl client')
    selectedOC = mock.Mock(return_value=oc, name='selected OC factory')
    selectedVirtCtl = mock.Mock(
        return_value=virtCtl,
        name='selected virtctl factory',
    )
    vm = mock.Mock(name='selected VM')
    runner = MakeRunner()
    QueueVMcreateByBRInputs(
        moduleBoundary,
        invocationUUID='selected-rest',
        ocClients=[],
        virtCtlClients=[],
        vmObjects=[vm],
        uidResults=[(True, '', ''), (True, 'selected-uid', '')],
        runner=runner,
    )
    moduleBoundary.excInfoResults.append((None, None, None))
    moduleBoundary.cleanupResults.append(None)
    QueueExcInfo(moduleBoundary, GeneratorExit())

    generator = MUT.FxtVMbyBR.__wrapped__(
        request,
        defaultOC,
        defaultVirtCtl,
    )
    bundle = next(generator)
    created = bundle.vmCreate(
        ns='selected-ns',
        kCfg='/selected/kubeconfig',
        cliCfg={
            'ocCfg': {'context': 'selected-context'},
            'vcCfg': {'kubeconfig': '/selected-config'},
        },
        ocCLI=selectedOC,
        virtCtlCLI=selectedVirtCtl,
        fxtInf={'brCfg': {'count': 1}},
    )

    assert (created == [vm])
    selectedOC.assert_called_once_with(
        ns='selected-ns',
        context='selected-context',
        kubeconfig='/selected/kubeconfig',
    )
    selectedVirtCtl.assert_called_once_with(
        ns='selected-ns',
        kubeconfig='/selected/kubeconfig',
    )
    defaultOC.assert_not_called()
    defaultVirtCtl.assert_not_called()
    fPars = partialFactory.call_args.args[2]
    partialFactory.assert_called_once_with(
        MUT.VMcreateByBR,
        request,
        fPars,
        ocCLI=defaultOC,
        virtCtlCLI=defaultVirtCtl,
    )
    generator.close()
    moduleBoundary.cleanupVMs.assert_called_once_with(
        bundle.vmList,
        [(oc, 'VirtualMachine/selected-vm-selected', 'selected-uid')],
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )


@pytest.mark.parametrize('argument', ['request', 'fPars'])
def test_FxtVMbyBR_VMcreateRejectsInternalKeywordReplacementBeforeOperations(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
    argument: str,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    config = moduleBoundary.configScript.Config(count=0)
    QueueFixtureInitialConfig(moduleBoundary, config)
    request = SimpleNamespace(param={'fxtInf': {'brCfg': {'count': 0}}})
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory
    QueueExcInfo(moduleBoundary, GeneratorExit())

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    bundle = next(generator)
    fPars = partialFactory.call_args.args[2]
    sharedVMs = fPars.vmList
    sharedOwnership = fPars.ownResTrk.VMs
    with pytest.raises(TypeError, match='multiple values'):
        bundle.vmCreate(**{argument: object()})

    assert (fPars.vmList is sharedVMs)
    assert (fPars.ownResTrk.VMs is sharedOwnership)
    assert (bundle.vmList is sharedVMs)
    assert (bundle.vmList == [])
    assert (sharedOwnership == [])
    assert (moduleBoundary.namedDictFactory.call_count == 3)
    moduleBoundary.getResUID.assert_not_called()
    moduleBoundary.ocFactory.assert_not_called()
    moduleBoundary.virtCtlFactory.assert_not_called()
    generator.close()
    moduleBoundary.cleanupVMs.assert_not_called()
    partialFactory.assert_called_once_with(
        MUT.VMcreateByBR,
        request,
        fPars,
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
    )


def test_FxtVMbyBR_PreyieldPartialFailureCleansCreatedVMs(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    config = moduleBoundary.configScript.Config(count=2)
    QueueFixtureConfigAndOwnership(moduleBoundary, config)
    request = SimpleNamespace(param={'fxtInf': {'brCfg': {'count': 2}}})
    primary = RuntimeError('wrapper failed')
    vmZero = SimpleNamespace(name='created before failure')
    owned = [
        (object(), 'VirtualMachine/partial-0', 'uid-zero'),
        (object(), 'VirtualMachine/partial-1', 'uid-one'),
    ]
    calls: list[tuple[object, ...]] = []
    target = MakeLifecycleVMcreateByBR(
        calls,
        deque([primary]),
        deque([([vmZero], owned)]),
    )
    monkeypatch.setattr(MUT, 'VMcreateByBR', target)
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory
    moduleBoundary.cleanupResults.append(None)
    QueueExcInfo(moduleBoundary, primary)

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    with pytest.raises(RuntimeError) as raised:
        next(generator)

    assert (raised.value is primary)
    fPars = partialFactory.call_args.args[2]
    assert (fPars.vmList == [vmZero])
    assert (fPars.ownResTrk.VMs == owned)
    partialFactory.assert_called_once_with(
        target,
        request,
        fPars,
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
    )
    assert (calls[0][0] is request)
    assert (calls[0][1] is fPars)
    assert (calls[0][2] is ocFactory)
    assert (calls[0][3] is virtCtlFactory)
    moduleBoundary.cleanupVMs.assert_called_once_with(
        [vmZero],
        owned,
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )
    moduleBoundary.logs.error.assert_not_called()


def test_FxtVMbyBR_LaterPartialFailureCleansAggregateOnClose(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    config = moduleBoundary.configScript.Config(count=0)
    QueueFixtureConfigAndOwnership(moduleBoundary, config)
    request = SimpleNamespace(param={'fxtInf': {'brCfg': {'count': 0}}})
    primary = RuntimeError('later batch failed')
    vmZero = SimpleNamespace(name='later partial VM')
    owned = [
        (object(), 'VirtualMachine/later-0', 'uid-zero'),
        (object(), 'VirtualMachine/later-1', 'uid-one'),
    ]
    calls: list[tuple[object, ...]] = []
    target = MakeLifecycleVMcreateByBR(
        calls,
        deque([[], primary]),
        deque([([], []), ([vmZero], owned)]),
    )
    monkeypatch.setattr(MUT, 'VMcreateByBR', target)
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory
    moduleBoundary.cleanupResults.append(None)
    QueueExcInfo(moduleBoundary, GeneratorExit())

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    bundle = next(generator)
    fPars = partialFactory.call_args.args[2]
    with pytest.raises(RuntimeError) as raised:
        bundle.vmCreate(fxtInf={'brCfg': {'count': 2}})

    assert (raised.value is primary)
    assert (bundle.vmList == [vmZero])
    moduleBoundary.cleanupVMs.assert_not_called()
    generator.close()
    moduleBoundary.cleanupVMs.assert_called_once_with(
        bundle.vmList,
        owned,
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )
    assert (fPars.ownResTrk.VMs == owned)
    assert (calls[1][0] is request)
    assert (calls[1][1] is fPars)
    assert (calls[1][2] is ocFactory)
    assert (calls[1][3] is virtCtlFactory)
    moduleBoundary.logs.error.assert_not_called()


@pytest.mark.parametrize('cleanupFails', [False, True])
def test_FxtVMbyBR_SetupFailurePreservesPrimaryAndCleansOwnedVMs(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
    cleanupFails: bool,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    config = moduleBoundary.configScript.Config(count=0)
    QueueFixtureConfigAndOwnership(moduleBoundary, config)
    request = SimpleNamespace(param={'fxtInf': {'brCfg': {'count': 0}}})
    primary = ValueError('primary provisioning failure')
    cleanup = RuntimeError('cleanup failure')
    owned = (object(), 'VirtualMachine/partial', 'uid-partial')
    calls: list[tuple[object, ...]] = []
    target = MakeLifecycleVMcreateByBR(
        calls,
        deque([primary]),
        deque([([], [owned])]),
    )
    monkeypatch.setattr(MUT, 'VMcreateByBR', target)
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory
    moduleBoundary.cleanupResults.append(cleanup if cleanupFails else None)
    QueueExcInfo(moduleBoundary, primary)

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    with pytest.raises(ValueError) as raised:
        next(generator)

    assert (raised.value is primary)
    fPars = partialFactory.call_args.args[2]
    assert (fPars.vmList == [])
    assert (fPars.ownResTrk.VMs == [owned])
    moduleBoundary.cleanupVMs.assert_called_once_with(
        [],
        [owned],
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )
    if cleanupFails:
        moduleBoundary.logs.error.assert_called_once_with(
            'Could not clean up fixture VMs: %s',
            cleanup,
        )

    else: moduleBoundary.logs.error.assert_not_called()


@pytest.mark.parametrize('teardown', ['resume', 'close'])
@pytest.mark.parametrize('cleanupFails', [False, True])
def test_FxtVMbyBR_TeardownSurfacesCleanupFailureWithoutPrimary(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
    teardown: str,
    cleanupFails: bool,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    config = moduleBoundary.configScript.Config(count=0)
    QueueFixtureConfigAndOwnership(moduleBoundary, config)
    request = SimpleNamespace(param={'fxtInf': {'brCfg': {'count': 0}}})
    calls: list[tuple[object, ...]] = []
    target = MakeLifecycleVMcreateByBR(calls, deque([[]]), deque([([], [])]))
    monkeypatch.setattr(MUT, 'VMcreateByBR', target)
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory
    vm = SimpleNamespace(name='teardown VM')
    cleanup = RuntimeError('cleanup failure')
    moduleBoundary.cleanupResults.append(cleanup if cleanupFails else None)
    activeError = GeneratorExit() if (teardown == 'close') else None
    QueueExcInfo(moduleBoundary, activeError)

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    bundle = next(generator)
    bundle.vmList.append(vm)
    finish = (
        generator.close if (teardown == 'close') else lambda: next(generator)
    )
    if cleanupFails:
        with pytest.raises(RuntimeError) as raised:
            finish()
        assert (raised.value is cleanup)
        moduleBoundary.logs.error.assert_called_once_with(
            'Could not clean up fixture VMs: %s',
            cleanup,
        )
    elif (teardown == 'resume'):
        with pytest.raises(StopIteration):
            finish()
        moduleBoundary.logs.error.assert_not_called()
    else:
        finish()
        moduleBoundary.logs.error.assert_not_called()

    fPars = partialFactory.call_args.args[2]
    moduleBoundary.cleanupVMs.assert_called_once_with(
        bundle.vmList,
        fPars.ownResTrk.VMs,
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )
    assert (bundle.vmList is fPars.vmList)
    assert (fPars.fxtCfgObj is config)
    assert (bundle.vmList == [vm])


def test_FxtVMbyBR_TestFailurePreservesPrimaryWhenCleanupFails(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    config = moduleBoundary.configScript.Config(count=0)
    QueueFixtureConfigAndOwnership(moduleBoundary, config)
    request = SimpleNamespace(param={'fxtInf': {'brCfg': {'count': 0}}})
    calls: list[tuple[object, ...]] = []
    target = MakeLifecycleVMcreateByBR(calls, deque([[]]), deque([([], [])]))
    monkeypatch.setattr(MUT, 'VMcreateByBR', target)
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory
    primary = ValueError('primary test failure')
    cleanup = RuntimeError('cleanup failure')
    moduleBoundary.cleanupResults.append(cleanup)
    QueueExcInfo(moduleBoundary, primary)

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    bundle = next(generator)
    vm = SimpleNamespace(name='body failure VM')
    bundle.vmList.append(vm)
    fPars = partialFactory.call_args.args[2]
    owned = (object(), 'VirtualMachine/body-failure', 'uid-body')
    fPars.ownResTrk.VMs.append(owned)

    with pytest.raises(ValueError) as raised:
        generator.throw(primary)

    assert (raised.value is primary)
    moduleBoundary.cleanupVMs.assert_called_once_with(
        bundle.vmList,
        [owned],
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )
    moduleBoundary.logs.error.assert_called_once_with(
        'Could not clean up fixture VMs: %s',
        cleanup,
    )


@pytest.mark.parametrize('vmRemove', [None, '420s'])
@pytest.mark.parametrize('phase', ['normal', 'setup', 'later', 'body'])
@pytest.mark.parametrize('cleanupFails', [False, True])
def test_FxtVMbyBR_RemovalTimeoutUsesInitialConfigAcrossFailurePaths(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
    vmRemove: str | None,
    phase: str,
    cleanupFails: bool,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    defaultRemoval = '5m'
    expectedWait = vmRemove or defaultRemoval
    config = moduleBoundary.configScript.Config(
        count=0, vmStart='15m',
        vmRemove=expectedWait,
    )
    request = SimpleNamespace(param={'fxtInf': {'brCfg': {'count': 0}}})
    requestBefore = json.loads(json.dumps(request.param, default=str))
    defaultsBefore = ConfigSnapshot(SOURCE_DEFAULT_CONFIG)
    QueueFixtureConfigAndOwnership(moduleBoundary, config)
    primary = RuntimeError('primary fixture failure')
    secondary = RuntimeError('cleanup fixture failure')
    wrapper = SimpleNamespace(name='initial fixture VM')
    owned = (object(), 'VirtualMachine/owned', 'owned-uid')
    calls: list[tuple[object, ...]] = []

    def Create(
        callRequest: object,
        fPars: MUT.FuncParsVMcreateByBR,
        *,
        ocCLI: Callable[..., object],
        virtCtlCLI: Callable[..., object],
        **fxtCfgArgs: object,
    ) -> list[object]:
        calls.append((callRequest, fPars, ocCLI, virtCtlCLI, fxtCfgArgs))
        if (len(calls) == 1):
            timeout = fPars.fxtCfgObj.fxtInf.brCfg.timeout
            assert (timeout.vmStart == '15m')
            assert (timeout.vmRemove == expectedWait)
            fPars.ownResTrk.VMs.append(owned)
            fPars.vmList.append(wrapper)

            if (phase == 'setup'): raise primary
            return [wrapper]
        assert (phase == 'later')
        laterConfig = fxtCfgArgs['fxtInf']
        assert (laterConfig.brCfg.timeout.vmRemove == '1s')
        raise primary

    monkeypatch.setattr(MUT, 'VMcreateByBR', Create)
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory
    moduleBoundary.cleanupResults.append(secondary if cleanupFails else None)
    activeError = primary if (phase in ('setup', 'body')) else None
    QueueExcInfo(moduleBoundary, activeError)

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    if (phase == 'setup'):
        with pytest.raises(RuntimeError) as raised:
            next(generator)
        assert (raised.value is primary)
    else:
        bundle = next(generator)
        assert isinstance(bundle, MUT.FxtObjVMbyBR)
        if (phase == 'later'):
            laterOverride = SimpleNamespace(
                brCfg=SimpleNamespace(
                    timeout=SimpleNamespace(vmRemove='1s'),
                ),
            )
            with pytest.raises(RuntimeError) as raised:
                bundle.vmCreate(fxtInf=laterOverride)
            assert (raised.value is primary)
        if (phase == 'body'):
            with pytest.raises(RuntimeError) as raised:
                generator.throw(primary)
            assert (raised.value is primary)
        elif cleanupFails:
            with pytest.raises(RuntimeError) as raised:
                next(generator)
            assert (raised.value is secondary)
        else:
            with pytest.raises(StopIteration):
                next(generator)

    fPars = partialFactory.call_args.args[2]
    partialFactory.assert_called_once_with(
        Create,
        request,
        fPars,
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
    )
    assert (calls[0][0] is request)
    assert (calls[0][1] is fPars)
    assert (calls[0][2] is ocFactory)
    assert (calls[0][3] is virtCtlFactory)
    if (phase == 'later'):
        assert (calls[1][4]['fxtInf'].brCfg.timeout.vmRemove == '1s')
    moduleBoundary.cleanupVMs.assert_called_once_with(
        [wrapper],
        [owned],
        optsCleanUpRes={'optsDelRes': {'waitTime': expectedWait}},
    )
    assert (
        json.loads(json.dumps(request.param, default=str)) == requestBefore
    )
    assert (ConfigSnapshot(SOURCE_DEFAULT_CONFIG) == defaultsBefore)

    if cleanupFails:
        moduleBoundary.logs.error.assert_called_once_with(
            'Could not clean up fixture VMs: %s',
            secondary,
        )
    else: moduleBoundary.logs.error.assert_not_called()


@pytest.mark.parametrize('cleanupFails', [False, True])
def test_FxtVMbyBR_RunnerTimeoutReconcilesAndCleansPartialVMs(
    monkeypatch: pytest.MonkeyPatch,
    moduleBoundary: SimpleNamespace,
    cleanupFails: bool,
) -> None:
    partialFactory = PatchFixturePartial(monkeypatch)
    config = moduleBoundary.configScript.Config(count=2)
    QueueFixtureInitialConfig(moduleBoundary, config)
    request = SimpleNamespace(param={'fxtInf': {'brCfg': {'count': 2}}})
    requestBefore = json.loads(json.dumps(request.param, default=str))
    defaultsBefore = ConfigSnapshot(SOURCE_DEFAULT_CONFIG)
    localEnvironment = moduleBoundary.osProxy.environ
    localEnvironment.update({
        'SCALE_NODES': '[\'synthetic-worker\']',
        'RUN_ARTIFACTS_PATH': '/artifacts',
    })
    clients = [
        mock.Mock(name='timeout OC zero'),
        mock.Mock(name='timeout OC one'),
    ]
    runner = MakeRunner()
    primary = TimeoutError('runner exceeded its execution budget')
    cleanup = RuntimeError('cleanup failed')
    runner.Call.side_effect = primary
    QueueVMcreateByBRInputs(
        moduleBoundary,
        invocationUUID='timeout-rest',
        ocClients=clients,
        virtCtlClients=[],
        vmObjects=[],
        uidResults=[
            (True, '', ''), (True, '', ''),
            (True, 'partial-uid', ''), (True, '', ''),
        ],
        runner=runner,
    )
    QueueExcInfo(moduleBoundary, primary, primary)
    moduleBoundary.cleanupResults.append(cleanup if cleanupFails else None)
    ocFactory = moduleBoundary.ocFactory
    virtCtlFactory = moduleBoundary.virtCtlFactory

    generator = MUT.FxtVMbyBR.__wrapped__(request, ocFactory, virtCtlFactory)
    with pytest.raises(TimeoutError) as raised:
        next(generator)

    assert (raised.value is primary)
    runner.Call.assert_called_once()
    moduleBoundary.runnerFactory.assert_called_once_with(
        config.fxtInf.rootPath.br,
        execTO=2400,
    )
    moduleBoundary.vmFactory.assert_not_called()
    record = (clients[0], 'VirtualMachine/windows-vm-timeout-0', 'partial-uid')
    moduleBoundary.cleanupVMs.assert_called_once_with(
        [],
        [record],
        optsCleanUpRes={'optsDelRes': {'waitTime': '5m'}},
    )
    assert (moduleBoundary.getResUID.call_count == 4)
    assert (moduleBoundary.ocFactory.call_count == 2)
    assert (localEnvironment['SCALE_NODES'] == '[\'synthetic-worker\']')
    assert (
        json.loads(json.dumps(request.param, default=str)) == requestBefore
    )
    assert (ConfigSnapshot(SOURCE_DEFAULT_CONFIG) == defaultsBefore)
    if cleanupFails:
        moduleBoundary.logs.error.assert_any_call(
            'Could not clean up fixture VMs: %s',
            cleanup,
        )
    else:
        assert not any(
            (call.args[:1] == ('Could not clean up fixture VMs: %s',))
            for call in moduleBoundary.logs.error.call_args_list
        )
    fPars = partialFactory.call_args.args[2]
    partialFactory.assert_called_once_with(
        MUT.VMcreateByBR,
        request,
        fPars,
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
    )
