'''VM creation fixture using resource handlers and runtime VM wrappers.'''
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
from libs.OpenShift.LP.Virt.Resources import VirtualMachine
from libs.OpenShift.LP.Virt.VM.VM import VM
from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.OpenShift.OCP.Resources import (
    OwnInf, ResCfg,
    Namespace, Secret,
)
from libs.Utils.CmdExec import CmdExec
from libs.Utils.YAML import YAMLcfg


logs = logging.getLogger()
_DEF__FXT_CFG = NamedDict(
    kCfg=(
        os.environ.get('KUBECONFIG') or
        os.path.expanduser('~/.kube/config')
    ),
    # The `--kubeconfig` option will be overridden by the above `.kCfg`.
    cliCfg=NamedDict(
        ocCfg={},
        vcCfg={},
    ),
    ns=LPQE__DEF__NAMESPACE,
    fxtInf=NamedDict(
        vmCfg=NamedDict(
            cfgTmpl='cfg-template--vm.yaml',
            ctxData={},
            namePfx='lpqe-test',
            count=1,
            startVM=True,
            reuseExisting=False,
            timeout=NamedDict(
                vmCreate='15m',
                dvImport='30m',
                vmStart='15m',
                vmRemove='3m',
            ),
        ),
        gstCfg=NamedDict(
            imgSrc=NamedDict(
                CDI=NamedDict(
                    type='AWS-S3',
                    credType='K8s',
                    credInfo=NamedDict(
                        name='aws-s3--cred',
                        vaultType='LocalFile',
                        vaultInfo={
                            'file--AccKeyId':
                                '/secretMount/secretFileForAccKeyId',
                            'file--AccKeySecret': (
                                '/secretMount/secretFileFoAccKeySecret'
                            ),
                        },
                    ),
                    url=os.getenv(
                        (
                            'IENG_PYTEST____FXT__VM____'
                            'DEF_FXT_CFG__GSTCFG_IMGSRC_CDI_URL'
                        ),
                        (
                            'https://s3.us-east-1.amazonaws.com/'
                            'ieng--vm-image--windows--us-east-1/'
                            'win11/dist/win11-25H2--template--base.qcow2'
                        ),
                    ),
                ),
            ),
            usrCrd=NamedDict(
                sshUsr='qa-usr',
                sshKey=NamedDict(prv=None, pub=None),
            ),
        ),
    ),
    testPars=NamedDict(),
)


