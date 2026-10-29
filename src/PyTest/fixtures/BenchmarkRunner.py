'''
BenchmarkRunner fixtures - VM provisioning at scale.

https://github.com/redhat-performance/benchmark-runner

Runs benchmark-runner in its own virtualenv via subprocess so its dependency
pins never share the PyTest process environment.
'''
from collections.abc import Callable, Generator
import dataclasses
import functools
import logging
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Optional, Union
from uuid import uuid4

import pytest

from fixtures.libs.CommonConstant import LPQE__DEF__NAMESPACE
from fixtures.VM import FxtObjVM
from libs.CustomTypes import NamedDict
from libs.OpenShift.LP.Virt.VM.VM import VM
from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.Utils.VenvTool import RunInVenv


logs = logging.getLogger()
_DEF__ROOT_PATH_BR = Path(os.getenv(
    'IENG_PYTEST____FXT__BR____DEF_FXT_CFG__ROOTPATH',
    f'{Path(__file__).resolve().parent.parent}/vendor/benchmark-runner',
))
_DEF__FXT_CFG = NamedDict(
    brCfg=NamedDict(
        workloadName='windows',
        workloadKind='vm',
        rootPath=_DEF__ROOT_PATH_BR,
        python=None,
    ),
    count=1,
    cliCfg=NamedDict(
        ocCfg={},
        vcCfg={},
    ),
    ns=LPQE__DEF__NAMESPACE,
    gstCfg=NamedDict(
        usrCrd=NamedDict(
            sshUsr='qa-usr',
            sshKey='~/.ssh/openshift-qe.pem',
        ),
    ),
)
_BR__MAIN_ARGV = [
    '-c',
    (
        'from benchmark_runner.main.main import main; '
        'raise SystemExit(main() or 0)'
    ),
]


@dataclasses.dataclass(frozen=True)
class FuncParsVMcreateByBR:
    fxtCfgObj: NamedDict
    vmList: list[VM] = dataclasses.field(default_factory=list)
    ownVMs: list[tuple[OCcli, str, str]] = dataclasses.field(
        default_factory=list,
    )  # (oc, resRef, resUID)


@dataclasses.dataclass(frozen=True)
class FxtObjVMbyBR(FxtObjVM):
    '''Benchmark-runner VM fixture object.'''


