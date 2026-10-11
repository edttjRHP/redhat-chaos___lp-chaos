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


logs = logging.getLogger()
_DEF__FXT_CFG = NamedDict(
    workloadName='windows',
    workloadKind='vm',
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


@dataclasses.dataclass(frozen=True)
class FxtObjVMbyBR(FxtObjVM):
    '''Benchmark-runner VM fixture object.'''


def VMcreateByBR(
    request: Any,
    vmList: list[VM],
    ownVMs: list[tuple[Any, str, str]],
    *,
    ocCLI: Callable[..., Any],
    virtCtlCLI: Callable[..., Any],
    **fxtCfgArgs: Any,
) -> list[VM]:
    '''
    Provision VMs via benchmark-runner and return `VM` wrapper objects.

    Calls `benchmark_runner.main.main.main()` with the required environment
    variables. VMs are running when returned.

    :param request:
        Pytest `request` built-in fixture.
        See `FxtVMbyBR()` for fixture parametrization details.
    :param vmList:
        Shared list to which created `VM` wrappers are appended.
    :param ownVMs:      Shared ownership records used during fixture cleanup.
    :param ocCLI:       Factory for the `OCcli` used by each VM.
    :param virtCtlCLI:  Factory for the `VirtCtlCLI` used by each VM.
    :param fxtCfgArgs:  Fixture configuration. See `FxtVMbyBR` for details.
    :return:            A list of `VM` instances created by this call.
    '''
    fxtCfg = _DEF__FXT_CFG | NamedDict(fxtCfgArgs)

    vmCount = fxtCfg.count
    if (vmCount < 0): raise ValueError('VM count must not be negative.')
    if not vmCount: return []

    # Import lazily so the TCs are still collected if these modules are absent.
    from benchmark_runner.main.environment_variables import (
        environment_variables,
    )
    from benchmark_runner.main.main import main as brMain
    from benchmark_runner.main.temporary_environment_variables import (
        TemporaryEnvironmentVariables,
    )

    ns = fxtCfg.ns
    isScale = vmCount > 1
    newVMs = []

    logs.info(f'Creating {vmCount} VM(s) via benchmark-runner')
    with TemporaryEnvironmentVariables():
        env = environment_variables.environment_variables_dict
        workload = f'{fxtCfg.workloadName}_{fxtCfg.workloadKind}'
        env['workload'] = workload
        env['run_type'] = 'test_ci'
        env['namespace'] = ns
        env['delete_all'] = False
        env['run_artifacts_path'] = (
            env.get('run_artifacts_path') or tempfile.mkdtemp()
        )
        invocationUUID = str(uuid4())
        truncUUID = invocationUUID.split('-')[0]
        env['uuid'] = invocationUUID
        env['trunc_uuid'] = truncUUID
        if isScale:
            env['scale'] = str(vmCount)
            env['scale_nodes'] = str([os.environ.get('WORKER_NODE')])
        else:
            env['scale'] = ''
            env['scale_nodes'] = ''

        namePfx = workload.removesuffix('_scale').replace('_', '-')
        brVMs = []
        for idx in range(vmCount):
            vmName = f'{namePfx}-{truncUUID}'
            if isScale: vmName += f'-{idx}'
            oc = ocCLI(ns=ns, **fxtCfg.cliCfg.ocCfg)
            (lookupOK, prevUID, lookupErr) = VM.GetVMuid(oc, vmName)
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

        try: brMain()
        finally:
            primExc = sys.exc_info()[1]
            lookupFail = []
            missingVMs = []
            for (vmName, oc) in brVMs:
                try:
                    (lookupOK, curUID, lookupErr) = VM.GetVMuid(oc, vmName)
                    if not lookupOK:
                        lookupFail.append(f'VM `{vmName}`: {lookupErr}')
                        continue
                    if not curUID:
                        missingVMs.append(vmName)
                        continue
                    ownVMs.append((oc, vmName, curUID))
                except Exception as ex:
                    lookupFail.append(f'VM `{vmName}`: {ex}')

            i = 2
            while (i):
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

        sshKey = Path(env['run_artifacts_path'], 'ssh', 'vm_key')
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
            vmList.append(newVMs[-1])

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
          - workloadName:
            Template directory name inside benchmark-runner
            (default: `'windows'`).
          - workloadKind:
            Resource kind suffix (default: `'vm'`).
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

    :return:
        A `FxtObjVMbyBR` where:
          - vmCreate:   A callable to create VMs through benchmark-runner.
          - vmList:     A shared list of `VM` wrappers managed by this fixture.
                        Calling `fxtVMbyBR.vmCreate()` adds wrappers to this
                        list.
    '''
    vmList: list[VM] = []
    ownVMs: list[tuple[Any, str, str]] = []
    vmCreate = functools.partial(
        VMcreateByBR,
        request,
        vmList,
        ownVMs,
        ocCLI=fxtOCcli,
        virtCtlCLI=fxtVirtCtlCLI,
    )

    try:
        vmCreate(**getattr(request, 'param', {}))
        yield FxtObjVMbyBR(vmCreate=vmCreate, vmList=vmList)
    finally:
        if vmList or ownVMs:
            VM.CleanUpVMs(vmList, ownVMs)
