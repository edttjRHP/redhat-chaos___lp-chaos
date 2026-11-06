'''
BenchmarkRunner fixtures - VM provisioning at scale.

https://github.com/redhat-performance/benchmark-runner
'''
from collections.abc import Callable, Generator
import dataclasses
import functools
import logging
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
from uuid import uuid4

import pytest

from fixtures.libs.CommonConstant import LPQE__DEF__NAMESPACE
from fixtures.VM import FxtObjVM
from libs.CustomTypes import NamedDict
from libs.OpenShift.LP.Virt.VM.VM import VM
from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.Utils.LibInVenv import ModInVenv


logs = logging.getLogger()
_DEF__FXT_CFG = NamedDict(
    kCfg=(
        os.environ.get('KUBECONFIG') or
        os.path.expanduser('~/.kube/config')
    ),
    cliCfg=NamedDict(
        ocCfg={},
        vcCfg={},
    ),
    ns=LPQE__DEF__NAMESPACE,
    fxtInf=NamedDict(
        rootPath=NamedDict(
            br=Path(os.getenv(
                (
                    'IENG_PYTEST____FXT__BENCHMARKRUNNER____'
                    'DEF_FXT_CFG__ROOTPATH_BR'
                ),
                (
                    f'{Path(__file__).resolve().parent.parent}/'
                    'vendor/BenchmarkRunner'
                ),
            )),
        ),
        brCfg=NamedDict(
            count=1,
            workloadName='windows',
            workloadKind='vm',
        ),
        gstCfg=NamedDict(
            usrCrd=NamedDict(
                sshUsr='qa-usr',
                sshKey='~/.ssh/openshift-qe.pem',
            ),
        ),
    ),
    testPars=NamedDict(),
)


@dataclasses.dataclass(frozen=True)
class FuncParsVMcreateByBR:
    fxtCfgObj: NamedDict
    vmList: list[VM] = dataclasses.field(default_factory=list)
    ownResTrk: NamedDict[
        str, list[tuple[OCcli, str, str]]
    ] = dataclasses.field(
        default_factory=(lambda: NamedDict(VMs=[])),
    )   # List of `(oc, resRef, resUID)`.


@dataclasses.dataclass(frozen=True)
class FxtObjVMbyBR(FxtObjVM):
    '''Benchmark-runner VM fixture object.'''


def VMcreateByBR(
    request: Any,
    fPars: FuncParsVMcreateByBR,
    *,
    ocCLI: Callable[..., Any],
    virtCtlCLI: Callable[..., Any],
    **fxtCfgArgs: Any,
) -> list[VM]:
    '''
    Provision VMs via benchmark-runner and return `VM` wrapper objects.

    Calls `benchmark_runner.main.main.main()` with the required Env. Variables
    inside `fxtInf.rootPath.br` and reconciles created VM UIDs.

    :param request:
        Pytest `request` built-in fixture.
        See `FxtVMbyBR()` for fixture parametrization details.
    :param fPars:
        Persistent function parameters.
          - fxtCfgObj:
                Base fixture configuration. See `FxtVMbyBR` for details.
          - vmList:
                Shared list to which created `VM` wrappers are appended.
          - ownResTrk:
                Shared ownership OpenShift Resource records for fixture
                cleanup. Each item below is a list of `oc` CLI, Resource
                Reference and UID.
                  - VMs:    VirtualMachine Resources.
    :param ocCLI:       Factory for the `OCcli` used by each VM.
    :param virtCtlCLI:  Factory for the `VirtCtlCLI` used by each VM.
    :param fxtCfgArgs:  Fixture conf. overrides. See `FxtVMbyBR` for details.

    :return:
        A list of `VM` instances created by this call.
    '''
    fxtCfg = fPars.fxtCfgObj | NamedDict(fxtCfgArgs)

    vmCount = fxtCfg.fxtInf.brCfg.count
    if (vmCount < 0): raise ValueError('VM count must not be negative.')
    if not vmCount: return []

    ns = fxtCfg.ns
    isScale = vmCount > 1
    newVMs = []

    logs.info(f'Creating {vmCount} VM(s) via benchmark-runner')
    workload = (
        f'{fxtCfg.fxtInf.brCfg.workloadName}_'
        f'{fxtCfg.fxtInf.brCfg.workloadKind}'
    )
    invocationUUID = str(uuid4())
    truncUUID = invocationUUID.split('-')[0]
    env = {
        'KUBECONFIG': fxtCfg.kCfg,
        'WORKLOAD': workload,
        'RUN_TYPE': 'test_ci',
        'NAMESPACE': ns,
        'DELETE_ALL': 'no',
        'RUN_ARTIFACTS_PATH': (
            os.environ.get('RUN_ARTIFACTS_PATH') or tempfile.mkdtemp()
        ),
        'UUID': invocationUUID,
    }
    if isScale:
        env['SCALE'] = str(vmCount)
        env['SCALE_NODES'] = str([os.environ.get('WORKER_NODE')])
    else:
        env['SCALE'] = ''
        env['SCALE_NODES'] = ''

    namePfx = workload.removesuffix('_scale').replace('_', '-')
    brVMs = []
    for idx in range(vmCount):
        vmName = f'{namePfx}-{truncUUID}'
        if isScale: vmName += f'-{idx}'
        oc = ocCLI(ns=ns, **fxtCfg.cliCfg.ocCfg)
        (lookupOK, prevUID, lookupErr) = OCcli.GetResUID(
            oc, f'VirtualMachine/{vmName}'
        )
        if not lookupOK:
            raise RuntimeError(
                f'Could not establish whether VM `{vmName}` already '
                f'exists: {lookupErr}'
            )
        if prevUID:
            raise RuntimeError(
                'Refusing to invoke benchmark-runner because VM '
                f'`{vmName}` already exists.'
            )
        brVMs.append((vmName, oc))

    try:
        ModInVenv(fxtCfg.fxtInf.rootPath.br).Call(
            'benchmark_runner.main.main', 'main', envUpd=env,
        )
    finally:
        primExc = sys.exc_info()[1]
        lookupFail = []
        missingVMs = []
        for (vmName, oc) in brVMs:
            try:
                (lookupOK, curUID, lookupErr) = OCcli.GetResUID(
                    oc, f'VirtualMachine/{vmName}'
                )
                if not lookupOK:
                    lookupFail.append(f'VM `{vmName}`: {lookupErr}')
                    continue
                if not curUID:
                    missingVMs.append(vmName)
                    continue
                fPars.ownResTrk.VMs.append(
                    (oc, f'VirtualMachine/{vmName}', curUID)
                )
            except Exception as ex:
                lookupFail.append(f'VM `{vmName}`: {ex}')

        i = 2
        while i:
            message = ''
            if ((i == 2) and lookupFail):
                message = ((
                    'Could not reconcile `benchmark-runner` VM '
                    'ownership: '
                ) + (
                    ', '.join(lookupFail)
                ))
            elif ((i == 1) and missingVMs):
                message = ((
                    'The `benchmark-runner` did not create expected '
                    'VM(s): '
                ) + (
                    ', '.join(f'`{name}`' for name in missingVMs)
                ))
            if message:
                if (primExc is None):
                    raise RuntimeError(message)
                logs.error(
                    f'{message}; Preserving benchmark-runner failure: '
                    f'{primExc}'
                )
            i -= 1

    sshKey = Path(env['RUN_ARTIFACTS_PATH'], 'ssh', 'vm_key')
    sshKey = (
        str(sshKey) if sshKey.is_file()
        else fxtCfg.fxtInf.gstCfg.usrCrd.sshKey
    )

    for (vmName, oc) in brVMs:
        newVMs.append(VM(
            name=vmName,
            ns=ns,
            oc=oc,
            virtCtl=virtCtlCLI(
                ns=ns, **fxtCfg.cliCfg.vcCfg
            ),
            sshUsr=fxtCfg.fxtInf.gstCfg.usrCrd.sshUsr,
            sshKey=sshKey,
        ))
        fPars.vmList.append(newVMs[-1])

    return newVMs


