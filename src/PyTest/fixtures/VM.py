'''VM creation fixture using OCcli.NewRes and VM wrapper.'''
from collections.abc import Callable, Generator
import dataclasses
import functools
import logging
from typing import Any

import pytest

from fixtures.libs.CommonConstant import LPQE__DEF__NAMESPACE
from libs.CustomTypes import NamedDict
from libs.OpenShift.LP.Virt.VM.VM import VM
from libs.Utils.YAML import YAMLcfg


logs = logging.getLogger()
_DEF__FXT_CFG = NamedDict(
    count=1,
    reuseExisting=False,
    cliCfg=NamedDict(
        ocCfg={},
        vcCfg={},
    ),
    ns=LPQE__DEF__NAMESPACE,
    configFile='config--vm--template.yaml',
    namePfx='lpqe-test',
    timeout='15m',
    gstCfg=NamedDict(
        usrCrd=NamedDict(
            sshUsr='qa-usr',
            sshKey='~/.ssh/openshift-qe.pem',
        ),
    ),
)


@dataclasses.dataclass(frozen=True)
class FxtObjVM:
    '''
    Fixture Object.

    Can be sub-classed to add fields. Sub-classes must also be decorated with
    `@dataclass(frozen=True)`.
    '''
    vmCreate: Callable[..., list[VM]]
    vmList: list[VM]


def VMcreate(
    request: Any,
    vmList: list[VM],
    ownVMs: list[tuple[Any, str, str]],
    *,
    ocCLI: Callable[..., Any],
    virtCtlCLI: Callable[..., Any],
    **fxtCfgArgs: Any,
) -> list[VM]:
    '''
    Create VMs from a Jinja2 template and return `VM` wrapper objects.

    :param request:
        Pytest `request` built-in fixture.
        See `FxtVM()` for fixture parametrization details.
    :param vmList:
        Shared list to which created `VM` wrappers are appended.
    :param ownVMs:      Shared ownership records used during fixture cleanup.
    :param ocCLI:       Factory for the `OCcli` used by each VM.
    :param virtCtlCLI:  Factory for the `VirtCtlCLI` used by each VM.
    :param fxtCfgArgs:  Fixture configuration. See `FxtVM` for details.
    :return:
        A list of `VM` instances created or reused by this call.
    '''
    fxtCfg = _DEF__FXT_CFG | NamedDict(fxtCfgArgs)

    vmCount = fxtCfg.count
    if (vmCount < 0): raise ValueError('VM count must not be negative.')
    if not vmCount: return []
    ns = fxtCfg.ns
    templPath = request.config.rootpath / 'conf' / fxtCfg.configFile
    newVMs = []

    sfxLen = len(str(max(vmCount - 1, 0)))
    for idx in range(vmCount):
        vmName = (
            f'{fxtCfg.namePfx}'
            f"{f'-{idx:0{sfxLen}d}' if (vmCount > 1) else ''}"
        )
        ctxData = {'vmName': vmName}
        oc = ocCLI(ns=ns, **fxtCfg.cliCfg.ocCfg)

        (lookupOK, prevUID, lookupErr) = VM.GetVMuid(oc, vmName)
        if not lookupOK:
            raise RuntimeError(
                f'Could not establish whether VM `{vmName}` already '
                f'exists: {lookupErr}'
            )
        if prevUID:
            if not fxtCfg.reuseExisting:
                raise RuntimeError(
                    f'VM `{vmName}` already exists (set `reuseExisting` '
                    'to `True` to reuse it).'
                )
        else:
            yamlStr = YAMLcfg.Render(templPath, ctxData)
            phase, ocRes = oc.NewRes(yamlStr, waitTime=fxtCfg.timeout)
            applied = (
                (phase.name == 'WAITED') or
                ((phase.name == 'APPLIED') and ocRes.success)
            )
            (lookupOK, curUID, lookupErr) = VM.GetVMuid(oc, vmName)
            if (applied and not prevUID):
                if lookupOK:
                    if curUID: ownVMs.append((oc, vmName, curUID))
                else:
                    raise RuntimeError(
                        f'Could not establish ownership of VM `{vmName}` after '
                        f'apply: {lookupErr}'
                    )
            if ocRes.success:
                logs.info(f'VM `{vmName}` created (phase: `{phase.name}`).')
            else:
                raise RuntimeError(
                    f'VM `{vmName}` creation failed at `{phase.name}`: '
                    f'{ocRes.stderr}'
                )

        newVMs.append(VM(
            name=vmName,
            ns=ns,
            oc=oc,
            virtCtl=virtCtlCLI(ns=ns, **fxtCfg.cliCfg.vcCfg),
            **fxtCfg.gstCfg.usrCrd,
        ))
        vmList.append(newVMs[-1])

    return newVMs


@pytest.fixture(name='fxtVM', scope='function')
def FxtVM(
    request: Any,
    fxtOCcli: Callable[..., Any],
    fxtVirtCtlCLI: Callable[..., Any],
) -> Generator[FxtObjVM, None, None]:
    '''
    Create initial VMs and expose their shared factory and aggregate list.

    Configure via indirect parametrize with `dict`:
        @pytest.mark.parametrize(
            'fxtVM,',
            [({'count': 2, 'ns': 'bsod-test'},)],
            indirect=True,
        )
        @pytest.mark.parametrize('fxtVM,', [({'count': 5},)], indirect=True)

        Fixture Configuration:
          - count:
            Number of VMs to create (default: `1`).
          - reuseExisting:
            Reuse VMs with generated names that already exist instead of
            failing on a name collision (default: `False`).
            WARNING: Reused VMs are NOT validated against their VM
                     specification, guest system image compatibility, or guest
                     credentials. Reused VMs are NOT deleted during teardown.
          - cliCfg:
            Configuration options for CLIs:
              - ocCfg:  Configuration options for `OCcli`.
              - vcCfg:  Configuration options for `VirtCtlCLI`.
          - ns:
            Kubernetes Namespace (default: `LPQE__DEF__NAMESPACE`).
          - configFile:
            Template configuration file under `conf/`
            (default: `'config--vm--template.yaml'`).
          - namePfx:
            Prefix for generated VM names (default: `'lpqe-test'`).
          - timeout:
            Wait timeout for VM creation (default: `'15m'`).
          - gstCfg:
            Guest System configurations:
              - usrCrd:
                User Credential to connect to the Guest System.
                  - sshUsr: SSH UserName (default: `'qa-usr'`).
                  - sshKey: Path to the SSH private key
                            (default: `'~/.ssh/openshift-qe.pem'`).

    :return:
        A `FxtObjVM` where:
          - vmCreate:   A callable to create or reuse VMs.
          - vmList:     A shared list of `VM` wrappers managed by this fixture.
                        Calling `fxtVM.vmCreate()` adds wrappers to this list.
    '''
    vmList: list[VM] = []
    ownVMs: list[tuple[Any, str, str]] = []
    vmCreate = functools.partial(
        VMcreate,
        request,
        vmList,
        ownVMs,
        ocCLI=fxtOCcli,
        virtCtlCLI=fxtVirtCtlCLI,
    )

    try:
        vmCreate(**getattr(request, 'param', {}))
        yield FxtObjVM(vmCreate=vmCreate, vmList=vmList)
    finally:
        if vmList or ownVMs:
            VM.CleanUpVMs(vmList, ownVMs)
