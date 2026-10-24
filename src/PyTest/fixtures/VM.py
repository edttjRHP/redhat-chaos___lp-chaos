'''VM creation fixture using OCcli.NewRes and VM wrapper.'''
import base64
from collections.abc import Callable, Generator
import dataclasses
import functools
import logging
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit

import pytest

from fixtures.libs.CommonConstant import LPQE__DEF__NAMESPACE
from libs.CustomTypes import NamedDict
from libs.OpenShift.LP.Virt.VM.VM import VM
from libs.Utils.CmdExec import CmdExec
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
        imgSrc=NamedDict(
            CDI=NamedDict(
                type='AWS-S3',
                credType='K8s',
                credInfo=NamedDict(
                    name='aws-s3--cred',
                    vaultType='LocalFile',
                    vaultInfo={
                        'file--AccKeyId': '/secretMount/secretFileForAccKeyId',
                        'file--AccKeySecret': (
                            '/secretMount/secretFileFoAccKeySecret'
                        ),
                    },
                ),
                url=(
                    'https://s3.us-east-1.amazonaws.com/'
                    'ieng--vm-image--windows--us-east-1/'
                    'win11/dist/win11-25H2--template--base.qcow2'
                ),
            ),
        ),
        usrCrd=NamedDict(
            sshUsr='qa-usr',
            sshKey=NamedDict(prv=None, pub=None),
        ),
    ),
)