def _BRenv(
    *,
    workload: str,
    ns: str,
    artifactsPath: str,
    invocationUUID: str,
    scale: str,
    scaleNodes: str,
) -> dict[str, str]:
    '''Build uppercase OS Env. Variables consumed by benchmark-runner.'''
    return {
        'WORKLOAD': workload,
        'RUN_TYPE': 'test_ci',
        'NAMESPACE': ns,
        'DELETE_ALL': 'False',
        'RUN_ARTIFACTS_PATH': artifactsPath,
        'UUID': invocationUUID,
        'SCALE': scale,
        'SCALE_NODES': scaleNodes,
    }


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

    Invokes benchmark-runner's `main()` inside its tool venv with the required
    environment variables and reconciles created VM UIDs.

    :param request:
        Pytest `request` built-in fixture.
        See `FxtVMbyBR()` for fixture parametrization details.
    :param fPars:
        Persistent function parameters.
          - fxtCfgObj:
                Base fixture configuration. See `FxtVMbyBR` for details.
          - vmList:
                Shared list to which created `VM` wrappers are appended.
          - ownVMs:     Shared ownership VM records for fixture cleanup.
    :param ocCLI:       Factory for the `OCcli` used by each VM.
    :param virtCtlCLI:  Factory for the `VirtCtlCLI` used by each VM.
    :param fxtCfgArgs:  Fixture conf. overrides. See `FxtVMbyBR` for details.
    :return:
        A list of `VM` instances created by this call.
    '''
    fxtCfg = fPars.fxtCfgObj | NamedDict(fxtCfgArgs)

    vmCount = fxtCfg.count
    if (vmCount < 0): raise ValueError('VM count must not be negative.')
    if not vmCount: return []

    ns = fxtCfg.ns
    isScale = vmCount > 1
    newVMs = []
    rootPath = Path(fxtCfg.brCfg.rootPath)
    python: Optional[Union[str, Path]] = fxtCfg.brCfg.python
    artifactsPath = os.environ.get('RUN_ARTIFACTS_PATH') or tempfile.mkdtemp()
    invocationUUID = str(uuid4())
    truncUUID = invocationUUID.split('-')[0]
    workload = f'{fxtCfg.brCfg.workloadName}_{fxtCfg.brCfg.workloadKind}'
    scale = str(vmCount) if isScale else ''
    scaleNodes = (
        str([os.environ.get('WORKER_NODE')]) if isScale else ''
    )

    logs.info(f'Creating {vmCount} VM(s) via benchmark-runner')
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

    brEnv = _BRenv(
        workload=workload,
        ns=ns,
        artifactsPath=artifactsPath,
        invocationUUID=invocationUUID,
        scale=scale,
        scaleNodes=scaleNodes,
    )
    try:
        RunInVenv(
            rootPath,
            _BR__MAIN_ARGV,
            python=python,
            envVars=brEnv,
            cwd=rootPath,
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
                fPars.ownVMs.append(
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

    sshKey = Path(artifactsPath, 'ssh', 'vm_key')
    sshKey = (
        str(sshKey) if sshKey.is_file()
        else fxtCfg.gstCfg.usrCrd.sshKey
    )

    for (vmName, oc) in brVMs:
        newVMs.append(VM(
            name=vmName,
            ns=ns,
            oc=oc,
            virtCtl=virtCtlCLI(
                ns=ns, **fxtCfg.cliCfg.vcCfg
            ),
            sshUsr=fxtCfg.gstCfg.usrCrd.sshUsr,
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
            [({'count': 3},)],
            indirect=True,
        )

        Fixture Configuration:
          - brCfg:
            Benchmark-runner workload configuration:
              - workloadName:
                Template directory name inside `benchmark-runner`
                (default: `'windows'`).
              - workloadKind:
                Resource kind suffix (default: `'vm'`).
              - rootPath:
                Checkout / install root containing `.venv`
                (default: `vendor/benchmark-runner`, overridable via
                `IENG_PYTEST____FXT__BR____DEF_FXT_CFG__ROOTPATH`).
              - python:
                Optional explicit interpreter; default resolves
                `<rootPath>/.venv/bin/python`.
          - count:
            Number of VMs to create (default: `1`).
          - cliCfg:
            Configuration options for CLIs:
              - ocCfg:
                  Configuration options for `OCcli`.
              - vcCfg:
                  Configuration options for `VirtCtlCLI`.
          - ns:
            Kubernetes Namespace (default: `LPQE__DEF__NAMESPACE`).
          - gstCfg:
            Guest System configurations:
              - usrCrd:
                User Credential to connect to the Guest System.
                  - sshUsr: SSH UserName (default: `'qa-usr'`).
                  - sshKey: Path to the SSH private key (default:
                    `'~/.ssh/openshift-qe.pem'`).

    :yield:
        A `FxtObjVMbyBR` where:
          - vmCreate:   A callable to create VMs through benchmark-runner.
          - vmList:     A shared list of `VM` wrappers managed by this fixture.
                        Calling `fxtVMbyBR.vmCreate()` adds wrappers to this
                        list.
    '''
    fxtCfg = _DEF__FXT_CFG | NamedDict(**getattr(request, 'param', {}))
    vmList: list[VM] = []
    ownVMs: list[tuple[OCcli, str, str]] = []

    try:
        vmCreate = functools.partial(
            VMcreateByBR,
            request,
            FuncParsVMcreateByBR(
                fxtCfgObj=fxtCfg,
                vmList=vmList,
                ownVMs=ownVMs,
            ),
            ocCLI=fxtOCcli,
            virtCtlCLI=fxtVirtCtlCLI,
        )
        vmCreate()
        yield FxtObjVMbyBR(vmCreate=vmCreate, vmList=vmList)
    finally:
        if (vmList or ownVMs):
            VM.CleanUpVMs(vmList, ownVMs)