@pytest.fixture(name='fxtVMbyBR', scope='module')
def FxtVMbyBR(
    request: Any,
    fxtOCcli: Callable[..., Any],
    fxtVirtCtlCLI: Callable[..., Any],
) -> Generator[FxtObjVMbyBR, None, None]:
    '''
    Create initial VMs and expose their shared factory and aggregate list.

    Configure via indirect parametrize with `dict`:
        @pytest.mark.parametrize(
            'fxtVMbyBR,',
            [({'fxtInf': NamedDict(brCfg=NamedDict(count=3))},)],
            indirect=True,
        )

        Fixture Configuration:
          - kCfg:
            The `KUBECONFIG` file for BenchmarkRunner.
          - cliCfg:
            Configuration options for CLIs:
              - ocCfg:  Configuration options for `OCcli`.
              - vcCfg:  Configuration options for `VirtCtlCLI`.
          - ns:
            Kubernetes Namespace (default: `LPQE__DEF__NAMESPACE`).
          - fxtInf:
            Fixture settings:
              - rootPath:
                Root dir. of vEnv libraries:
                  - br: Location where `benchmark-runner` is installed.
              - brCfg:
                BenchmarkRunner workload configurations:
                  - count:  Number of VMs to create (default: `1`).
                  - workloadName:
                    Template directory name inside `benchmark-runner`
                    (default: `'windows'`).
                  - workloadKind:
                    Resource kind suffix (default: `'vm'`).
              - gstCfg:
                Guest System configurations:
                  - usrCrd:
                    User Credential to connect to the Guest System.
                      - sshUsr: SSH UserName (default: `'qa-usr'`).
                      - sshKey: Path to the SSH private key (default:
                        `'~/.ssh/openshift-qe.pem'`).
          - testPars:
            Parameters to be passed to test.

    :yield:
        A `FxtObjVMbyBR` where:
          - vmCreate:   A callable to create VMs through benchmark-runner.
          - vmList:     A shared list of `VM` wrappers managed by this fixture.
                        Calling `fxtVMbyBR.vmCreate()` adds wrappers to this
                        list.
    '''
    fxtCfg = _DEF__FXT_CFG | NamedDict(**getattr(request, 'param', {}))
    vmList = []
    ownResTrk = NamedDict(VMs=[])

    try:
        vmCreate = functools.partial(
            VMcreateByBR,
            request,
            FuncParsVMcreateByBR(
                fxtCfgObj=fxtCfg,
                vmList=vmList,
                ownResTrk=ownResTrk,
            ),
            ocCLI=fxtOCcli,
            virtCtlCLI=fxtVirtCtlCLI,
        )
        vmCreate()
        yield FxtObjVMbyBR(vmCreate=vmCreate, vmList=vmList)
    finally:
        primExc = sys.exc_info()[1]
        try:
            if (vmList or ownResTrk.VMs): VM.CleanUpVMs(vmList, ownResTrk.VMs)
        except Exception as error:
            logs.error('Could not clean up fixture VMs: %s', error)
            if ((primExc is None) or isinstance(primExc, GeneratorExit)): raise