@dataclasses.dataclass(frozen=True)
class FuncParsVMcreate:
    fxtCfgObj: NamedDict
    vmList: list[VM] = dataclasses.field(default_factory=list)
    ownResTrk: NamedDict[
        str, list[tuple[OCcli, str, str]]
    ] = dataclasses.field(
        default_factory=lambda: NamedDict(NSs=[], Secrets=[], VMs=[]),
    )   # List of `(oc, resRef, resUID)`.
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
    fxtCfg: NamedDict,
    sshKeyDirs: list[TemporaryDirectory],
) -> NamedDict:
    '''
    Resolve supplied SSH paths or generate missing keys.

    :param fxtCfg:  Fixture configuration containing guest SSH key paths.
    :param sshKeyDirs:
        Shared temporary directories retaining generated keys or a derived
        public key until fixture teardown.

    :return:    Updated `NamedDict` containing resolved key paths.

    :raises ValueError: Key generation or public-key resolution fails.
    :raises OSError:    Temporary directory creation or permission setup fails.
    '''
    sshKey = fxtCfg.fxtInf.gstCfg.usrCrd.sshKey
    if (sshKey.prv is not None):
        prvKeyStr = os.path.expandvars(sshKey.prv)
        if not prvKeyStr.strip():
            raise ValueError(
                'The `fxtInf.gstCfg.usrCrd.sshKey.prv` can not be empty '
                'string.'
            )
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
                input='', isSecret=True, spPars={'timeout': 30},
            )
            if not result.success: raise ValueError()
            prvKey.chmod(0o0600)
        except Exception:
            raise ValueError(
                '`fxtInf.gstCfg.usrCrd.sshKey`: Key generation failed.'
            ) from None
    else:
        prvKey = Path(prvKeyStr).expanduser()
        if (sshKey.pub is None):
            pubKey = Path(dir.name) / (prvKey.name + '.pub')
            try:
                result = CmdExec().Run(
                    ['ssh-keygen', '-y', '-P', '', '-f', str(prvKey)],
                    input='', isSecret=True, spPars={'timeout': 30},
                )
                if not result.success: raise ValueError()
                pubKeyVal = result.stdout.strip()
                if not pubKeyVal: raise ValueError()
                pubKey.write_text((pubKeyVal + '\n'), encoding='utf-8')
            except Exception:
                raise ValueError(
                    '`fxtInf.gstCfg.usrCrd.sshKey`: Public Key resolution '
                    'failed. Use readable Public or unencrypted '
                    'Private SSH Key files and/or install `ssh-keygen`.'
                ) from None
        else:
            pubKey = Path(os.path.expandvars(sshKey.pub)).expanduser()
    fxtCfg |= NamedDict(fxtInf=NamedDict(gstCfg=NamedDict(usrCrd=NamedDict(
        sshKey=NamedDict(prv=str(prvKey), pub=str(pubKey)),
    ))))
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

    Additional batches share fixture teardown and append to `vmList`. Guest SSH
    connections are lazy. Choose distinct `namePfx` values unless
    `reuseExisting` is intended. Reused VMs remain unowned.

    Register created resources and VM wrappers before waiting for DV imports.
    If an import fails, fixture teardown still attempts cleanup in order, i.e.
    VMs, then Secrets, then Namespaces, even if an earlier cleanup fails.
    Cleanup is best effort and can leave left over resources.

    :param request:
        Pytest `request` built-in fixture.
        See `FxtVM()` for fixture parametrization details.
    :param fPars:
        Persistent function parameters.
          - fxtCfgObj:  Base fixture configuration. See `FxtVM()` for details.
          - vmList:
                Shared list to which created `VM` wrappers are appended.
          - ownResTrk:
                Shared ownership OpenShift Resource records for fixture
                cleanup. Each group below holds `(oc, resRef, UID)`
                tuples, recording the CLI, Resource Reference and UID.
                  - NSs:        Namespace Resources.
                  - Secrets:    Secret Resources.
                  - VMs:        VirtualMachine Resources.
          - sshKeyDirs: Fixture-owned SSH key temporary directories.
    :param ocCLI:       Factory for the `OCcli` used by each VM.
    :param virtCtlCLI:  Factory for the `VirtCtlCLI` used by each VM.
    :param fxtCfgArgs:  Fixture conf. overrides. See `FxtVM()` for details.

    :return:
        A list of `VM` instances created or reused by this call.

    :raises ValueError:    Invalid VM, image or credential configuration.
    :raises RuntimeError:
        Resource resolution, VM provisioning and/or startup failed.
    '''
    fxtCfg = fPars.fxtCfgObj | NamedDict(fxtCfgArgs)
    if fxtCfgArgs:
        keyArgs = fxtCfgArgs.get('fxtInf', {}).get('gstCfg', {}).get(
            'usrCrd', {},
        ).get('sshKey', {})
        if (
            ('prv' in keyArgs) and
            ('pub' not in keyArgs) and
            (keyArgs['prv'] != fPars.fxtCfgObj.fxtInf.gstCfg.usrCrd.sshKey.prv)
        ):
            fxtCfg |= NamedDict(fxtInf=NamedDict(gstCfg=NamedDict(
                usrCrd=NamedDict(sshKey=NamedDict(pub=None))
            )))
        fxtCfg = _ResolveSSHkeys(fxtCfg, fPars.sshKeyDirs)

    vmCount = fxtCfg.fxtInf.vmCfg.count
    if (vmCount < 0): raise ValueError('VM count must not be negative.')
    if not vmCount: return []
    ns = fxtCfg.ns
    if not ns.strip(): raise ValueError('Namespace must not be empty.')
    sshKey = fxtCfg.fxtInf.gstCfg.usrCrd.sshKey
    templPath = (
        request.config.rootpath / 'conf/VM' / fxtCfg.fxtInf.vmCfg.cfgTmpl
    ).resolve()
    ctxData = None
    vmResLst = []
    newVMs = []

    sfxLen = len(str(max((vmCount - 1), 0)))
    for idx in range(vmCount):
        vmName = (
            f'{fxtCfg.fxtInf.vmCfg.namePfx}'
            f"{f'-{idx:0{sfxLen}d}' if (vmCount > 1) else ''}"
        )
        oc = ocCLI(ns=ns, **{**fxtCfg.cliCfg.ocCfg, 'kubeconfig': fxtCfg.kCfg})
        if (idx == 0):
            Namespace.Create(
                oc,
                ResCfg(meta=NamedDict(name=ns)),
                OwnInf(owned=fPars.ownResTrk.NSs),
            )

        (lookupOK, prevUID, lookupErr) = OCcli.GetResUID(
            oc,
            f'VirtualMachine/{vmName}',
        )
        if not lookupOK:
            raise RuntimeError(
                f'Could not establish whether VM `{vmName}` already '
                f'exists: {lookupErr}'
            )
        if prevUID:
            if not fxtCfg.fxtInf.vmCfg.reuseExisting:
                raise RuntimeError(
                    f'VM `{vmName}` already exists (set '
                    '`fxtInf.vmCfg.reuseExisting` to `True` to reuse it).'
                )
            resource = {}
        else:
            if not ctxData:
                imgSrc = fxtCfg.fxtInf.gstCfg.imgSrc.CDI
                for (fld, val, validLst) in (
                    (
                        'fxtInf.gstCfg.imgSrc.CDI.type',
                        imgSrc.type,
                        ['AWS-S3'],
                    ),
                    (
                        'fxtInf.gstCfg.imgSrc.CDI.credType',
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
                if not validURL:
                    raise ValueError(
                        'The `fxtInf.gstCfg.imgSrc.CDI.url` must be an '
                        'HTTP(S) URL with a host.'
                    )
                ctxData = fxtCfg.fxtInf.vmCfg.ctxData
                if (imgSrc.credType == 'None'):
                    ctxData['vmDataVolumeSource'] = {'http': {'url': url}}
                elif (imgSrc.credType == 'K8s'):
                    credInfo = imgSrc.credInfo
                    vaultInfo = credInfo.vaultInfo
                    for (fld, val, validLst) in (
                        (
                            'fxtInf.gstCfg.imgSrc.CDI.credInfo.vaultType',
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
                            'The `fxtInf.gstCfg.imgSrc.CDI.credInfo.name` '
                            'must not be empty.'
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
                                'The `fxtInf.gstCfg.imgSrc.CDI.credInfo.'
                                f'vaultInfo.{fld}` must not be empty.'
                            )
                        try:
                            value = Path(path).read_text(
                                encoding='utf-8',
                            ).strip()
                        except (OSError, ValueError, UnicodeError):
                            raise ValueError(
                                'The `fxtInf.gstCfg.imgSrc.CDI.credInfo.'
                                f'vaultInfo.{fld}` must name a readable UTF-8 '
                                'raw file.'
                            ) from None
                        if (not value or (len(value.splitlines()) != 1)):
                            raise ValueError(
                                'The `fxtInf.gstCfg.imgSrc.CDI.credInfo.'
                                f'vaultInfo.{fld}` must contain one non-empty '
                                'raw value.'
                            )
                        data[secretFld] = base64.b64encode(
                            value.encode(),
                        ).decode('ascii')
                    step = 'Creation'
                    try:
                        Secret.Create(
                            oc,
                            ResCfg(
                                meta=NamedDict(namespace=ns, name=name),
                                spec=NamedDict(type='Opaque', data=data),
                            ),
                            OwnInf(owned=fPars.ownResTrk.Secrets),
                        )
                    except Secret.SpecMismatch:
                        raise RuntimeError(
                            f'Existing image Secret `{ns}/{name}` has '
                            'different data. Refusing to overwrite it.'
                        ) from None
                    except Exception:
                        raise RuntimeError(
                            f'Could not prepare image Secret `{ns}/{name}`. '
                            f'Step: {step}.'
                        ) from None
                else:
                    raise ValueError('Unsupported image credential mode.')
                try:
                    with Path(sshKey.pub).open(
                        encoding='utf-8', newline='',
                    ) as stream:
                        pubKeyVal = stream.read()
                    if not pubKeyVal.strip(): raise ValueError()
                except Exception:
                    raise ValueError(
                        '`fxtInf.gstCfg.usrCrd.sshKey`: Public Key resolution '
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
                raise ValueError(
                    'fxtInf.vmCfg.cfgTmpl must render nonempty VM YAML.'
                )
            resource = VirtualMachine.Create(
                oc,
                ResCfg(yaml=yamlStr),
                OwnInf(owned=fPars.ownResTrk.VMs),
                optsNewRes={'waitTime': fxtCfg.fxtInf.vmCfg.timeout.vmCreate},
            )
            logs.info('VM `%s` resolved.', vmName)

        newVMs.append(VM(
            name=vmName,
            ns=ns,
            oc=oc,
            virtCtl=virtCtlCLI(
                ns=ns, **{**fxtCfg.cliCfg.vcCfg, 'kubeconfig': fxtCfg.kCfg},
            ),
            **(fxtCfg.fxtInf.gstCfg.usrCrd + {'sshKey': sshKey.prv}),
        ))
        fPars.vmList.append(newVMs[-1])
        vmResLst.append((oc, resource, newVMs[-1]))

    for (oc, resource, vm) in vmResLst:
        volumes = resource.get('spec', {}).get('template', {}).get(
            'spec', {},
        ).get('volumes', [])
        for dvName in dict.fromkeys(
            volume['dataVolume']['name'] for volume in volumes
            if ('dataVolume' in volume)
        ):
            result = oc.Run(
                'wait',
                f'DataVolume/{dvName}',
                **{'for': 'jsonpath={.status.phase}=Succeeded'},
                timeout=fxtCfg.fxtInf.vmCfg.timeout.dvImport,
            )
            if not result.success:
                raise RuntimeError(
                    f'DataVolume `{dvName}` import failed: {result.stderr}'
                )

        if fxtCfg.fxtInf.vmCfg.startVM:
            if not vm.Start(timeout=fxtCfg.fxtInf.vmCfg.timeout.vmStart):
                raise RuntimeError(
                    f'VM `{vm.namespace}/{vm.name}` failed to start.'
                )

    return newVMs


@pytest.fixture(name='fxtVM', scope='function')
def FxtVM(
    request: Any,
    fxtOCcli: Callable[..., Any],
    fxtVirtCtlCLI: Callable[..., Any],
) -> Generator[FxtObjVM, None, None]:
    '''
    Create initial VMs and expose their shared factory and aggregate list.

    VMs live for one test. Startup waits for it to be Ready, not guaranteeing
    SSH connection to the Guest OS. The used VM Image Template and VM Template
    provide guest authorization. Reused VMs may still be started but are not
    owned.

    Teardown closes connections and attempts deletion of owned VMs, Secrets,
    and Namespaces (beware, namespace deletion removes ALL of its contents)

    Pre-existing resources stay unowned. Generated keys are temporary. The
    `vmCreate(...)` returns only its batch and appends it to `vmList`.

    This is the complete Fixture Configuration reference (see
    `docs/guide--vm.md` for concise usage and custom YAML templates).
    Configure via indirect parametrize with `dict`:
        @pytest.mark.parametrize(
            'fxtVM,',
            [({'fxtInf': NamedDict(vmCfg=NamedDict(count=2)),
               'ns': 'bsod-test'},)],
            indirect=True,
        )
        @pytest.mark.parametrize(
            'fxtVM,',
            [({'fxtInf': NamedDict(vmCfg=NamedDict(count=5))},)],
            indirect=True,
        )

        Fixture Configuration:
          - kCfg:
            The `KUBECONFIG` file for OpenShift Cluster target.
          - cliCfg:
            Configuration options for CLIs:
              - ocCfg:  Configuration options for `OCcli`.
              - vcCfg:  Configuration options for `VirtCtlCLI`.
          - ns:
            Kubernetes Namespace (default:
            `.libs.CommonConstant.LPQE__DEF__NAMESPACE`).
          - fxtInf:
            Fixture settings:
              - vmCfg:
                VM manifest template and provisioning configurations:
                  - cfgTmpl:
                    Template path, absolute or relative to `conf/VM/` under
                    the PyTest root (default: `'cfg-template--vm.yaml'`).
                  - ctxData:
                    A `dict` context for the VM Manifest template (Jinja2). See
                    the used VM Manifest template for content. Some of the
                    Jinja2 variables will always be set from other information,
                    such as, `vmName`, `vmNS`, `vmDataVolumeSource`, and
                    `sshPublicKeyB64`.
                  - namePfx:
                    Prefix for generated VM names (default: `'lpqe-test'`).
                  - count:      Number of VMs to create (default: `1`).
                  - startVM:    Start each VM (default: `True`).
                  - reuseExisting:
                    Reuse VMs with generated names that already exist instead
                    of failing on a name collision (default: `False`).
                    WARNING:
                        Reused VMs are NOT validated against their VM
                        specification, guest system image compatibility, or
                        guest credentials. Reused VMs are NOT deleted during
                        teardown.
                  - timeout:
                      - vmCreate:
                        Wait timeout for VM creation (default: `'15m'`).
                      - dvImport:
                        Wait timeout for DV import, for each referenced
                        DataVolume in a new VM (default: '30m').
                      - vmStart:
                        The `timeout` par. for `VM.Start()` (default: `'15m'`).
                      - vmRemove:
                        VM deletion wait at fixture teardown (default: `'3m'`).
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
                            Public SSH Key. When set to `None`, derive it from
                            the supplied `.prv`.
                        When reusing VMs (reuseExisting is `True`), the
                        appropriate `.prv` must be supplied.
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
                          file--AccKeyId:
                            /secretMount/secretFileForAccKeyId
                          file--AccKeySecret:
                            /secretMount/secretFileFoAccKeySecret
                        ---
                        # To be implemented.
                        # credType: [K8s]
                        name: aws-s3--cred
                        vaultType: BitWarden
                        vaultInfo:
                          name: ...bwNoteName...
                          subField: ...bwSubFieldName...
                        ```
          - testPars:
            Parameters to be passed to test.

    :yield:
        A `FxtObjVM` where:
          - vmCreate:   A callable to create or reuse VMs.
          - vmList:     A shared list of `VM` wrappers managed by this fixture.
                        Calling `fxtVM.vmCreate()` adds wrappers to this list.
    '''
    fxtCfg = _DEF__FXT_CFG | NamedDict(**getattr(request, 'param', {}))
    vmList = []
    ownResTrk = NamedDict(NSs=[], Secrets=[], VMs=[])
    sshKeyDirs = []

    try:
        fxtCfg = _ResolveSSHkeys(fxtCfg, sshKeyDirs)
        try:
            with Path(fxtCfg.fxtInf.gstCfg.usrCrd.sshKey.pub).open(
                encoding='utf-8', newline='',
            ) as stream:
                pubKeyVal = stream.read()
            if not pubKeyVal.strip(): raise ValueError()
        except Exception:
            raise ValueError(
                '`fxtInf.gstCfg.usrCrd.sshKey`: Public Key resolution failed. '
                'Use readable public or unencrypted private key files and '
                'install `ssh-keygen`.'
            ) from None
        vmCreate = functools.partial(
            VMcreate,
            request,
            FuncParsVMcreate(
                fxtCfgObj=fxtCfg,
                vmList=vmList,
                ownResTrk=ownResTrk,
                sshKeyDirs=sshKeyDirs,
            ),
            ocCLI=fxtOCcli,
            virtCtlCLI=fxtVirtCtlCLI,
        )
        vmCreate()
        yield FxtObjVM(vmCreate=vmCreate, vmList=vmList)
    finally:
        primExc = sys.exc_info()[1]
        clnFail = False
        clnExc = None
        try:
            if (vmList or ownResTrk.VMs):
                VM.CleanUpVMs(
                    vmList,
                    ownResTrk.VMs,
                    optsCleanUpRes={'optsDelRes': {
                        'waitTime': fxtCfg.fxtInf.vmCfg.timeout.vmRemove,
                    }},
                )
        except Exception as error:
            clnFail = True
            clnExc = error
            logs.error('Could not clean up fixture VMs: %s', error)
        finally:
            for owned in (ownResTrk.Secrets, ownResTrk.NSs):
                try: OCcli.CleanUpRes(owned)
                except Exception as error:
                    clnFail = True
                    clnExc = error
                    logs.error(
                        'Could not clean up fixture resources: %s', error
                    )
            for dir in sshKeyDirs:
                try: dir.cleanup()
                except Exception:
                    clnFail = True
                    logs.error(
                        'Could not clean up fixture SSH key directory `%s`.',
                        dir.name,
                    )
            if (clnFail and (primExc is None)):
                if (clnExc is not None): raise clnExc
                raise RuntimeError(
                    'Could not clean up fixture resources or SSH key files.',
                ) from None