@dataclasses.dataclass(frozen=True)
class FuncParsVMcreate:
    fxtCfgObj: NamedDict
    vmList: list[VM] = dataclasses.field(default_factory=list)
    ownVMs: list[tuple[Any, str, str]] = dataclasses.field(
        default_factory=list,
    )
    sshKeyDirs: list[TemporaryDirectory] = dataclasses.field(
        default_factory=list,
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


def _ResolveSSHkeys(
    fxtCfg: NamedDict, sshKeyDirs: list[TemporaryDirectory],
) -> NamedDict:
    sshKey = fxtCfg.gstCfg.usrCrd.sshKey
    if ((sshKey.prv is None) or (sshKey.pub is None)):
        dir = TemporaryDirectory(prefix='fxtVM--ssh--')
        sshKeyDirs.append(dir)
        Path(dir.name).chmod(0o0700)
    if (sshKey.prv is None):
        prvKey = Path(dir.name) / 'id_ed25519'
        pubKey = prvKey.with_suffix('.pub')
        try:
            result = CmdExec().Run(
                ['ssh-keygen', '-q', '-t', 'ed25519', '-N', '',
                 '-f', str(prvKey)],
                input='', spPars={'timeout': 30}, isSecret=True,
            )
            if (not result.success): raise ValueError()
            prvKey.chmod(0o0600)
        except Exception:
            raise ValueError(
                '`gstCfg.usrCrd.sshKey`: Key generation failed.'
            ) from None
    else:
        prvKey = Path(os.path.expandvars(sshKey.prv)).expanduser()
        if not str(prvKey).strip():
            raise ValueError(
                'The `gstCfg.usrCrd.sshKey.prv` can not be empty string.'
            )
        if (sshKey.pub is None):
            pubKey = Path(dir.name) / (prvKey.name + '.pub')
            try:
                result = CmdExec().Run(
                    ['ssh-keygen', '-y', '-P', '', '-f', str(prvKey)],
                    input='', spPars={'timeout': 30}, isSecret=True,
                )
                if (not result.success): raise ValueError()
                pubKeyVal = result.stdout.strip()
                if (not pubKeyVal): raise ValueError()
                pubKey.write_text(pubKeyVal + '\n', encoding='utf-8')
            except Exception:
                raise ValueError(
                    '`gstCfg.usrCrd.sshKey`: Public Key resolution '
                    'failed. Use readable Public or unencrypted '
                    'Private SSH Key files and/or install `ssh-keygen`.'
                ) from None
        else:
            pubKey = Path(os.path.expandvars(sshKey.pub)).expanduser()
    fxtCfg |= NamedDict(gstCfg=NamedDict(usrCrd=NamedDict(
        sshKey=NamedDict(prv=str(prvKey), pub=str(pubKey)),
    )))
    return fxtCfg


def VMcreate(
    request: Any,
    fPars: FuncParsVMcreate,
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
    :param fPars:
        Persistent function paremeters.
          - fxtCfgObj:  Base fixture configuration. See `FxtVM` for details.
          - vmList:
                Shared list to which created `VM` wrappers are appended.
          - ownVMs:     Shared ownership records used during fixture cleanup.
          - sshKeyDirs: Fixture-owned SSH key temporary directories.
    :param ocCLI:       Factory for the `OCcli` used by each VM.
    :param virtCtlCLI:  Factory for the `VirtCtlCLI` used by each VM.
    :param fxtCfgArgs:  Fixture conf. overrides. See `FxtVM` for details.
    :return:
        A list of `VM` instances created or reused by this call.
    '''
    fxtCfg = fPars.fxtCfgObj | NamedDict(fxtCfgArgs)
    if (fxtCfgArgs): fxtCfg = _ResolveSSHkeys(fxtCfg, fPars.sshKeyDirs)

    vmCount = fxtCfg.count
    if (vmCount < 0): raise ValueError('VM count must not be negative.')
    if not vmCount: return []
    ns = fxtCfg.ns
    if (not ns.strip()): raise ValueError('Namespace must not be empty.')
    sshKey = fxtCfg.gstCfg.usrCrd.sshKey
    templPath = request.config.rootpath / 'conf' / fxtCfg.configFile
    newVMs = []
    ctxData = None
    secretYAML = None

    sfxLen = len(str(max(vmCount - 1, 0)))
    for idx in range(vmCount):
        vmName = (
            f'{fxtCfg.namePfx}'
            f"{f'-{idx:0{sfxLen}d}' if (vmCount > 1) else ''}"
        )
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
            if (ctxData is None):
                imgSrc = fxtCfg.gstCfg.imgSrc.CDI
                for (fld, val, validLst) in (
                    (
                        'gstCfg.imgSrc.CDI.type',
                        imgSrc.type,
                        ['AWS-S3'],
                    ),
                    (
                        'gstCfg.imgSrc.CDI.credType',
                        imgSrc.credType,
                        ['K8s', 'None'],
                    ),
                ):
                    if (val not in validLst):
                        raise ValueError(f'{fld} must be one of {validLst!r}.')
                url = imgSrc.url
                try:
                    parsedURL = urlsplit(url)
                    validURL = (
                        (parsedURL.scheme in ('http', 'https')) and
                        bool(parsedURL.hostname)
                    )
                except ValueError: validURL = False
                if (not validURL):
                    raise ValueError(
                        'The `gstCfg.imgSrc.CDI.url` must be an HTTP(S) URL '
                        'with a host.'
                    )
                ctxData = {}
                vmOpts = {}
                if (imgSrc.credType == 'None'):
                    ctxData['vmDataVolumeSource'] = {'http': {'url': url}}
                elif (imgSrc.credType == 'K8s'):
                    credInfo = imgSrc.credInfo
                    vaultInfo = credInfo.vaultInfo
                    for (fld, val, validLst) in (
                        (
                            'gstCfg.imgSrc.CDI.credInfo.vaultType',
                            credInfo.vaultType,
                            ['LocalFile'],
                        ),
                    ):
                        if (val not in validLst):
                            raise ValueError(
                                f'{fld} must be one of {validLst!r}.'
                            )
                    name = credInfo.name
                    if not name.strip():
                        raise ValueError(
                            'The `gstCfg.imgSrc.CDI.credInfo.name` must not '
                            'be empty.'
                        )
                    ctxData['vmDataVolumeSource'] = {
                        's3': {'secretRef': name, 'url': url},
                    }
                    data = {}
                    for (fld, secretFld) in (
                        ('file--AccKeyId', 'accessKeyId'),
                        ('file--AccKeySecret', 'secretKey'),
                    ):
                        path = vaultInfo[fld]
                        if not path.strip():
                            raise ValueError(
                                'The `gstCfg.imgSrc.CDI.credInfo.vaultInfo.'
                                f'{fld}` must not be empty.'
                            )
                        try:
                            value = Path(path).read_text(
                                encoding='utf-8',
                            ).strip()
                        except (OSError, ValueError, UnicodeError):
                            raise ValueError(
                                'The `gstCfg.imgSrc.CDI.credInfo.vaultInfo.'
                                f'{fld}` must name a readable UTF-8 raw file.'
                            ) from None
                        if (not value or (len(value.splitlines()) != 1)):
                            raise ValueError(
                                'The `gstCfg.imgSrc.CDI.credInfo.vaultInfo.'
                                f'{fld}` must contain one non-empty raw value.'
                            )
                        data[secretFld] = base64.b64encode(
                            value.encode(),
                        ).decode('ascii')
                    secretYAML = YAMLcfg.ToYAML({
                        'apiVersion': 'v1',
                        'kind': 'Secret',
                        'metadata': {'name': name, 'namespace': ns},
                        'type': 'Opaque',
                        'data': data,
                    })
                else:
                    raise ValueError('Unsupported image credential mode.')
                try:
                    with Path(sshKey.pub).open(
                        encoding='utf-8', newline='',
                    ) as stream:
                        pubKeyVal = stream.read()
                    if (not pubKeyVal.strip()): raise ValueError()
                except Exception:
                    raise ValueError(
                        '`gstCfg.usrCrd.sshKey`: Public Key resolution '
                        'failed. Use readable public key files.'
                    ) from None
                ctxData.update({
                    'vmNS': ns,
                    'sshPublicKeyB64': base64.b64encode(
                        pubKeyVal.encode()
                    ).decode('ascii'),
                })
            yamlStr = YAMLcfg.Render(templPath, {**ctxData, 'vmName': vmName})
            if not yamlStr.strip():
                raise ValueError('configFile must render nonempty VM YAML.')
            if (secretYAML is not None):
                try:
                    (_, secretResult) = oc.NewRes(
                        secretYAML, waitCond=None, isSecret=True,
                    )
                    if (not secretResult.success):
                        raise RuntimeError('Secret apply failed.')
                except Exception:
                    raise RuntimeError(
                        '`gstCfg.imgSrc.CDI.credInfo`: Secret apply failed. '
                        'Check Namespace and Secret permissions.'
                    ) from None
                secretYAML = None
            (phase, ocRes) = oc.NewRes(
                yamlStr, waitTime=fxtCfg.timeout, **vmOpts,
            )
            applied = (
                (phase.name == 'WAITED') or
                ((phase.name == 'APPLIED') and ocRes.success)
            )
            (lookupOK, curUID, lookupErr) = VM.GetVMuid(oc, vmName)
            if (applied and not prevUID):
                if lookupOK:
                    if curUID: fPars.ownVMs.append((oc, vmName, curUID))
                else:
                    raise RuntimeError(
                        'Could not establish ownership of VM '
                        f'`{vmName}` after '
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
            **(fxtCfg.gstCfg.usrCrd + {'sshKey': sshKey.prv}),
        ))
        fPars.vmList.append(newVMs[-1])

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
                  - sshKey:
                    SSH Key file paths:
                      - prv:
                        Private SSH Key. When set to `None`, generate an
                        ED25519 Key pair. The `.pub` will be set to the
                        corresponding Public SSH Key.
                      - pub:
                        Public SSH Key. When set to `None`, derive it from the
                        supplied `.prv`.
                    When reusing VMs (reuseExisting is `True`), the appropriate
                    `.prv` must be supplied.
              - imgSrc:
                Complete YAML schema:
                  - Main:
                    ```yaml
                    CDI:
                      type: AWS-S3
                      credType: None
                      url:
                        "https://s3.us-east-1.amazonaws.com/\\
                        ieng--vm-image--windows--us-east-1/\\
                        win11/dist/win11-25H2--template--base.qcow2"
                    ---
                    CDI:
                      type: AWS-S3
                      credType: K8s
                      credInfo: {}  # See `SubSchema--credInfo`.
                      url:
                        "https://s3.us-east-1.amazonaws.com/\\
                        ieng--vm-image--windows--us-east-1/\\
                        win11/dist/win11-25H2--template--base.qcow2"
                    ```
                  - SubSchema--credInfo:
                    ```yaml
                    # credType: [K8s]
                    name: aws-s3--cred
                    vaultType: LocalFile
                    vaultInfo:
                      file--AccKeyId: /secretMount/secretFileForAccKeyId
                      file--AccKeySecret: /secretMount/secretFileFoAccKeySecret
                    ---
                    # To be implemented.
                    # credType: [K8s]
                    name: aws-s3--cred
                    vaultType: BitWarden
                    vaultInfo:
                      name: ...bwNoteName...
                      subField: ...bwSubFieldName...
                    ```

    :return:
        A `FxtObjVM` where:
          - vmCreate:   A callable to create or reuse VMs.
          - vmList:     A shared list of `VM` wrappers managed by this fixture.
                        Calling `fxtVM.vmCreate()` adds wrappers to this list.
    '''
    fxtCfg = _DEF__FXT_CFG | NamedDict(**getattr(request, 'param', {}))
    vmList: list[VM] = []
    ownVMs: list[tuple[Any, str, str]] = []
    sshKeyDirs: list[TemporaryDirectory] = []

    try:
        fxtCfg = _ResolveSSHkeys(fxtCfg, sshKeyDirs)
        try:
            with Path(fxtCfg.gstCfg.usrCrd.sshKey.pub).open(
                encoding='utf-8', newline='',
            ) as stream:
                pubKeyVal = stream.read()
            if (not pubKeyVal.strip()): raise ValueError()
        except Exception:
            raise ValueError(
                '`gstCfg.usrCrd.sshKey`: Public Key resolution failed. '
                'Use readable public or unencrypted private key files '
                'and install `ssh-keygen`.'
            ) from None
        vmCreate = functools.partial(
            VMcreate,
            request,
            FuncParsVMcreate(
                fxtCfgObj=fxtCfg,
                vmList=vmList,
                ownVMs=ownVMs,
                sshKeyDirs=sshKeyDirs,
            ),
            ocCLI=fxtOCcli,
            virtCtlCLI=fxtVirtCtlCLI,
        )
        vmCreate()
        yield FxtObjVM(vmCreate=vmCreate, vmList=vmList)
    finally:
        try:
            if vmList or ownVMs: VM.CleanUpVMs(vmList, ownVMs)
        finally:
            primExc = sys.exc_info()[1]
            clnFail = False
            for dir in sshKeyDirs:
                try: dir.cleanup()
                except Exception:
                    clnFail = True
                    logs.error(
                        'Could not clean up fixture SSH key directory `%s`.',
                        dir.name,
                    )
            if (clnFail and primExc is None):
                raise RuntimeError(
                    'Could not clean up fixture SSH key files.',
                ) from None
