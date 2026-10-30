import ast
import base64
import csv
import hashlib
import importlib.util
import inspect
from dataclasses import FrozenInstanceError
import json
import logging
from pathlib import Path
import subprocess
import tempfile
import textwrap
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
import yaml

import fixtures.VM as MUT
from libs.CustomTypes import NamedDict
from libs.OpenShift.LP.Virt.CLI.VirtCtl import VirtCtlCLI
from libs.OpenShift.LP.Virt.VM.RHOVssh import RHOVsshCon
from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.OpenShift.OCP.Resources import Secret, SpecMismatch
from libs.Utils.CmdExec import CmdRes
import Utils


@pytest.mark.usefixtures('localPreparation')
def test_VMcreate_ResourceHandlersReceiveNamedDictAndSharedOwnership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MUT, '_DEF__FXT_CFG', (MUT._DEF__FXT_CFG | NamedDict(
        vmCfg=NamedDict(ctxData={}),
    )))
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT.YAMLcfg, 'Render')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT.Namespace, 'Create')
    Utils.AutoPatch(monkeypatch, MUT.Secret, 'Create')
    OCcli.GetResUID.return_value = (True, '', '')
    MUT.YAMLcfg.Render.return_value = 'dummy manifest'
    client = OCmock()
    client.vmNewRes.return_value = (
        OCcli.ResPhase.APPLIED, CommandResult(True),
        {'metadata': {'uid': 'dummy-vm-uid'}},
    )
    ordered = mock.Mock()
    ordered.attach_mock(MUT.Namespace.Create, 'namespace')
    ordered.attach_mock(MUT.Secret.Create, 'secret')
    ordered.attach_mock(client.vmNewRes, 'vm')
    params = MUT.FuncParsVMcreate(fxtCfgObj=MUT._DEF__FXT_CFG)
    MUT.VMcreate(
        Request(), params, ocCLI=mock.Mock(return_value=client),
        virtCtlCLI=mock.Mock(),
    )
    assert ([call[0] for call in ordered.mock_calls] == [
        'namespace', 'secret', 'vm',
    ])
    (nsClient, nsCfg, nsInfo) = MUT.Namespace.Create.call_args.args
    (secretClient, secretCfg, secretInfo) = MUT.Secret.Create.call_args.args
    assert (nsClient is secretClient is client)
    assert (type(nsCfg.meta) is type(secretCfg.meta) is NamedDict)
    assert (type(secretCfg.spec) is NamedDict)
    assert (nsCfg.meta.name == secretCfg.meta.namespace == params.fxtCfgObj.ns)
    assert (secretCfg.meta.name == 'aws-s3--cred')
    assert (secretCfg.spec.type == 'Opaque')
    assert ({
        key: base64.b64decode(value)
        for (key, value) in secretCfg.spec.data.items()
    } == {
        'accessKeyId': b'ssh-ed25519 synthetic',
        'secretKey': b'ssh-ed25519 synthetic',
    })
    assert (nsInfo.owned is params.ownNSs)
    assert (secretInfo.owned is params.ownSecrets)


@pytest.mark.usefixtures('localPreparation')
@pytest.mark.parametrize('failure', [
    'mismatch', 'secret', 'wrapper', 'cleanup'
])
def test_FxtVM_ResourceHandlersKeepFailureCleanupAndPrimaryError(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    monkeypatch.setattr(MUT, '_DEF__FXT_CFG', (MUT._DEF__FXT_CFG | NamedDict(
        vmCfg=NamedDict(ctxData={}),
    )))
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, OCcli, 'CleanUpRes')
    Utils.AutoPatch(monkeypatch, MUT.YAMLcfg, 'Render')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT.Namespace, 'Create')
    Utils.AutoPatch(monkeypatch, MUT.Secret, 'Create')
    OCcli.GetResUID.return_value = (True, '', '')
    MUT.YAMLcfg.Render.return_value = 'dummy manifest'
    client = OCmock()
    client.vmNewRes.return_value = (
        OCcli.ResPhase.APPLIED, CommandResult(True),
        {'metadata': {'uid': 'dummy-vm-uid'}},
    )
    primary = RuntimeError('dummy-primary')
    owned = {}

    def Namespace(cli: OCcli, cfg: Any, info: Any) -> dict:
        owned['namespace'] = info.owned
        info.owned.append((cli, 'Namespace/dummy', 'ns-uid'))
        return {'kind': 'Namespace'}

    def Secret(cli: OCcli, cfg: Any, info: Any) -> dict:
        owned['secret'] = info.owned
        if (failure == 'mismatch'): raise SpecMismatch('dummy-private')
        info.owned.append((cli, 'Secret/dummy', 'secret-uid'))
        if (failure == 'secret'): raise RuntimeError('dummy-private')
        return {'kind': 'Secret'}

    MUT.Namespace.Create.side_effect = Namespace
    MUT.Secret.Create.side_effect = Secret
    if (failure == 'wrapper'): MUT.VM.side_effect = primary
    if (failure == 'cleanup'): MUT.VM.CleanUpVMs.side_effect = primary
    else:
        OCcli.CleanUpRes.side_effect = RuntimeError('dummy-cleanup-secondary')
    generator = MUT.FxtVM.__wrapped__(
        Request(), mock.Mock(return_value=client), mock.Mock(),
    )
    with pytest.raises(RuntimeError) as caught:
        if (failure == 'cleanup'):
            next(generator)
            next(generator)
        else: next(generator)
    if (failure in ('wrapper', 'cleanup')): assert (caught.value is primary)
    else:
        assert ('dummy-private' not in str(caught.value))
        assert (('different data' in str(caught.value)) == (
            failure == 'mismatch'
        ))
    assert (MUT.Secret.SpecMismatch is SpecMismatch)
    assert (OCcli.CleanUpRes.call_args_list == [
        mock.call(owned['secret']), mock.call(owned['namespace']),
    ])
    assert (bool(owned['secret']) == (failure != 'mismatch'))
    if (failure in ('wrapper', 'cleanup')):
        MUT.VM.CleanUpVMs.assert_called_once()


@pytest.mark.parametrize('moduleName,count', [
    ('fixtures.VM', 6),
    ('fixtures.BenchmarkRunner', 2),
    ('libs.OpenShift.OCP.CLI.OC', 2),
    ('libs.OpenShift.LP.Virt.VM.VM', 1),
])
def test_FuncParsVMcreate_OwnershipTypesUseConcreteCLI(
    moduleName: str, count: int,
) -> None:
    module = importlib.import_module(moduleName)
    source = inspect.getsource(module)
    lines = source.splitlines()
    annotations = []
    names = {'ownVMs', 'ownSecrets', 'ownNSs', 'ownRes', 'owned'}
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.AnnAssign) and isinstance(
            node.target, ast.Name
        ) and (node.target.id in names)):
            annotations.append(node.annotation)
            if isinstance(node.value, ast.Call):
                assert ('(oc, resRef, resUID)' in lines[node.end_lineno - 1])
        if isinstance(node, ast.FunctionDef):
            for argument in node.args.args:
                if (argument.arg in names):
                    annotations.append(argument.annotation)
    assert (len(annotations) == count)
    for annotation in annotations:
        assert (ast.unparse(annotation).replace(
            "'OCcli'", 'OCcli'
        ) == 'list[tuple[OCcli, str, str]]')


def test__DEF__FXT_CFG_UsesNestedVMConfiguration() -> None:
    cfg = MUT._DEF__FXT_CFG
    assert (cfg.cfgFile == 'config--vm--template.yaml')
    assert (type(cfg.vmCfg.ctxData) is dict)
    assert (cfg.vmCfg.ctxData == {})
    assert (cfg.vmCfg.timeout == NamedDict(vmCreate='15m', dvImport='30m'))
    assert ('configFile' not in cfg)
    assert ('timeout' not in cfg)


@pytest.mark.usefixtures('localPreparation')
@pytest.mark.parametrize('baseContext', [{}, {
    'inherited': 'kept', 'vmMemory': '2Gi',
    'custom': {'inherited': 'kept', 'selection': 'base'},
}])
@pytest.mark.parametrize('context', ['omitted', {}, {
    'vmNS': 'ignored-ns', 'sshPublicKeyB64': 'ignored-public',
    'vmDataVolumeSource': {'http': {'url': 'https://example.invalid/ignored'}},
    'vmName': 'ignored-name', 'vmMemory': '4Gi',
    'custom': {'selection': 'override'},
}])
@pytest.mark.parametrize('count', [1, 2])
def test_VMcreate_ContextMergesCustomFieldsAndGeneratesManagedFields(
    monkeypatch: pytest.MonkeyPatch, context: dict[str, Any] | str,
    baseContext: dict[str, Any], count: int,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT.YAMLcfg, 'Render')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    OCcli.GetResUID.return_value = (True, '', '')
    MUT.YAMLcfg.Render.return_value = 'rendered manifest'
    oc = OCmock()
    oc.vmNewRes.return_value = (
        OCcli.ResPhase.WAITED, CommandResult(True),
        {'metadata': {'uid': 'new-vm'}},
    )
    params = MUT.FuncParsVMcreate(
        fxtCfgObj=MUT._DEF__FXT_CFG | NamedDict(
            vmCfg=NamedDict(ctxData=baseContext),
        ),
    )

    merged = dict(baseContext)
    overrides = {}
    if (context != 'omitted'):
        overrides['vmCfg'] = NamedDict(ctxData=context)
        merged.update(context)
        if ('custom' in context):
            merged['custom'] = {
                **baseContext.get('custom', {}), **context['custom'],
            }

    result = MUT.VMcreate(
        Request(), params, ocCLI=mock.Mock(return_value=oc),
        virtCtlCLI=mock.Mock(), namePfx='custom', count=count,
        gstCfg=NamedDict(imgSrc=NamedDict(
            CDI=NamedDict(credType='None'),
        )),
        **overrides,
    )

    assert (result == params.vmList == [MUT.VM.return_value] * count)
    expected = {
        **merged, 'vmNS': 'default',
        'sshPublicKeyB64': base64.b64encode(
            b'ssh-ed25519 synthetic',
        ).decode(),
        'vmDataVolumeSource': {'http': {
            'url': MUT._DEF__FXT_CFG.gstCfg.imgSrc.CDI.url,
        }},
    }
    assert (MUT.YAMLcfg.Render.call_args_list == [
        mock.call(
            Path('/repo/conf/config--vm--template.yaml'),
            {**expected, 'vmName': (
                f'custom-{index}' if (count > 1) else 'custom'
            )},
        ) for index in range(count)
    ])
    assert (oc.vmNewRes.call_args_list == [
        mock.call('rendered manifest', waitTime='15m'),
    ] * count)
    assert not any('Secret/' in ref for (_, ref, _) in params.ownSecrets)


@pytest.mark.parametrize('context', [NamedDict(), [], 'invalid', 1, False])
def test_VMcreate_DefaultContextRejectsMismatchedTypes(
    context: Any,
) -> None:
    params = MUT.FuncParsVMcreate(fxtCfgObj=MUT._DEF__FXT_CFG)
    ocFactory = mock.Mock()
    with pytest.raises(TypeError, match=(
        r"\['vmCfg'\]\['ctxData'\].*expected `dict`, received "
        f'`{type(context).__name__}`'
    )):
        MUT.VMcreate(
            Request(), params, ocCLI=ocFactory, virtCtlCLI=mock.Mock(),
            vmCfg=NamedDict(ctxData=context),
        )
    ocFactory.assert_not_called()
    assert not params.vmList
    assert not params.ownVMs


@pytest.mark.usefixtures('localPreparation')
@pytest.mark.parametrize('count', [1, 2])
@pytest.mark.parametrize('dvNames', [
    [], ['actual-disk'], ['os', 'data', 'os'],
])
@pytest.mark.parametrize('reuse', [False, True])
def test_VMcreate_WaitsReferencedImportsAfterTrackingAllVMs(
    monkeypatch: pytest.MonkeyPatch, count: int,
    dvNames: list[str], reuse: bool,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT.YAMLcfg, 'Render')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    OCcli.GetResUID.return_value = (True, 'preexisting' if reuse else '', '')
    MUT.YAMLcfg.Render.return_value = 'manifest'
    params = MUT.FuncParsVMcreate(fxtCfgObj=MUT._DEF__FXT_CFG)
    clients = [OCmock() for _ in range(count)]
    waits = []

    def Wait(*args: Any, **opts: Any) -> CmdRes:
        assert (len(params.vmList) == count)
        assert (len(params.ownVMs) == count)
        waits.append((args, opts))
        return CommandResult(True)

    for (index, client) in enumerate(clients):
        client.EnsureRes = mock.Mock(return_value={'kind': 'Namespace'})
        client.Run.side_effect = Wait
        client.vmNewRes.return_value = (
            OCcli.ResPhase.WAITED, CommandResult(True), {
                'metadata': {'uid': f'new-{index}'},
                'spec': {'template': {'spec': {'volumes': [
                    {'name': 'ignored', 'persistentVolumeClaim': {
                        'claimName': 'not-a-datavolume',
                    }},
                    *[{'name': name, 'dataVolume': {
                        'name': f'{index}-{name}',
                    }} for name in dvNames],
                ]}}},
            },
        )

    result = MUT.VMcreate(
        Request(), params, ocCLI=mock.Mock(side_effect=clients),
        virtCtlCLI=mock.Mock(), count=count, reuseExisting=reuse,
        vmCfg=NamedDict(
            ctxData={'custom': 'context'},
            timeout=NamedDict(dvImport='7m'),
        ),
        gstCfg=NamedDict(imgSrc=NamedDict(CDI=NamedDict(credType='None'))),
    )

    assert (len(result) == len(params.vmList) == count)
    expected = [] if reuse else [
        (('wait', f'DataVolume/{index}-{name}'), {
            'for': 'jsonpath={.status.phase}=Succeeded', 'timeout': '7m',
        }) for index in range(count) for name in dict.fromkeys(dvNames)
    ]
    assert (waits == expected)
    assert (len(params.ownVMs) == (0 if reuse else count))


@pytest.mark.parametrize('failure', ['result', 'exception'])
@pytest.mark.parametrize('cleanupFailure', [False, True])
def test_FxtVM_ImportFailureCleansOwnedResourcesAndPreservesError(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    failure: str, cleanupFailure: bool,
) -> None:
    state = localContext
    nativeFactory = state.ocFactory
    primary = RuntimeError('synthetic-import-error')
    cleanupOrder = []

    def Factory(ns: str, **opts: Any) -> OCcli:
        client = nativeFactory(ns, **opts)
        nativeRun = client.Run

        def Run(subCmd: str, *args: Any, **kwargs: Any) -> CmdRes:
            if ((subCmd == 'wait') and args[0].startswith('DataVolume/')):
                assert (len(state.resources) == 4)
                if (failure == 'exception'): raise primary
                return CommandResult(False, 'synthetic-import-error')
            if (subCmd == 'delete'):
                cleanupOrder.append(args[0].split('/')[0])
                if (cleanupFailure and args[0].startswith('Secret/')):
                    raise RuntimeError('synthetic-cleanup-error')
            return nativeRun(subCmd, *args, **kwargs)

        client.Run = Run
        return client

    cfg = MUT._DEF__FXT_CFG | NamedDict(
        count=2, namePfx='import-failure', ns='owned-ns',
        gstCfg=NamedDict(
            usrCrd=NamedDict(sshKey=NamedDict(
                prv=str(state.keyPath), pub=None,
            )),
            imgSrc=NamedDict(CDI=NamedDict(credInfo=NamedDict(
                vaultInfo=state.cfg['gstCfg']['imgSrc']['CDI'][
                    'credInfo'
                ]['vaultInfo'],
            ))),
        ),
    )
    state.namespaces['owned-ns'] = None
    state.request.param = cfg
    generator = MUT.FxtVM.__wrapped__(state.request, Factory, VirtCtlCLI)
    with pytest.raises(RuntimeError, match='synthetic-import-error') as caught:
        next(generator)
    if (failure == 'exception'): assert (caught.value is primary)
    assert (cleanupOrder == [
        'VirtualMachine', 'VirtualMachine', 'Secret', 'Namespace',
    ])


def test_FuncParsVMcreate_RequiresConfiguration() -> None:
    with pytest.raises(TypeError, match='fxtCfgObj'):
        MUT.FuncParsVMcreate()


@pytest.mark.parametrize('fld', ['vmList', 'ownVMs', 'sshKeyDirs'])
def test_FuncParsVMcreate_DefaultListsAreIndependentAndMutable(
    fld: str,
) -> None:
    cfg = MUT._DEF__FXT_CFG
    first = MUT.FuncParsVMcreate(fxtCfgObj=cfg)
    second = MUT.FuncParsVMcreate(fxtCfgObj=cfg)
    firstList = getattr(first, fld)
    secondList = getattr(second, fld)
    assert (firstList is not secondList)
    assert (firstList == secondList == [])

    firstList.append(mock.sentinel.entry)

    assert (firstList == [mock.sentinel.entry])
    assert (secondList == [])
    assert (first.fxtCfgObj is second.fxtCfgObj is cfg)


@pytest.mark.parametrize('fld', [
    'fxtCfgObj', 'vmList', 'ownVMs', 'sshKeyDirs',
])
def test_FuncParsVMcreate_RejectsFieldRebinding(fld: str) -> None:
    fPars = MUT.FuncParsVMcreate(fxtCfgObj=MUT._DEF__FXT_CFG)
    before = getattr(fPars, fld)

    with pytest.raises(FrozenInstanceError):
        setattr(fPars, fld, object())

    assert (getattr(fPars, fld) is before)


def test_FuncParsVMcreate_PreservesSuppliedListsAndConfiguration() -> None:
    cfg = MUT._DEF__FXT_CFG | NamedDict(count=0)
    before = json.dumps(cfg)
    vmList = []
    ownVMs = []
    sshKeyDirs = []

    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=cfg,
        vmList=vmList,
        ownVMs=ownVMs,
        sshKeyDirs=sshKeyDirs,
    )

    assert (fPars.fxtCfgObj is cfg)
    assert (fPars.vmList is vmList)
    assert (fPars.ownVMs is ownVMs)
    assert (fPars.sshKeyDirs is sshKeyDirs)
    assert (vmList == ownVMs == sshKeyDirs == [])
    assert (json.dumps(cfg) == before)


def ConfigSnapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    '''Compare settings except the three fixture-managed mutable values.'''
    snapshot = json.loads(json.dumps(cfg))
    context = snapshot.get('vmCfg', {}).get('ctxData')
    if (type(context) is dict):
        for field in ('vmNS', 'vmDataVolumeSource', 'sshPublicKeyB64'):
            context.pop(field, None)
    return snapshot


def test_ConfigSnapshot_PreservesAllUnmanagedSettings() -> None:
    cfg = {'vmCfg': {'ctxData': {
        'vmNS': 'managed', 'vmDataVolumeSource': {'http': {'url': 'managed'}},
        'sshPublicKeyB64': 'managed', 'vmName': 'custom-name',
        'custom': {'retained': True},
    }, 'timeout': {'vmCreate': '15m', 'dvImport': '30m'}},
        'gstCfg': {'usrCrd': {'sshUsr': 'unchanged'}}}
    before = json.dumps(cfg)
    assert (ConfigSnapshot(cfg) == {
        'vmCfg': {'ctxData': {
            'vmName': 'custom-name', 'custom': {'retained': True},
        }, 'timeout': {'vmCreate': '15m', 'dvImport': '30m'}},
        'gstCfg': {'usrCrd': {'sshUsr': 'unchanged'}},
    })
    assert (json.dumps(cfg) == before)


def CommandResult(success: bool, stderr: str = '') -> CmdRes:
    return CmdRes('', stderr, 0 if success else 1, success)


def ResourceResult(resource: object) -> CmdRes:
    return CmdRes(
        json.dumps(resource) if (resource is not None) else '', '', 0, True
    )


@pytest.mark.parametrize('case', [
    'created', 'existing', 'different', 'dry-run', 'lookup', 'apply',
    'exception', 'cleanup', 'invalid-data', 'created-invalid-data',
    'missing-uid',
    'deleting', 'wait-failed', 'wait-timeout',
])
def test_VMcreate_InlineSecretThroughActualCLI(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    state = localContext
    raw = {'accessKeyId': b'access\x00value', 'secretKey': b'secret\x00value'}
    (state.keyPath.parent / 'access').write_bytes(b' access\x00value \n')
    (state.keyPath.parent / 'relative-secret').write_bytes(
        b' secret\x00value \n'
    )
    expected = {key: base64.b64encode(value).decode()
                for (key, value) in raw.items()}
    paths = []
    owners = []
    waited = []
    nativeOpen = MUT.os.open
    nativeDirectory = Secret.TemporaryDirectory
    originalRun = subprocess.run
    originalEnsure = OCcli.EnsureRes

    def Ensure(
        client: OCcli, specification: Any, owned: Any, **kwargs: Any,
    ) -> Any:
        if (specification['subCmd'] == 'secret generic'): owners.append(owned)
        return originalEnsure(client, specification, owned, **kwargs)

    def Open(path: Any, flags: int, mode: int = 0o777,
             **kwargs: Any) -> int:
        descriptor = nativeOpen(path, flags, mode, **kwargs)
        if (flags & MUT.os.O_EXCL):
            file = Path(path)
            assert ((file.parent.stat().st_mode & 0o777) == 0o700)
            assert ((file.stat().st_mode & 0o777) == mode == 0o600)
            assert (file.read_bytes() == b'')
            paths.append(file)
        return descriptor

    def Directory(**kwargs: Any) -> Any:
        directory = nativeDirectory(**kwargs)
        cleanup = directory.cleanup
        if (case == 'cleanup'):
            def FailCleanup() -> None:
                assert owners[0]
                cleanup()
                raise OSError('synthetic-cleanup-failure')
            directory.cleanup = FailCleanup
        return directory

    def Run(command: list[str], **kwargs: Any) -> Any:
        if ((command[0] != 'oc-unit') or (command[-1] == 'options')):
            return originalRun(command, **kwargs)
        phase = next(value for value in ('create', 'get', 'apply', 'wait')
                     if (value in command))
        if ((phase == 'wait') and (command[-1] == 'Secret/aws-s3--cred')):
            assert not owners[0]
            assert ('--timeout=5m' in command)
            if (case == 'wait-timeout'):
                raise subprocess.TimeoutExpired(command, 300)
            waited.append(True)
            return subprocess.CompletedProcess(
                command, int((case == 'wait-failed')), '', '',
            )
        secret = ('secret' in command) and ('generic' in command)
        if (not secret and kwargs.get('input')):
            secret = yaml.safe_load(kwargs['input']).get('kind') == 'Secret'
        if not secret: return originalRun(command, **kwargs)
        resource = {
            'kind': 'Secret', 'metadata': {'name': 'aws-s3--cred'},
            'type': 'Opaque', 'data': dict(expected),
        }
        if (phase == 'create'):
            generated = {}
            for part in command:
                if part.startswith('--from-file='):
                    for source in next(csv.reader([part.split('=', 1)[1]])):
                        (key, file) = source.split('=', 1)
                        generated[key] = Path(file).read_bytes()
            assert (generated == raw)
            assert not any(('\x00' in part) for part in command)
            stdout = yaml.safe_dump(resource)
        elif (phase == 'get'):
            if ((case in ('deleting', 'wait-failed', 'wait-timeout'))
                    and not waited):
                resource['metadata']['deletionTimestamp'] = 'now'
                return subprocess.CompletedProcess(
                    command, 0, json.dumps(resource), '',
                )
            if (case == 'different'): resource['data'] = {}
            if (case == 'invalid-data'): resource['data']['secretKey'] = '!'
            stdout = json.dumps(resource) if (case in (
                'existing', 'different', 'invalid-data'
            )) else ''
        else:
            assert (phase == 'apply')
            resource['metadata']['uid'] = 'created-uid'
            if (case == 'missing-uid'): resource['metadata'].pop('uid')
            if (case == 'created-invalid-data'):
                resource['data']['secretKey'] = '!'
            stdout = json.dumps(resource)
        if (case == 'exception'): raise RuntimeError('synthetic-private')
        failed = (phase == {'dry-run': 'create', 'lookup': 'get',
                           'apply': 'apply'}.get(case))
        return subprocess.CompletedProcess(command, int(failed), stdout, '')

    monkeypatch.setattr(MUT.os, 'open', Open)
    monkeypatch.setattr(Secret, 'TemporaryDirectory', Directory)
    monkeypatch.setattr(OCcli, 'EnsureRes', Ensure)
    monkeypatch.setattr(subprocess, 'run', Run)
    if (case in ('created', 'existing', 'deleting')):
        Create(state)
    else:
        with pytest.raises(RuntimeError): Create(state)
    assert (bool(owners[0]) == (
        case in ('created', 'cleanup', 'created-invalid-data', 'deleting')
    ))
    assert (len(paths) == 2)
    assert all((not path.exists() and not path.parent.exists())
               for path in paths)


@pytest.mark.parametrize('timestamp', [None, 'now'])
@pytest.mark.parametrize('outcome', ['gone', 'failed', 'timeout'])
def test_VMcreate_DirectNamespaceDeletionWait(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    timestamp: str | None, outcome: str,
) -> None:
    state = localContext
    state.namespaces['context-ns'] = {
        'kind': 'Namespace',
        'metadata': {'name': 'context-ns', 'uid': 'old',
                     'deletionTimestamp': timestamp},
        'status': {'phase': 'Terminating'},
    }
    originalRun = subprocess.run
    waited = []

    def Run(command: list[str], **kwargs: Any) -> Any:
        if (('wait' in command) and (command[-1] == 'Namespace/context-ns')):
            waited.append(command)
            assert ('--timeout=5m' in command)
            if (outcome == 'timeout'):
                raise subprocess.TimeoutExpired(command, 300)
            state.namespaces['context-ns'] = None
            return subprocess.CompletedProcess(
                command, int((outcome == 'failed')), '', '',
            )
        return originalRun(command, **kwargs)

    monkeypatch.setattr(subprocess, 'run', Run)
    with mock.patch.object(
        OCcli, 'EnsureRes', autospec=True, side_effect=OCcli.EnsureRes,
    ) as ensure:
        if ((timestamp is not None) and (outcome != 'gone')):
            with pytest.raises(RuntimeError, match='Namespace/context-ns'):
                Create(state)
            assert not ensure.call_args_list[0].args[2]
        else:
            Create(state)
            owned = ensure.call_args_list[0].args[2]
            assert (bool(owned) == (timestamp is not None))
            if owned: assert (owned[0][2] == 'owned-Namespace')
    assert (bool(waited) == (timestamp is not None))
    assert (ensure.call_args_list[0].args[1] == {
        'subCmd': 'namespace', 'args': ['context-ns'],
    })


def OCmock(name: str | None = None) -> mock.MagicMock:
    client = mock.create_autospec(OCcli, instance=True, name=name)
    client.ResPhase = OCcli.ResPhase
    client.EnsureRes.side_effect = lambda *args, **kwargs: OCcli.EnsureRes(
        client, *args, **kwargs
    )
    client.vmNewRes = client.NewRes
    secret = None
    def Run(*args: str, **kwargs: object) -> CmdRes:
        nonlocal secret
        if (args[0] == 'create namespace'):
            return ResourceResult({
                'kind': 'Namespace', 'metadata': {'name': args[1]},
            })
        if (args[0] == 'create secret generic'):
            data = {}
            for source in next(csv.reader([kwargs['from-file']])):
                (key, path) = source.split('=', 1)
                with open(path, 'rb') as stream:
                    data[key] = base64.b64encode(stream.read()).decode()
            return ResourceResult({
                'kind': 'Secret', 'metadata': {'name': args[1]}, 'data': data,
            })
        if ((args[0] == 'get') and ('-f' in args)):
            resource = json.loads(kwargs['input'])
            if (resource['kind'] == 'Namespace'):
                return ResourceResult(
                    {
                        'metadata': {'uid': 'existing-ns'},
                        'status': {'phase': 'Active'},
                    }
                )
            return ResourceResult(secret)
        if (args[0] in ('create', 'apply')):
            resource = json.loads(kwargs['input'])
            resource['metadata']['uid'] = 'secret-uid'
            if (args[0] == 'apply'): secret = resource
            return CmdRes(json.dumps(resource), '', 0, True)
        return CommandResult(True)
    client.Run.side_effect = Run
    client.GetResUID.return_value = (True, '', '')

    def NewRes(manifest: str, **kwargs: object) -> object:
        if kwargs.get('checkExist'):
            return OCcli.NewRes(client, manifest, **kwargs)
        return client.vmNewRes(manifest, **kwargs)

    client.NewRes = NewRes
    return client


def Request(param: dict[str, object] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        param={} if (param is None) else param,
        config=SimpleNamespace(rootpath=Path('/repo')),
    )


@pytest.mark.parametrize('imageURL', [
    None,
    'https://example.invalid/image.qcow2?part=one+two&part=%2f%2B'
    '&token=$VALUE\\literal',
    '',
])
def test_FxtVM_PreparesActualExampleEnvironmentConfiguration(
    monkeypatch: pytest.MonkeyPatch, pytestconfig: pytest.Config,
    imageURL: str | None,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    envKey = 'IENG_PYTEST____TS__EXAMPLE__VM____FXT_CFG__GSTCFG_IMGSRC_CDI_URL'
    monkeypatch.setenv('VALUE', 'must-not-expand')
    if (imageURL is None):
        monkeypatch.delenv(envKey, raising=False)
    else:
        monkeypatch.setenv(envKey, imageURL)
    pytestconfig.addinivalue_line('markers', 'vm: RHOV VM tests.')
    root = Path(MUT.__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        'vm_example', root / 'test--suites/ts--example/test_VM.py',
    )
    assert ((spec is not None) and (spec.loader is not None))
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    method = example.TestVM.test_VMcreate
    mark = next(mark for mark in method.pytestmark if (mark.name
        == 'parametrize'))
    assert ((mark.args[0] == 'fxtVM,') and (mark.kwargs['indirect'] is True))
    config = mark.args[1][0][0]
    expected = imageURL if (imageURL is not None) else (
        'https://s3.us-east-1.amazonaws.com/'
        'ieng--vm-image--windows--us-east-1/'
        'win11/dist/win11-25H2--template--base.qcow2'
    )
    defaultsBefore = ConfigSnapshot(MUT._DEF__FXT_CFG)
    source = config['gstCfg'].imgSrc.CDI.value
    assert isinstance(config['gstCfg'].imgSrc.CDI, NamedDict._TypeReplace)
    assert (source == {'type': 'AWS-S3', 'credType': 'None', 'url': expected})
    merged = MUT._DEF__FXT_CFG | NamedDict(**config)
    assert (merged.gstCfg.imgSrc.CDI is source)
    expectedConfig = dict(
        MUT._DEF__FXT_CFG, ns='vm-test', namePfx='vm-example'
    )
    expectedConfig['gstCfg'] = dict(MUT._DEF__FXT_CFG.gstCfg)
    expectedConfig['gstCfg']['imgSrc'] = dict(
        MUT._DEF__FXT_CFG.gstCfg.imgSrc, CDI=source
    )
    assert ((merged == expectedConfig) and (merged.count == 1))
    monkeypatch.setattr(
        MUT, '_ResolveSSHkeys', lambda cfg, dirs: cfg | NamedDict(
            gstCfg=NamedDict(usrCrd=NamedDict(sshKey=NamedDict(
                prv='/synthetic/not-read', pub='/synthetic/public',
            ))),
        )
    )

    def PublicOpen(path: Path, *args: Any, **kwargs: Any) -> Any:
        assert (str(path) == '/synthetic/public')
        return mock.mock_open(read_data='ssh-ed25519 SYNTHETIC\n')()

    monkeypatch.setattr(Path, 'open', PublicOpen)
    reads = mock.Mock(side_effect=AssertionError(
        'Image credentials accessed.'
    ))
    monkeypatch.setattr(Path, 'read_text', reads)
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    OCcli.GetResUID.side_effect = [(True, '', ''), (True, 'owned-uid', '')]
    oc = OCmock()
    oc.vmNewRes.return_value = (
        SimpleNamespace(name='WAITED'),
        CommandResult(True),
        {'metadata': {'uid': 'owned-uid'}},
    )
    apply = mock.Mock(wraps=oc.NewRes)
    oc.NewRes = apply
    request = Request(config)
    request.config.rootpath = root
    generator = MUT.FxtVM.__wrapped__(
        request, mock.MagicMock(return_value=oc), mock.MagicMock(),
    )
    try:
        if (imageURL == ''):
            with pytest.raises(ValueError, match='CDI.url.*HTTP'):
                next(generator)
            oc.vmNewRes.assert_not_called()
        else:
            bundle = next(generator)
            assert (len(bundle.vmList) == 1)
            oc.vmNewRes.assert_called_once()
            manifest = yaml.safe_load(oc.vmNewRes.call_args.args[0])
            assert (manifest['kind'] == 'VirtualMachine')
            assert (manifest['spec']['dataVolumeTemplates'][0]['spec']['source'
                ] == {
                'http': {'url': expected},
            })
            assert (manifest['metadata']['namespace'] == 'vm-test')
            assert (manifest['metadata']['name'] == 'vm-example')
            assert ('secretRef' not in yaml.safe_dump(manifest))
            assert ('isSecret' not in apply.call_args.kwargs)
    finally:
        generator.close()
    reads.assert_not_called()
    assert (ConfigSnapshot(MUT._DEF__FXT_CFG) == defaultsBefore)
    assert (config['gstCfg'].imgSrc.CDI.value is source)


@pytest.mark.usefixtures('localPreparation')
def test_VMcreate_NegativeCountIsRejectedBeforeAnyOperation(
    sshKeyDirs: list[TemporaryDirectory],
) -> None:
    vmList = []
    ownVMs = []
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()

    with pytest.raises(ValueError, match='must not be negative'):
        MUT.VMcreate(
            Request(),
            MUT.FuncParsVMcreate(
                fxtCfgObj=MUT._DEF__FXT_CFG,
                vmList=vmList,
                ownVMs=ownVMs,
                sshKeyDirs=sshKeyDirs,
            ),
            ocCLI=ocFactory,
            virtCtlCLI=virtCtlFactory,
            count=-1,
        )

    assert (vmList == [])
    assert (ownVMs == [])
    ocFactory.assert_not_called()
    virtCtlFactory.assert_not_called()


@pytest.mark.usefixtures('localPreparation')
def test_VMcreate_ZeroCountReturnsEmptyWithoutAnyOperation(
    sshKeyDirs: list[TemporaryDirectory],
) -> None:
    vmList = []
    ownVMs = []
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()

    result = MUT.VMcreate(
        Request(),
        MUT.FuncParsVMcreate(
            fxtCfgObj=MUT._DEF__FXT_CFG,
            vmList=vmList,
            ownVMs=ownVMs,
            sshKeyDirs=sshKeyDirs,
        ),
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
        count=0,
    )

    assert (result == [])
    assert (vmList == [])
    assert (ownVMs == [])
    ocFactory.assert_not_called()
    virtCtlFactory.assert_not_called()


@pytest.mark.usefixtures('localPreparation')
def test_VMcreate_BatchReturnsNewVMsAndAppendsConfirmedOwnership(
    sshKeyDirs: list[TemporaryDirectory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT.YAMLcfg, 'Render')
    MUT.YAMLcfg.Render.return_value = 'rendered yaml'
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
        (True, '', ''),
        (True, '', ''),
    ]
    vmZero = mock.MagicMock(name='vmZero')
    vmOne = mock.MagicMock(name='vmOne')
    MUT.VM.side_effect = [vmZero, vmOne]
    ocZero = OCmock('ocZero')
    ocOne = OCmock('ocOne')
    ocZero.vmNewRes.return_value = (
        SimpleNamespace(name='WAITED'),
        CommandResult(True),
        {'metadata': {'uid': 'uid-zero'}},
    )
    ocOne.vmNewRes.return_value = (
        SimpleNamespace(name='APPLIED'),
        CommandResult(True),
        {'metadata': {'uid': 'uid-one'}},
    )
    ocFactory = mock.MagicMock(side_effect=[ocZero, ocOne])
    virtCtlFactory = mock.MagicMock()
    vmList = []
    ownVMs = []

    created = MUT.VMcreate(
        Request(),
        MUT.FuncParsVMcreate(
            fxtCfgObj=MUT._DEF__FXT_CFG,
            vmList=vmList,
            ownVMs=ownVMs,
            sshKeyDirs=sshKeyDirs,
        ),
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
        count=2,
        namePfx='unit',
        cfgFile='template.yaml',
        vmCfg=NamedDict(timeout=NamedDict(vmCreate='4m')),
        cliCfg=NamedDict(
            ocCfg={'context': 'oc-context'},
            vcCfg={'kubeconfig': '/virt-kubeconfig'},
        ),
        gstCfg=NamedDict(
            usrCrd=NamedDict(sshUsr='unit-user'),
        ),
    )

    assert (created == [vmZero, vmOne])
    assert (vmList == [vmZero, vmOne])
    assert (ownVMs == [
        (ocZero, 'VirtualMachine/unit-0', 'uid-zero'),
        (ocOne, 'VirtualMachine/unit-1', 'uid-one'),
    ])
    assert (MUT.YAMLcfg.Render.call_args_list == [
        mock.call(Path('/repo/conf/template.yaml'), mock.ANY),
        mock.call(Path('/repo/conf/template.yaml'), mock.ANY),
    ])
    assert (ocFactory.call_args_list == [
        mock.call(ns='default', context='oc-context'),
        mock.call(ns='default', context='oc-context'),
    ])
    assert (virtCtlFactory.call_args_list == [
        mock.call(ns='default', kubeconfig='/virt-kubeconfig'),
        mock.call(ns='default', kubeconfig='/virt-kubeconfig'),
    ])
    ocZero.vmNewRes.assert_called_once_with('rendered yaml', waitTime='4m')
    ocOne.vmNewRes.assert_called_once_with('rendered yaml', waitTime='4m')
    assert ([item.kwargs['name'] for item in MUT.VM.call_args_list] == [
        'unit-0',
        'unit-1',
    ])
    assert all(
        (item.kwargs['ns'] == 'default') for item in MUT.VM.call_args_list
    )
    assert all(
        (item.kwargs['sshUsr'] == 'unit-user')
            for item in MUT.VM.call_args_list
    )
    assert all(
        (item.kwargs['sshKey'] == '/synthetic/private-key')
            for item in MUT.VM.call_args_list
    )


@pytest.mark.usefixtures('localPreparation')
def test_VMcreate_ReusesExistingVMWithoutRenderOrApply(
    sshKeyDirs: list[TemporaryDirectory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT.YAMLcfg, 'Render')
    OCcli.GetResUID.return_value = (True, 'existing', '')
    oc = OCmock()
    vmList = []
    ownVMs = []

    created = MUT.VMcreate(
        Request(),
        MUT.FuncParsVMcreate(
            fxtCfgObj=MUT._DEF__FXT_CFG,
            vmList=vmList,
            ownVMs=ownVMs,
            sshKeyDirs=sshKeyDirs,
        ),
        ocCLI=mock.MagicMock(return_value=oc),
        virtCtlCLI=mock.MagicMock(),
        reuseExisting=True,
        namePfx='single',
    )

    assert (created == [MUT.VM.return_value])
    assert (vmList == created)
    assert (ownVMs == [])
    MUT.YAMLcfg.Render.assert_not_called()
    oc.vmNewRes.assert_not_called()


@pytest.mark.usefixtures('localPreparation')
def test_VMcreate_PreflightLookupFailureRaisesWithoutTracking(
    sshKeyDirs: list[TemporaryDirectory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    OCcli.GetResUID.return_value = (False, '', 'lookup failed')
    vmList = []
    ownVMs = []

    with pytest.raises(RuntimeError, match='lookup failed'):
        MUT.VMcreate(
            Request(),
            MUT.FuncParsVMcreate(
                fxtCfgObj=MUT._DEF__FXT_CFG,
                vmList=vmList,
                ownVMs=ownVMs,
                sshKeyDirs=sshKeyDirs,
            ),
            ocCLI=mock.Mock(return_value=OCmock()),
            virtCtlCLI=mock.MagicMock(),
        )

    assert (vmList == [])
    assert (ownVMs == [])


@pytest.mark.usefixtures('localPreparation')
def test_VMcreate_ExistingVMWithoutReuseIsRejected(
    sshKeyDirs: list[TemporaryDirectory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    OCcli.GetResUID.return_value = (True, 'existing', '')

    with pytest.raises(RuntimeError, match='already exists'):
        MUT.VMcreate(
            Request(),
            MUT.FuncParsVMcreate(
                fxtCfgObj=MUT._DEF__FXT_CFG,
                vmList=[],
                ownVMs=[],
                sshKeyDirs=sshKeyDirs,
            ),
            ocCLI=mock.Mock(return_value=OCmock()),
            virtCtlCLI=mock.MagicMock(),
        )


@pytest.mark.usefixtures('localPreparation')
def test_VMcreate_FailedNewResRaisesAfterRecordingOwnership(
    sshKeyDirs: list[TemporaryDirectory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT.YAMLcfg, 'Render')
    MUT.YAMLcfg.Render.return_value = 'yaml'
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
    ]
    oc = OCmock()
    oc.vmNewRes.return_value = (
        SimpleNamespace(name='WAITED'),
        CommandResult(False, 'apply failed'),
        {'metadata': {'uid': 'failed-uid'}},
    )
    virtCtlFactory = mock.MagicMock()
    vmList = []
    ownVMs = []

    with pytest.raises(RuntimeError) as raised:
        MUT.VMcreate(
            Request(),
            MUT.FuncParsVMcreate(
                fxtCfgObj=MUT._DEF__FXT_CFG,
                vmList=vmList,
                ownVMs=ownVMs,
                sshKeyDirs=sshKeyDirs,
            ),
            ocCLI=mock.MagicMock(return_value=oc),
            virtCtlCLI=virtCtlFactory,
        )

    assert (str(raised.value) == (
        'VM `lpqe-test` creation failed at `WAITED`: apply failed'
    ))
    assert (vmList == [])
    assert (ownVMs == [(oc, 'VirtualMachine/lpqe-test', 'failed-uid')])
    MUT.VM.assert_not_called()
    virtCtlFactory.assert_not_called()


@pytest.mark.usefixtures('localPreparation')
def test_VMcreate_FailedNewResBeforeApplyRaisesWithoutOwnership(
    sshKeyDirs: list[TemporaryDirectory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT.YAMLcfg, 'Render')
    MUT.YAMLcfg.Render.return_value = 'yaml'
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
        (False, '', 'not applied'),
    ]
    oc = OCmock()
    oc.vmNewRes.return_value = (
        SimpleNamespace(name='DRY_RUN'),
        CommandResult(False, 'validation failed'),
        None,
    )
    vmList = []
    ownVMs = []

    with pytest.raises(RuntimeError) as raised:
        MUT.VMcreate(
            Request(),
            MUT.FuncParsVMcreate(
                fxtCfgObj=MUT._DEF__FXT_CFG,
                vmList=vmList,
                ownVMs=ownVMs,
                sshKeyDirs=sshKeyDirs,
            ),
            ocCLI=mock.MagicMock(return_value=oc),
            virtCtlCLI=mock.MagicMock(),
        )

    assert (str(raised.value) == (
        'VM `lpqe-test` creation failed at `DRY_RUN`: validation failed'
    ))
    assert (vmList == [])
    assert (ownVMs == [])
    MUT.VM.assert_not_called()


@pytest.mark.usefixtures('localPreparation')
def test_VMcreate_SuccessfulApplyWithoutUIDRaises(
    sshKeyDirs: list[TemporaryDirectory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT.YAMLcfg, 'Render')
    MUT.YAMLcfg.Render.return_value = 'yaml'
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
    ]
    oc = OCmock()
    oc.vmNewRes.return_value = (
        SimpleNamespace(name='WAITED'),
        CommandResult(True),
        {'metadata': {}},
    )

    with pytest.raises(RuntimeError, match='establish ownership'):
        MUT.VMcreate(
            Request(),
            MUT.FuncParsVMcreate(
                fxtCfgObj=MUT._DEF__FXT_CFG,
                vmList=[],
                ownVMs=[],
                sshKeyDirs=sshKeyDirs,
            ),
            ocCLI=mock.MagicMock(return_value=oc),
            virtCtlCLI=mock.MagicMock(),
        )


@pytest.mark.usefixtures('localPreparation')
def test_VMcreate_NestedOverridesDoNotMutateDefaults(
    sshKeyDirs: list[TemporaryDirectory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    OCcli.GetResUID.return_value = (True, 'existing', '')
    defaultNestedValues = (
        dict(MUT._DEF__FXT_CFG.cliCfg.ocCfg),
        dict(MUT._DEF__FXT_CFG.cliCfg.vcCfg),
        dict(MUT._DEF__FXT_CFG.gstCfg.usrCrd),
    )
    ocFactory = mock.MagicMock(return_value=OCmock())
    virtCtlFactory = mock.MagicMock()

    MUT.VMcreate(
        Request(),
        MUT.FuncParsVMcreate(
            fxtCfgObj=MUT._DEF__FXT_CFG,
            vmList=[],
            ownVMs=[],
            sshKeyDirs=sshKeyDirs,
        ),
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
        reuseExisting=True,
        cliCfg=NamedDict(ocCfg={'context': 'custom'}),
        gstCfg=NamedDict(usrCrd=NamedDict(sshUsr='custom-user')),
    )
    MUT.VMcreate(
        Request(),
        MUT.FuncParsVMcreate(
            fxtCfgObj=MUT._DEF__FXT_CFG,
            vmList=[],
            ownVMs=[],
            sshKeyDirs=sshKeyDirs,
        ),
        ocCLI=ocFactory,
        virtCtlCLI=virtCtlFactory,
        reuseExisting=True,
    )

    assert ((
        dict(MUT._DEF__FXT_CFG.cliCfg.ocCfg),
        dict(MUT._DEF__FXT_CFG.cliCfg.vcCfg),
        dict(MUT._DEF__FXT_CFG.gstCfg.usrCrd),
    ) == defaultNestedValues)
    assert (ocFactory.call_args_list == [
        mock.call(ns='default', context='custom'),
        mock.call(ns='default'),
    ])
    assert (virtCtlFactory.call_args_list == [
        mock.call(ns='default'),
        mock.call(ns='default'),
    ])
    assert (MUT.VM.call_args_list[0].kwargs['sshUsr'] == 'custom-user')
    assert (MUT.VM.call_args_list[0].kwargs['sshKey'] == \
        '/synthetic/private-key')
    assert (MUT.VM.call_args_list[1].kwargs['sshUsr'] == 'qa-usr')
    assert (MUT.VM.call_args_list[1].kwargs['sshKey'] == \
        '/synthetic/private-key')


@pytest.mark.usefixtures('localPreparation')
def test_FxtVM_InitialNegativeFailsBeforeYieldWithoutCleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    generator = MUT.FxtVM.__wrapped__(
        Request({'count': -1}),
        mock.MagicMock(),
        mock.MagicMock(),
    )

    with pytest.raises(ValueError, match='must not be negative'):
        next(generator)

    MUT.VM.CleanUpVMs.assert_not_called()


@pytest.mark.usefixtures('localPreparation')
def test_FxtVM_InitialZeroYieldsFrozenEmptyBundleWithoutCleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    generator = MUT.FxtVM.__wrapped__(
        Request({'count': 0}),
        mock.MagicMock(),
        mock.MagicMock(),
    )

    bundle = next(generator)

    assert isinstance(bundle, MUT.FxtObjVM)
    assert (bundle.vmList == [])
    with pytest.raises(FrozenInstanceError):
        bundle.vmList = []
    generator.close()
    MUT.VM.CleanUpVMs.assert_not_called()


@pytest.mark.usefixtures('localPreparation')
def test_FxtVM_InitialSuccessUsesSelectedConfigAndCleansOnClose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT.YAMLcfg, 'Render')
    MUT.YAMLcfg.Render.return_value = 'yaml'
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
    ]
    vm = mock.MagicMock(name='initialVM')
    MUT.VM.return_value = vm
    oc = OCmock()
    oc.vmNewRes.return_value = (
        SimpleNamespace(name='WAITED'),
        CommandResult(True),
        {'metadata': {'uid': 'initial-uid'}},
    )
    ocFactory = mock.MagicMock(return_value=oc)
    virtCtlFactory = mock.MagicMock()
    generator = MUT.FxtVM.__wrapped__(
        request=Request({
            'count': 1,
            'ns': 'initial-ns',
            'namePfx': 'initial',
            'cfgFile': 'initial.yaml',
            'cliCfg': NamedDict(
                ocCfg={'context': 'initial-context'},
                vcCfg={'kubeconfig': '/initial-config'},
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
    MUT.YAMLcfg.Render.assert_called_once_with(
        Path('/repo/conf/initial.yaml'),
        mock.ANY,
    )
    generator.close()
    MUT.VM.CleanUpVMs.assert_called_once_with(
        bundle.vmList,
        [(oc, 'VirtualMachine/initial', 'initial-uid')],
    )


@pytest.mark.usefixtures('localPreparation')
def test_FxtVM_PerCallReturnsAreAggregatedAcrossBatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT.YAMLcfg, 'Render')
    MUT.YAMLcfg.Render.return_value = 'yaml'
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
        (True, '', ''),
        (True, '', ''),
        (True, '', ''),
        (True, '', ''),
        (True, '', ''),
    ]
    vmOne = mock.MagicMock(name='vmOne')
    vmTwo = mock.MagicMock(name='vmTwo')
    vmThree = mock.MagicMock(name='vmThree')
    MUT.VM.side_effect = [vmOne, vmTwo, vmThree]
    ocFactory = mock.MagicMock()
    ocFactory.return_value = OCmock()
    ocFactory.return_value.vmNewRes.return_value = (
        SimpleNamespace(name='APPLIED'),
        CommandResult(True),
        {'metadata': {'uid': 'owned-uid'}},
    )
    generator = MUT.FxtVM.__wrapped__(
        Request({'count': 0}),
        ocFactory,
        mock.MagicMock(),
    )
    bundle = next(generator)

    firstBatch = bundle.vmCreate(count=1, namePfx='first')
    secondBatch = bundle.vmCreate(count=2, namePfx='second')

    assert (firstBatch == [vmOne])
    assert (secondBatch == [vmTwo, vmThree])
    assert (bundle.vmList == [vmOne, vmTwo, vmThree])
    generator.close()
    MUT.VM.CleanUpVMs.assert_called_once_with(bundle.vmList, [
        (ocFactory.return_value, 'VirtualMachine/first', 'owned-uid'),
        (ocFactory.return_value, 'VirtualMachine/second-0', 'owned-uid'),
        (ocFactory.return_value, 'VirtualMachine/second-1', 'owned-uid'),
    ])


@pytest.mark.usefixtures('localPreparation')
def test_FxtVM_VMcreateAllowsCLIOverrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    OCcli.GetResUID.return_value = (True, 'existing', '')
    defaultOC = mock.MagicMock()
    defaultVirtCtl = mock.MagicMock()
    selectedOC = mock.MagicMock(return_value=OCmock())
    selectedVirtCtl = mock.MagicMock()
    generator = MUT.FxtVM.__wrapped__(
        Request({'count': 0}),
        defaultOC,
        defaultVirtCtl,
    )
    bundle = next(generator)

    created = bundle.vmCreate(
        count=1,
        reuseExisting=True,
        ns='selected-ns',
        cliCfg=NamedDict(
            ocCfg={'context': 'selected-context'},
            vcCfg={'kubeconfig': '/selected-config'},
        ),
        ocCLI=selectedOC,
        virtCtlCLI=selectedVirtCtl,
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


@pytest.mark.usefixtures('localPreparation')
@pytest.mark.parametrize(
    'argument', ['request', 'fPars'],
)
def test_FxtVM_VMcreateRejectsInternalKeywordReplacementBeforeOperations(
    monkeypatch: pytest.MonkeyPatch,
    argument: str,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()
    generator = MUT.FxtVM.__wrapped__(
        Request({'count': 0}),
        ocFactory,
        virtCtlFactory,
    )
    bundle = next(generator)
    sharedVMs = bundle.vmCreate.args[1].vmList
    sharedOwnership = bundle.vmCreate.args[1].ownVMs

    with pytest.raises(TypeError, match='multiple values'):
        bundle.vmCreate(**{argument: object()})

    assert (bundle.vmCreate.args[1].vmList is sharedVMs)
    assert (bundle.vmCreate.args[1].ownVMs is sharedOwnership)
    assert (bundle.vmList is sharedVMs)
    assert (bundle.vmList == [])
    assert (sharedOwnership == [])
    OCcli.GetResUID.assert_not_called()
    ocFactory.assert_not_called()
    virtCtlFactory.assert_not_called()
    generator.close()


@pytest.mark.usefixtures('localPreparation')
def test_FxtVM_PreyieldPartialFailureCleansCreatedVMs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT.YAMLcfg, 'Render')
    MUT.YAMLcfg.Render.return_value = 'yaml'
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
        (True, '', ''),
    ]
    vmZero = mock.MagicMock(name='vmZero')
    MUT.VM.return_value = vmZero
    ocZero = OCmock('ocZero')
    ocOne = OCmock('ocOne')
    ocZero.vmNewRes.return_value = (
        SimpleNamespace(name='WAITED'),
        CommandResult(True),
        {'metadata': {'uid': 'uid-zero'}},
    )
    ocOne.vmNewRes.return_value = (
        SimpleNamespace(name='WAITED'),
        CommandResult(False, 'second apply failed'),
        {'metadata': {'uid': 'failed-uid'}},
    )
    virtCtlFactory = mock.MagicMock()
    generator = MUT.FxtVM.__wrapped__(
        Request({'count': 2, 'namePfx': 'preyield'}),
        mock.MagicMock(side_effect=[ocZero, ocOne]),
        virtCtlFactory,
    )

    with pytest.raises(RuntimeError) as raised:
        next(generator)

    assert (str(raised.value) == (
        'VM `preyield-1` creation failed at `WAITED`: second apply failed'
    ))
    MUT.VM.CleanUpVMs.assert_called_once_with(
        [vmZero],
        [
            (ocZero, 'VirtualMachine/preyield-0', 'uid-zero'),
            (ocOne, 'VirtualMachine/preyield-1', 'failed-uid'),
        ],
    )
    MUT.VM.assert_called_once()
    assert (MUT.VM.call_args.kwargs['name'] == 'preyield-0')
    virtCtlFactory.assert_called_once()


@pytest.mark.usefixtures('localPreparation')
def test_FxtVM_PostyieldPartialFailureCleansCreatedVMsOnClose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, OCcli, 'GetResUID')
    Utils.AutoPatch(monkeypatch, MUT, 'VM')
    Utils.AutoPatch(monkeypatch, MUT.YAMLcfg, 'Render')
    MUT.YAMLcfg.Render.return_value = 'yaml'
    OCcli.GetResUID.side_effect = [
        (True, '', ''),
        (True, '', ''),
    ]
    vmZero = mock.MagicMock(name='vmZero')
    MUT.VM.return_value = vmZero
    ocZero = OCmock('ocZero')
    ocOne = OCmock('ocOne')
    ocZero.vmNewRes.return_value = (
        SimpleNamespace(name='WAITED'),
        CommandResult(True),
        {'metadata': {'uid': 'uid-zero'}},
    )
    ocOne.vmNewRes.return_value = (
        SimpleNamespace(name='WAITED'),
        CommandResult(False, 'second apply failed'),
        {'metadata': {'uid': 'failed-uid'}},
    )
    virtCtlFactory = mock.MagicMock()
    generator = MUT.FxtVM.__wrapped__(
        Request({'count': 0}),
        mock.MagicMock(side_effect=[ocZero, ocOne]),
        virtCtlFactory,
    )
    bundle = next(generator)

    with pytest.raises(RuntimeError) as raised:
        bundle.vmCreate(count=2, namePfx='postyield')

    assert (str(raised.value) == (
        'VM `postyield-1` creation failed at `WAITED`: second apply failed'
    ))
    assert (bundle.vmList == [vmZero])
    MUT.VM.assert_called_once()
    assert (MUT.VM.call_args.kwargs['name'] == 'postyield-0')
    virtCtlFactory.assert_called_once()
    MUT.VM.CleanUpVMs.assert_not_called()
    generator.close()
    MUT.VM.CleanUpVMs.assert_called_once_with(
        [vmZero],
        [
            (ocZero, 'VirtualMachine/postyield-0', 'uid-zero'),
            (ocOne, 'VirtualMachine/postyield-1', 'failed-uid'),
        ],
    )


@pytest.mark.usefixtures('localPreparation')
def test_FxtVM_PreyieldCleanupFailurePreservesOriginalExceptionIdentity(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VMcreate')
    primary = RuntimeError('primary creation failure')
    oc = OCmock()
    oc.Run.side_effect = None
    oc.Run.return_value = CommandResult(False, 'cleanup lookup failed')
    oc.GetResUID.return_value = (False, '', 'cleanup lookup failed')

    def FailCreate(
        request: object,
        fPars: MUT.FuncParsVMcreate,
        **kwargs: object,
    ) -> list[object]:
        fPars.ownVMs.append(
            (oc, 'VirtualMachine/partial', 'partial-uid'),
        )
        raise primary

    MUT.VMcreate.side_effect = FailCreate
    generator = MUT.FxtVM.__wrapped__(
        Request(),
        mock.MagicMock(),
        mock.MagicMock(),
    )

    with caplog.at_level('ERROR'), pytest.raises(RuntimeError) as raised:
        next(generator)

    assert (raised.value is primary)
    assert ('preserving primary exception' in caplog.text)


def Create(state: SimpleNamespace) -> tuple[list[Any], list[Any]]:
    sshKeyDirs = state.sshKeyDirs
    ownVMs: list[Any] = []
    source = state.cfg['gstCfg']['imgSrc']['CDI']
    cfg = {
        **state.cfg,
        'gstCfg': MUT.NamedDict(
            imgSrc=MUT.NamedDict(
                CDI=MUT.NamedDict(
                    **source | ({
                        'credInfo': MUT.NamedDict(source['credInfo']),
                    } if isinstance(source.get('credInfo'), dict) else {}),
                ),
            ),
            usrCrd=MUT.NamedDict(
                **state.cfg['gstCfg']['usrCrd'] | {
                    'sshKey': NamedDict(
                        state.cfg['gstCfg']['usrCrd']['sshKey'],
                    ),
                },
            ),
        ),
    }
    state.request.param = cfg | {'count': 0}
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, VirtCtlCLI,
    )
    bundle = next(generator)
    state.request.addfinalizer(generator.close)
    ownVMs = bundle.vmCreate.args[1].ownVMs
    result = MUT.VMcreate(
        state.request,
        MUT.FuncParsVMcreate(
            fxtCfgObj=bundle.vmCreate.args[1].fxtCfgObj,
            vmList=bundle.vmList,
            ownVMs=ownVMs,
            ownSecrets=bundle.vmCreate.args[1].ownSecrets,
            ownNSs=bundle.vmCreate.args[1].ownNSs,
            sshKeyDirs=sshKeyDirs,
        ),
        ocCLI=state.ocFactory,
        virtCtlCLI=lambda ns, **opts: VirtCtlCLI(
            'virtctl-unit', cmdExec=state.recorder, n=ns, **opts,
        ),
        count=cfg['count'],
    )
    return (result, ownVMs)


def test_VMcreate_NativeSecretFilesPreserveNormalizedNUL(
    localContext: SimpleNamespace,
) -> None:
    state = localContext
    (state.keyPath.parent / 'access').write_bytes(b' \taccess\x00value\r\n')
    (state.keyPath.parent / 'relative-secret').write_bytes(
        b' secret\x00value \r\n'
    )
    Create(state)
    secret = next(resource for resource in state.resources
                  if (resource['kind'] == 'Secret'))
    assert ({key: base64.b64decode(value)
            for (key, value) in secret['data'].items()} == {
        'accessKeyId': b'access\x00value', 'secretKey': b'secret\x00value',
    })
    paths = [
        Path(source.split('=', 1)[1])
        for command in state.commands
        for part in command
        if part.startswith('--from-file=')
        for source in next(csv.reader([part.split('=', 1)[1]]))
    ]
    assert (len(paths) == 2)
    assert all(
        (not path.exists() and not path.parent.exists())
        for path in paths
    )
    assert not any(
        ('\x00' in part)
        for command in state.commands
        for part in command
    )


@pytest.mark.parametrize(
    'case',
    [
        'owned',
        'existing',
        'different',
        'render-failure',
        'uid-changed',
        'vm-wait-failure',
        'secret-delete-failed',
        'namespace-delete-failed',
        'existing-uid-changed',
        'vm-uid-changed',
        'namespace-uid-changed',
    ],
)
def test_FxtVM_OwnedResourceLifecycleThroughNativeConsumer(
    localContext: SimpleNamespace,
    fxtVirtCtlCLI: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: str,
) -> None:
    state = localContext
    (ns, name) = 'context-ns', 'aws-s3--cred'
    secret = {
        'apiVersion': 'v1',
        'kind': 'Secret',
        'metadata': {'name': name, 'namespace': ns, 'uid': 'existing-secret'},
        'data': {
            'secretKey': base64.b64encode(b'synthetic-secret-value').decode(),
            'accessKeyId': base64.b64encode(
                b'synthetic-access-value'
            ).decode(),
        },
    }
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(
        ns=ns, namePfx='owned-vm', count=2
    )
    if (case in ('existing', 'different', 'existing-uid-changed')):
        state.namespaces[ns] = {
            'metadata': {'uid': 'existing-ns'},
            'status': {'phase': 'Active'},
        }
    else:
        state.namespaces[ns] = None
    if (case in ('existing', 'different')):
        if (case == 'different'): secret['data']['secretKey'] = 'eA=='
        state.secrets[(ns, name)] = secret
    primary = RuntimeError('synthetic-render-failure')
    if (case == 'render-failure'):
        def FailRender(*args: Any) -> str:
            raise primary
        monkeypatch.setattr(MUT.YAMLcfg, 'Render', FailRender)
    originalRun = subprocess.run
    deletions = []

    def Run(command: list[str], **kwargs: Any) -> Any:
        if (
            (case == 'vm-wait-failure')
            and ('wait' in command)
            and command[-1].startswith('VirtualMachine/')
        ):
            return subprocess.CompletedProcess(
                command, 1, '', 'synthetic-VM-wait-failure'
            )
        if (('delete' in command) and ('wait' not in command)):
            deletions.append(command[-1])
            if ((case == 'secret-delete-failed') and command[-1].startswith(
                'Secret/'
            )):
                return subprocess.CompletedProcess(
                    command, 1, '', 'synthetic-delete-failure'
                )
            if ((case == 'namespace-delete-failed') and command[-1].startswith(
                'Namespace/'
            )):
                return subprocess.CompletedProcess(
                    command, 1, '', 'synthetic-delete-failure'
                )
        return originalRun(command, **kwargs)

    monkeypatch.setattr(subprocess, 'run', Run)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI
    )
    if (case in (
        'different',
        'render-failure',
        'vm-wait-failure',
    )):
        with pytest.raises(RuntimeError) as caught:
            next(generator)
        if (case == 'render-failure'): assert (caught.value is primary)
        assert not state.present
        if (case == 'different'):
            assert (state.secrets[(ns, name)] is secret)
            assert not deletions
        else:
            assert (not state.secrets and (ns not in state.namespaces))
        expected = []
        if (case == 'vm-wait-failure'):
            expected.append('VirtualMachine/owned-vm-0')
        if (case in ('render-failure', 'vm-wait-failure')):
            expected.append(f'Secret/{name}')
        if (case in ('render-failure', 'vm-wait-failure')):
            expected.append(f'Namespace/{ns}')
        assert (deletions == expected)
        return
    bundle = next(generator)
    assert not any(
        (('wait' in command) and command[-1].startswith('Namespace/'))
        for command in state.commands
    )
    assert (len(bundle.vmList) == 2)
    pars = bundle.vmCreate.args[1]
    if (case in ('existing',)):
        assert (pars.ownSecrets == pars.ownNSs == [])
    elif (case == 'existing-uid-changed'):
        assert ((len(pars.ownSecrets) == 1) and not pars.ownNSs)
    else:
        assert (len(pars.ownSecrets) == len(pars.ownNSs) == 1)
        assert ([item['kind'] for item in state.resources] == [
            'Namespace', 'Secret', 'VirtualMachine', 'VirtualMachine',
        ])
    if (case in (
        'uid-changed',
        'existing-uid-changed',
        'namespace-uid-changed',
    )):
        state.secrets[(ns, name)]['metadata']['uid'] = 'replacement-uid'
    if (case in (
        'vm-uid-changed',
        'existing-uid-changed',
        'namespace-uid-changed',
    )):
        for vmName in state.present: state.present[vmName] = 'replacement-vm'
    if (case == 'namespace-uid-changed'):
        state.namespaces[ns]['metadata']['uid'] = 'replacement-namespace'
    if (case in ('secret-delete-failed', 'namespace-delete-failed')):
        with pytest.raises(
            RuntimeError, match='Resource cleanup failed'
        ) as caught:
            next(generator)
        resourceRef = (
            f'Secret/{name}'
            if (case == 'secret-delete-failed')
            else f'Namespace/{ns}'
        )
        diagnostic = f'Delete `{resourceRef}` at `DELETED`'
        assert ((diagnostic in str(caught.value)) and (diagnostic
            in caplog.text))
        assert ('synthetic-delete-failure' in str(caught.value))
    else:
        generator.close()
    if (case in ('existing-uid-changed', 'namespace-uid-changed')):
        assert (state.present == {
            'owned-vm-0': 'replacement-vm',
            'owned-vm-1': 'replacement-vm',
        })
        assert not deletions
    else:
        assert not state.present
    if (case in ('existing', 'existing-uid-changed', 'namespace-uid-changed')):
        assert ((ns, name) in state.secrets)
        if (case in ('existing',)):
            assert (state.secrets[(ns, name)]['data'] == secret['data'])
    else:
        assert not state.secrets
    if (case == 'namespace-delete-failed'):
        assert (state.namespaces[ns]['metadata']['uid'] == 'owned-Namespace')
        assert (deletions[-1] == f'Namespace/{ns}')
    elif (case in ('namespace-uid-changed',)):
        assert (
            state.namespaces[ns]['metadata']['uid'] == 'replacement-namespace'
        )
    elif (case in ('existing', 'existing-uid-changed')):
        assert (state.namespaces[ns] == {
            'metadata': {'uid': 'existing-ns'}, 'status': {'phase': 'Active'},
        })
        assert not any(path.endswith(f'Namespace/{ns}') for path in deletions)
    else:
        assert (ns not in state.namespaces)
        assert deletions[-1].endswith('Namespace/context-ns')
    if (case in ('owned', 'uid-changed')):
        assert deletions[0].endswith('VirtualMachine/owned-vm-0')
        assert deletions[1].endswith('VirtualMachine/owned-vm-1')
        if (case == 'owned'): assert ('Secret/' in deletions[2])
        assert deletions[-1].endswith('Namespace/context-ns')
    if (case == 'uid-changed'):
        assert not any(('Secret/' in path) for path in deletions)
    if (case == 'vm-uid-changed'):
        assert not any(('VirtualMachine/' in path) for path in deletions)


@pytest.mark.parametrize(
    'keyForm', [
        'literal', 'env', 'braced-env', 'home', 'env-home',
        'normalized',
    ],
)
def test_VMcreate_IdentityPathMatchesGuestConsumer(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    keyForm: str,
) -> None:
    state = localContext
    monkeypatch.setenv('VMCTX__KEY_DIR', str(state.keyPath.parent))
    monkeypatch.setenv('HOME', str(state.keyPath.parent))
    monkeypatch.setenv('VMCTX__KEY_HOME', '~/private-key')
    keyPath = {
        'literal': str(state.keyPath),
        'env': '$VMCTX__KEY_DIR/private-key',
        'braced-env': '${VMCTX__KEY_DIR}/private-key',
        'home': '~/private-key',
        'env-home': '$VMCTX__KEY_HOME',
        'normalized': f'{state.keyPath.parent}//./private-key',
    }[keyForm]
    state.cfg['gstCfg']['usrCrd']['sshKey'] = NamedDict(prv=keyPath, pub=None)
    monkeypatch.setattr(RHOVsshCon, 'Probe', lambda self, **opts: True)
    (vms, owned) = Create(state)
    connection = vms[0].NewSSH()
    assert (vms[0].sshKey == str(state.keyPath))
    assert (connection.idFile == str(state.keyPath))
    assert ((state.keyCalls == 1) and (len(owned) == 1))
    assert (base64.b64encode(state.publicKey.encode()).decode() in (
        yaml.safe_dump(state.resources[-1])
    ))


@pytest.mark.parametrize('fld', ['prv', 'pub'])
def test_FxtVM_SuppliedPathObjectsFailNativeMergeBeforeResourceEffects(
    localContext: SimpleNamespace, fld: str,
) -> None:
    state = localContext
    pub = state.keyPath.parent / 'supplied.pub'
    pub.write_text(state.publicKey)
    paths = {'prv': state.keyPath, 'pub': pub}
    key = {'prv': str(state.keyPath), 'pub': str(pub)}
    key[fld] = paths[fld]
    state.cfg['gstCfg']['usrCrd']['sshKey'] = NamedDict(key)
    before = json.dumps(state.cfg, default=str)
    files = [(path, path.read_bytes(), path.stat()) for path in paths.values()]

    expected = f'expected `{type(paths[fld]).__name__}`, received `str`'
    with pytest.raises(TypeError, match=expected):
        Create(state)

    assert (state.resources == state.commands == [])
    assert (json.dumps(state.cfg, default=str) == before)
    assert (state.cfg['gstCfg']['usrCrd']['sshKey'][fld] is paths[fld])
    for (path, content, info) in files:
        assert (path.read_bytes() == content)
        assert (path.stat().st_mode == info.st_mode)
        assert (path.stat().st_mtime_ns == info.st_mtime_ns)


@pytest.mark.parametrize('credType', ['None', 'K8s'])
def test_VMcreate_CustomContextRetainsTemplateFieldsAndManagedCredentials(
    localContext: SimpleNamespace, credType: str,
) -> None:
    state = localContext
    state.cfg['vmCfg'].ctxData.update({
        'vmName': 'ignored-name', 'vmNS': 'ignored-ns',
        'sshPublicKeyB64': 'ignored-public',
        'vmDataVolumeSource': {'http': {
            'url': 'https://example.invalid/ignored',
        }},
        'vmCpuCores': 2, 'vmDataVolumeName': 'custom-disk',
        'vmStorageClass': 'custom-storage', 'vmStorageSize': '42Gi',
    })
    state.cfg['gstCfg']['imgSrc']['CDI']['credType'] = credType

    (vms, owned) = Create(state)

    resource = state.resources[-1]
    assert (resource['metadata']['name'] == 'context-vm')
    assert (resource['metadata']['namespace'] == 'context-ns')
    volume = resource['spec']['dataVolumeTemplates'][0]
    assert (volume['metadata']['name'] == 'custom-disk')
    assert (volume['spec']['storage']['storageClassName'] == 'custom-storage')
    assert (volume['spec']['storage']['resources']['requests'] == {
        'storage': '42Gi',
    })
    domain = resource['spec']['template']['spec']['domain']
    assert (domain['cpu']['cores'] == 2)
    source = {'url': state.cfg['gstCfg']['imgSrc']['CDI']['url']}
    if (credType == 'K8s'):
        source['secretRef'] = 'aws-s3--cred'
        secret = state.resources[0]
        assert (secret['kind'] == 'Secret')
        assert ({
            name: base64.b64decode(value).decode()
            for (name, value) in secret['data'].items()
        } == {
            'accessKeyId': 'synthetic-access-value',
            'secretKey': 'synthetic-secret-value',
        })
    else:
        assert (state.secrets == {})
    assert (volume['spec']['source'] == {
        's3' if (credType == 'K8s') else 'http': source,
    })
    rendered = yaml.safe_dump(resource)
    assert (base64.b64encode(state.publicKey.encode()).decode() in rendered)
    assert ('ignored-' not in rendered)
    assert (len(vms) == len(owned) == 1)


def test_VMcreate_ActualRenderRawS3PublicKeyAndClientContext(
    localContext: SimpleNamespace, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    state = localContext
    (vms, owned) = Create(state)
    (secret, vm) = state.resources
    assert (secret['type'] == 'Opaque')
    assert (secret['metadata'] == {
        'name': 'aws-s3--cred',
        'namespace': 'context-ns',
        'uid': 'owned-Secret',
    })
    assert ({
        name: base64.b64decode(value).decode()
        for (name, value) in secret['data'].items()
    } == {
        'accessKeyId': 'synthetic-access-value',
        'secretKey': 'synthetic-secret-value',
    })
    assert (vm['metadata']['name'] == 'context-vm')
    assert (vm['metadata']['namespace'] == 'context-ns')
    volume = vm['spec']['dataVolumeTemplates'][0]
    assert (volume['spec']['source'] == {'s3': {
        'secretRef': 'aws-s3--cred',
        'url': state.cfg['gstCfg']['imgSrc']['CDI']['url'],
    }})
    assert (volume['spec']['storage']['storageClassName'] == 'gp3-csi')
    assert (volume['spec']['storage']['resources']['requests'] == {
        'storage': '96Gi',
    })
    assert (base64.b64encode(state.publicKey.encode()).decode() in (
        yaml.safe_dump(vm)
    ))
    assert not state.keyPath.with_suffix('.pub').exists()
    assert ((len(owned) == 1) and (owned[0][2] == 'owned-uid'))
    assert (vms[0].namespace == 'context-ns')
    assert (vms[0].sshKey == str(state.keyPath))
    for command in state.commands:
        if (command[-1] == 'options'): continue
        assert (command[(command.index('-n') + 1)] == 'context-ns')
        assert (command[command.index('--context') + 1] == 'fixture-context')
        if (command[0] == 'oc-unit'):
            assert (command[command.index('--kubeconfig') + 1]
                == '/mock/config')
    assert all((client.cmdExec is state.recorder) for client in state.clients)
    retained = repr(state.recorder.inputs) + caplog.text
    assert ('synthetic-access-value' not in retained)
    assert ('synthetic-secret-value' not in retained)
    assert (secret['data']['secretKey'] not in retained)
    assert ('PRIVATE KEY' not in retained)
    state.cfg['namePfx'] = 'second-vm'
    (state.keyPath.parent / 'relative-secret').write_text('updated-synthetic')
    with pytest.raises(
        RuntimeError, match='different data.*Refusing to overwrite'
    ):
        Create(state)
    assert ([item['kind'] for item in state.resources] == [
        'Secret',
        'VirtualMachine',
    ])
    assert (len(state.secrets) == 1)
    updated = state.secrets[('context-ns', 'aws-s3--cred')]
    assert (base64.b64decode(updated['data']['secretKey']) == (
        b'synthetic-secret-value'
    ))
    assert (updated['data']['accessKeyId'] == secret['data']['accessKeyId'])
    assert all((client.cmdExec is state.recorder) for client in state.clients)
    retained = repr(state.recorder.inputs) + caplog.text
    assert ('updated-synthetic' not in retained)
    assert (updated['data']['secretKey'] not in retained)


_TEXT_FIELDS = [
    ('gstCfg', 'imgSrc', 'CDI', 'url'),
    ('gstCfg', 'imgSrc', 'CDI', 'credInfo', 'name'),
    ('gstCfg', 'imgSrc', 'CDI', 'credInfo', 'vaultInfo', 'file--AccKeyId'),
    ('gstCfg', 'imgSrc', 'CDI', 'credInfo', 'vaultInfo', 'file--AccKeySecret'),
    ('gstCfg', 'usrCrd', 'sshKey', 'prv'),
]


@pytest.mark.parametrize('fldPath', _TEXT_FIELDS)
@pytest.mark.parametrize('badValue', ['', ' '])
def test_VMcreate_EmptyValuesFailBeforeResourceEffects(
    localContext: SimpleNamespace, fldPath: tuple[str, ...],
    badValue: str, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    state = localContext
    parent = state.cfg
    for fld in fldPath[:-1]: parent = parent[fld]
    parent[fldPath[-1]] = badValue
    with pytest.raises(ValueError) as caught:
        Create(state)
    expected = ('Public Key resolution failed'
                if ((fldPath[-1] == 'prv') and (badValue == ''))
                else fldPath[-1])
    assert (expected in str(caught.value))
    assert (state.resources == [])
    assert not any(('apply' in cmd) for cmd in state.commands)
    assert ('synthetic-secret-value' not in (str(caught.value) + caplog.text))


@pytest.mark.parametrize('fld,value', [
    ('type', 'HTTP'), ('credType', 'BitWarden'),
    ('vaultType', 'BitWarden'), ('vaultType', 'None'),
    ('vaultType', 'FutureVault'), ('vaultType', ''), ('vaultType', None),
    ('url', 'not-a-url'),
    ('url', 'https://[invalid-host'),
    ('url', 'https://:443/image.qcow2'),
    ('url', 'https://user@:443/image.qcow2'),
])
@pytest.mark.parametrize('customContext', [False, True])
def test_VMcreate_UnsupportedSourceFailsBeforeResourceEffects(
    localContext: SimpleNamespace, fld: str, value: Any,
    customContext: bool,
) -> None:
    state = localContext
    if (customContext):
        state.cfg['vmCfg'].ctxData.update({
            'customTemplateField': 'retained',
            'vmDataVolumeSource': {'http': {
                'url': 'https://example.invalid/ignored',
            }},
        })
    source = state.cfg['gstCfg']['imgSrc']['CDI']
    if (fld == 'vaultType'): source = source['credInfo']
    source[fld] = value
    with pytest.raises(ValueError, match=fld) as caught: Create(state)
    if (fld != 'url'):
        assert (str(caught.value) == {
            'type': (
                "gstCfg.imgSrc.CDI.type must be one of ['AWS-S3']."
            ),
            'credType': (
                "gstCfg.imgSrc.CDI.credType must be one of ['K8s', 'None']."
            ),
            'vaultType': (
                'gstCfg.imgSrc.CDI.credInfo.vaultType '
                "must be one of ['LocalFile']."
            ),
        }[fld])
    assert (state.resources == [])
    assert not any(('apply' in cmd) for cmd in state.commands)


@pytest.mark.parametrize('fld', ['file--AccKeyId', 'file--AccKeySecret'])
@pytest.mark.parametrize('case', ['missing', 'directory', 'empty', 'binary',
                                  'multiline'])
def test_VMcreate_UnreadableOrInvalidRawValueFailsBeforeEffects(
    localContext: SimpleNamespace, fld: str, case: str,
) -> None:
    state = localContext
    path = state.keyPath.parent / 'bad-credential'
    if (case == 'directory'): path.mkdir()
    elif (case != 'missing'):
        path.write_bytes({
            'empty': b' \n', 'binary': b'\xff', 'multiline': b'one\ntwo',
        }[case])
    vaultInfo = state.cfg['gstCfg']['imgSrc']['CDI']['credInfo']['vaultInfo']
    vaultInfo[fld] = str(path)
    with pytest.raises(ValueError, match=fld): Create(state)
    assert (state.resources == [])


@pytest.mark.parametrize('case', ['missing', 'directory', 'invalid',
                                  'encrypted'])
def test_FxtVM_PrivateKeyFailureIsNoninteractiveAndSanitized(
    localContext: SimpleNamespace, case: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    state = localContext
    path = state.keyPath
    if (case == 'missing'): path.unlink()
    elif (case == 'directory'):
        state.cfg['gstCfg']['usrCrd']['sshKey'] = NamedDict(
            prv=str(path.parent), pub=None,
        )
    elif (case == 'invalid'): path.write_text('PRIVATE KEY synthetic-secret')
    else:
        result = state.nativeRun(
            ['ssh-keygen', '-p', '-P', '', '-N', 'synthetic-passphrase',
             '-f', str(path)],
            capture_output=True, text=True, timeout=30,
        )
        assert (result.returncode == 0)
    with pytest.raises(ValueError, match='sshKey') as caught:
        Create(state)
    assert (state.resources == [])
    assert ('PRIVATE KEY' not in (str(caught.value) + caplog.text))


@pytest.mark.parametrize('phase', [
    'create', 'os-error', 'exception', 'secret-timeout',
])
def test_VMcreate_SecretFailureSuppressesPrivateProcessDiagnostics(
    localContext: SimpleNamespace, phase: str,
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    state = localContext
    state.fail = phase
    results = []
    originalRun = state.recorder.Run

    def Run(command: list[str], **kwargs: Any) -> Any:
        result = originalRun(command, **kwargs)
        if (('secret' in command) and ('generic' in command)):
            results.append(result)
        return result

    monkeypatch.setattr(state.recorder, 'Run', Run)
    with pytest.raises(
        RuntimeError, match='Could not prepare image Secret'
    ) as caught:
        Create(state)
    assert (state.resources == [])
    assert all((client.cmdExec is state.recorder) for client in state.clients)
    text = str(caught.value) + caplog.text
    if (phase == 'secret-timeout'):
        assert (len(state.secretTimeouts) == 1)
        assert isinstance(state.secretTimeouts[0], subprocess.TimeoutExpired)
        assert (state.secretTimeouts[0].output == 'synthetic-secret-value')
        assert (len(results) == 1)
        assert ((results[0].exitCode == -2) and not results[0].success)
        assert (results[0].stderr == str(state.secretTimeouts[0]))
        assert caught.value.__suppress_context__
    assert ('synthetic-secret-value' not in caplog.text)
    assert ('synthetic-secret-value' not in str(caught.value))
    assert ('synthetic-access-value' not in text)
    assert ('PRIVATE KEY' not in text)


@pytest.mark.parametrize('case', ['key-timeout', 'key-empty'])
def test_FxtVM_KeyTimeoutOrEmptyOutputFailsBeforeEffects(
    localContext: SimpleNamespace, case: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    state = localContext
    state.fail = case
    with pytest.raises(ValueError, match='sshKey') as caught:
        Create(state)
    assert (state.keyCalls == 1)
    assert (state.resources == [])
    assert not any(('apply' in cmd) for cmd in state.commands)
    text = str(caught.value) + caplog.text
    assert ('PRIVATE KEY' not in text)
    assert ('synthetic-secret-value' not in text)


def test_VMcreate_RenderFailureLeavesSecretOwnedForFixtureCleanup(
    localContext: SimpleNamespace,
) -> None:
    state = localContext
    state.cfg['cfgFile'] = 'missing-template.yaml'
    with pytest.raises(FileNotFoundError, match='Template file not found'):
        Create(state)
    assert (state.keyCalls == 1)
    assert ([resource['kind'] for resource in state.resources] == ['Secret'])
    assert (len(state.secrets) == 1)


def test_VMcreate_MixedReuseAndMultipleNewVMsRenderAndOwnOnlyNew(
    localContext: SimpleNamespace,
) -> None:
    state = localContext
    state.cfg.update(count=3, reuseExisting=True)
    state.present['context-vm-0'] = 'existing-uid'
    (vms, owned) = Create(state)
    assert ([vm.name for vm in vms] == [
        'context-vm-0', 'context-vm-1', 'context-vm-2',
    ])
    assert ([(name, uid) for (_, name, uid) in owned] == [
        ('VirtualMachine/context-vm-1', 'owned-uid'),
        ('VirtualMachine/context-vm-2', 'owned-uid'),
    ])
    assert ([item['kind'] for item in state.resources] == [
        'Secret', 'VirtualMachine', 'VirtualMachine',
    ])
    assert (state.keyCalls == 1)
    for (manifest, name) in zip(state.resources[1:], ('context-vm-1',
                                                    'context-vm-2')):
        assert (manifest['metadata']['name'] == name)
        assert (manifest['metadata']['namespace'] == 'context-ns')
        volume = manifest['spec']['dataVolumeTemplates'][0]
        assert (volume['metadata']['name'] == (name + '--vhd--00-system'))
        assert (volume['spec']['source'] == {'s3': {
            'secretRef': 'aws-s3--cred',
            'url': state.cfg['gstCfg']['imgSrc']['CDI']['url'],
        }})


def test_FxtVM_PartialNamedDictOverridesPreserveInputsAndDefaults(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = localContext
    initial = {
        'ns': 'context-ns', 'namePfx': 'initial-vm',
        'cliCfg': state.cfg['cliCfg'],
        'gstCfg': MUT.NamedDict(
            imgSrc=MUT.NamedDict(CDI=MUT.NamedDict(
                credInfo=MUT.NamedDict(vaultInfo={
                    'file--AccKeyId': str(state.keyPath.parent / 'access'),
                    'file--AccKeySecret': 'relative-secret',
                }),
            )),
            usrCrd=MUT.NamedDict(sshKey=NamedDict(
                prv=str(state.keyPath), pub=None,
            )),
        ),
    }
    later = {
        **initial, 'namePfx': 'later-vm',
        'gstCfg': MUT.NamedDict(
            imgSrc=initial['gstCfg'].imgSrc,
            usrCrd=MUT.NamedDict(
                sshUsr='override-user',
            ),
        ),
    }
    defaultsBefore = ConfigSnapshot(MUT._DEF__FXT_CFG)
    inputsBefore = json.loads(json.dumps([initial, later]))
    state.request.param = initial
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory,
        lambda ns, **opts: VirtCtlCLI(
            'virtctl-unit', cmdExec=state.recorder, n=ns, **opts,
        ),
    )
    try:
        bundle = next(generator)
        assert ([vm.name for vm in bundle.vmList] == ['initial-vm'])
        assert (bundle.vmList[0].sshUsr == 'qa-usr')
        assert (ConfigSnapshot(MUT._DEF__FXT_CFG) == defaultsBefore)
        assert (json.loads(json.dumps([initial, later])) == inputsBefore)
        created = bundle.vmCreate(**later)
        assert ([vm.name for vm in created] == ['later-vm'])
        assert (created[0].sshUsr == 'override-user')
        assert (
            [vm.name for vm in bundle.vmList] == ['initial-vm', 'later-vm']
        )
        assert (ConfigSnapshot(MUT._DEF__FXT_CFG) == defaultsBefore)
        assert (json.loads(json.dumps([initial, later])) == inputsBefore)
        assert (state.keyCalls == 1)
        for (cfg, manifest) in zip((initial, later), state.resources[1:]):
            assert (manifest['metadata']['name'] == cfg['namePfx'])
            source = (MUT._DEF__FXT_CFG | MUT.NamedDict(cfg)).gstCfg.imgSrc.CDI
            assert (source['type'] == 'AWS-S3')
            assert (source['credType'] == 'K8s')
            assert (source['credInfo']['vaultType'] == 'LocalFile')
            assert (source['credInfo']['name'] == 'aws-s3--cred')
            assert (source.url == (
                defaultsBefore['gstCfg']['imgSrc']['CDI']['url']
            ))
            assert (source['credInfo']['vaultInfo']['file--AccKeySecret'] == (
                'relative-secret'
            ))
            volume = manifest['spec']['dataVolumeTemplates'][0]
            assert (volume['spec']['source'] == {'s3': {
                'secretRef': 'aws-s3--cred', 'url': source['url'],
            }})
            assert (base64.b64encode(state.publicKey.encode()).decode() in (
                yaml.safe_dump(manifest)
            ))
    finally:
        generator.close()
    assert (state.present == {})
    assert (state.secrets == {})


def test__ResolveSSHkeys_ReturnsNewConfigurationAndPreservesCaller(
    localContext: SimpleNamespace,
    sshKeyDirs: list[TemporaryDirectory],
) -> None:
    cfg = MUT._DEF__FXT_CFG | NamedDict(_REGISTERED_CFG)
    before = json.dumps(cfg)
    resolved = MUT._ResolveSSHkeys(cfg, sshKeyDirs)
    assert (resolved is not cfg)
    assert (resolved.gstCfg is not cfg.gstCfg)
    assert (json.dumps(cfg) == before)
    assert (resolved.gstCfg.usrCrd.sshKey.prv == str(localContext.keyPath))
    pubKey = Path(resolved.gstCfg.usrCrd.sshKey.pub)
    assert (pubKey.read_text() == localContext.publicKey)
    assert (pubKey.parent == Path(sshKeyDirs[0].name))


def test_FxtVM_NoOverrideSkipsResolutionAndSharesOwnedDirectories(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = localContext
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(count=0)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    base = bundle.vmCreate.args[1].fxtCfgObj
    before = json.dumps(base)
    owned = bundle.vmCreate.args[1].sshKeyDirs
    nativeResolve = MUT._ResolveSSHkeys
    calls = []

    def Resolve(
        fxtCfg: NamedDict, sshKeyDirs: list[TemporaryDirectory],
    ) -> NamedDict:
        assert (sshKeyDirs is owned)
        resolved = nativeResolve(fxtCfg, sshKeyDirs)
        calls.append(resolved)
        return resolved

    monkeypatch.setattr(MUT, '_ResolveSSHkeys', Resolve)
    counts = (state.genCalls, state.keyCalls)
    try:
        assert (bundle.vmCreate() == [])
        assert (calls == [])
        assert (bundle.vmCreate(namePfx='unrelated') == [])
        assert (len(calls) == 1)
        assert ((state.genCalls, state.keyCalls) == counts)
        assert (bundle.vmCreate(gstCfg=NamedDict(usrCrd=NamedDict(
            sshKey=NamedDict(prv=None),
        ))) == [])
        assert (calls[-1].gstCfg.usrCrd.sshKey.prv != (
            base.gstCfg.usrCrd.sshKey.prv
        ))
        assert all(Path(dir.name).exists() for dir in owned)
        assert (json.dumps(base) == before)
    finally:
        generator.close()
    assert all(not Path(dir.name).exists() for dir in owned)
    assert (state.present == {})


def test_FxtVM_PerCallAlternatePEMPrivateDerivesOwnedPublic(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
) -> None:
    state = localContext
    prvKey = state.keyPath.parent / 'alternate.pem'
    result = state.nativeRun(
        ['ssh-keygen', '-q', '-t', 'rsa', '-b', '2048', '-m', 'PEM',
         '-N', '', '-f', str(prvKey)],
        capture_output=True, timeout=30,
    )
    assert (result.returncode == 0)
    pubKey = prvKey.with_suffix('.pem.pub')
    expected = state.nativeRun(
        ['ssh-keygen', '-y', '-P', '', '-f', str(prvKey)],
        input='', capture_output=True, timeout=30,
    )
    assert (expected.returncode == 0)
    files = [(path, path.read_bytes(), path.stat())
             for path in (prvKey, pubKey)]
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(count=0)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    base = bundle.vmCreate.args[1].fxtCfgObj
    before = ConfigSnapshot(base)
    owned = bundle.vmCreate.args[1].sshKeyDirs
    try:
        vm = bundle.vmCreate(count=1, gstCfg=NamedDict(usrCrd=NamedDict(
            sshKey=NamedDict(prv=str(prvKey), pub=None),
        )))[0]
        assert (vm.sshKey == str(prvKey))
        derived = Path(owned[-1].name) / (prvKey.name + '.pub')
        assert (derived.read_bytes() == expected.stdout)
        assert (derived != pubKey)
        assert (base64.b64encode(expected.stdout).decode() in (
            yaml.safe_dump(state.resources[-1])
        ))
        assert (ConfigSnapshot(base) == before)
        assert (bundle.vmCreate() == [])
    finally:
        generator.close()
    assert all(not Path(dir.name).exists() for dir in owned)
    for (path, content, info) in files:
        assert (path.read_bytes() == content)
        assert (path.stat().st_mode == info.st_mode)
        assert (path.stat().st_mtime_ns == info.st_mtime_ns)


@pytest.mark.parametrize('value', ['', ' '])
def test_VMcreate_InvalidNamespaceFailsBeforeResourceEffects(
    localContext: SimpleNamespace, value: Any,
) -> None:
    localContext.cfg['ns'] = value
    with pytest.raises(ValueError, match='Namespace'):
        Create(localContext)
    assert (localContext.resources == [])


def test_VMcreate_SuppliedPrivateReuseBypassesPublicAndImageReads(
    sshKeyDirs: list[TemporaryDirectory],
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = localContext
    state.present['context-vm'] = 'existing-uid'
    state.cfg['reuseExisting'] = True
    state.cfg['gstCfg']['usrCrd']['sshKey'] = NamedDict(
        prv='/never/read/key', pub='/never/read/public',
    )
    image = state.cfg['gstCfg']['imgSrc']['CDI']
    image['url'] = 'not-an-image-url'
    image['credInfo']['vaultInfo'] = {
        'file--AccKeyId': '/never/read/access',
        'file--AccKeySecret': '/never/read/secret',
    }
    reads = []

    def FailRead(path: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(path)
        raise AssertionError('Reused VM must not read local input files.')

    monkeypatch.setattr(Path, 'read_text', FailRead)
    monkeypatch.setattr(Path, 'open', FailRead)
    Utils.AutoPatch(monkeypatch, MUT.YAMLcfg, 'Render')
    state.keyPath.unlink()
    cfg = MUT._DEF__FXT_CFG | NamedDict(
        ns='context-ns', namePfx='context-vm', reuseExisting=True,
        gstCfg=NamedDict(
            usrCrd=NamedDict(sshKey=NamedDict(
                prv='/never/read/key', pub='/never/read/public',
            )),
            imgSrc=NamedDict(CDI=NamedDict(url='not-an-image-url')),
        ),
    )
    owned = []
    vms = MUT.VMcreate(
        state.request,
        MUT.FuncParsVMcreate(
            fxtCfgObj=cfg,
            vmList=[],
            ownVMs=owned,
            sshKeyDirs=sshKeyDirs,
        ),
        ocCLI=state.ocFactory,
        virtCtlCLI=lambda ns, **opts: VirtCtlCLI(
            'virtctl-unit', cmdExec=state.recorder, n=ns, **opts,
        ),
    )
    assert ((len(vms) == 1) and (owned == []))
    assert (vms[0].sshKey == '/never/read/key')
    assert (reads == [])
    assert (state.genCalls == state.keyCalls == 0)
    MUT.YAMLcfg.Render.assert_not_called()
    assert (state.resources == [])


def test_FxtVM_KeyExecutorExceptionIsSanitizedBeforeEffects(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    Utils.AutoPatch(monkeypatch, MUT, 'CmdExec')
    MUT.CmdExec.return_value.Run.side_effect = RuntimeError(
        'synthetic-sensitive-exception',
    )
    with pytest.raises(ValueError, match='sshKey') as caught:
        Create(localContext)
    assert (localContext.resources == [])
    assert ('synthetic-sensitive-exception' not in (
        str(caught.value) + caplog.text
    ))


@pytest.mark.parametrize(
    'keyMode', ['generate', 'public-only', 'derive', 'read'],
)
def test_FxtVM_KeyPairLifetimeUsesRealFilesAndSSHIdentity(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, keyMode: str,
) -> None:
    state = localContext
    caplog.set_level(logging.DEBUG)
    publicPath = state.keyPath.with_suffix('.pub')
    publicPath.write_text(state.publicKey)
    selectedPublic = state.keyPath.parent / 'selected-public'
    selectedPublic.write_text(state.publicKey)
    publicReads = []
    nativeOpen = Path.open

    def ReadPublic(path: Path, *args: Any, **kwargs: Any) -> Any:
        if (path == selectedPublic): publicReads.append(path)
        return nativeOpen(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', ReadPublic)
    privateBefore = hashlib.sha256(state.keyPath.read_bytes()).hexdigest()
    publicBefore = publicPath.read_bytes()
    fileStats = [(path, path.stat()) for path in (state.keyPath, publicPath)]
    supplied = keyMode in ('derive', 'read')
    key = NamedDict(
        prv=str(state.keyPath) if supplied else None,
        pub=(str(selectedPublic) if (keyMode == 'read') else
             str(publicPath) if (keyMode == 'public-only') else None),
    )
    cfg = NamedDict(_REGISTERED_CFG) | NamedDict(
        count=0, gstCfg=NamedDict(usrCrd=NamedDict(sshKey=key)),
    )
    inputsBefore = ConfigSnapshot(cfg)
    defaultsBefore = ConfigSnapshot(MUT._DEF__FXT_CFG)
    monkeypatch.setattr(RHOVsshCon, 'Probe', lambda self, **opts: True)
    closed = []

    def CloseSSH(connection: RHOVsshCon) -> None:
        assert Path(connection.idFile).is_file()
        closed.append(connection.idFile)

    monkeypatch.setattr(RHOVsshCon, 'Close', CloseSSH)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory,
        lambda ns, **opts: VirtCtlCLI(
            'virtctl-unit', cmdExec=state.recorder, n=ns, **opts,
        ),
    )
    state.request.param = cfg
    bundle = next(generator)
    assert (state.genCalls == (0 if supplied else 1))
    assert (state.keyCalls == (1 if (keyMode == 'derive') else 0))
    assert (bundle.vmList == [])
    try:
        resolved = bundle.vmCreate.args[1].fxtCfgObj
        assert (resolved is not cfg)
        assert (resolved.gstCfg.usrCrd.sshKey.prv is not None)
        assert (resolved.gstCfg.usrCrd.sshKey.pub is not None)
        first = bundle.vmCreate(count=2, namePfx='first')
        privatePath = Path(first[0].sshKey)
        if not supplied:
            assert (privatePath.name == 'id_ed25519')
            assert privatePath.parent.name.startswith('fxtVM--ssh--')
            assert (privatePath.parent.parent == Path(tempfile.gettempdir()))
            assert ((privatePath.parent.stat().st_mode & 0o777) == 0o0700)
            assert ((privatePath.stat().st_mode & 0o777) == 0o0600)
            publicKey = privatePath.with_suffix('.pub').read_text()
            derived = state.nativeRun(
                ['ssh-keygen', '-y', '-P', '', '-f', str(privatePath)],
                input='', capture_output=True, text=True, timeout=30,
            )
            assert (derived.returncode == 0)
            assert (derived.stdout.split()[:2] == publicKey.split()[:2])
        else: publicKey = state.publicKey
        later = bundle.vmCreate(count=1, namePfx='later')
        assert (publicReads == (
            [selectedPublic] * 3 if (keyMode == 'read') else []
        ))
        assert (len(bundle.vmList) == 3)
        assert all((vm.sshKey == str(privatePath)) for vm in first + later)
        connections = [vm.ssh for vm in bundle.vmList]
        assert all((con.idFile == str(privatePath)) for con in connections)
        assert (state.genCalls == (0 if supplied else 1))
        assert (state.keyCalls == (1 if (keyMode == 'derive') else 0))
        encoded = base64.b64encode(publicKey.encode()).decode()
        manifests = [item for item in state.resources
                     if (item['kind'] == 'VirtualMachine')]
        assert (len(manifests) == 3)
        assert all((encoded in yaml.safe_dump(item)) for item in manifests)
        retained = caplog.text + repr(state.recorder.inputs)
        assert ('PRIVATE KEY' not in retained)
        assert (str(privatePath) not
            in ''.join(map(yaml.safe_dump, manifests)))
        if (keyMode == 'public-only'):
            assert (publicKey.split()[:2] != state.publicKey.split()[:2])
    finally:
        generator.close()
    assert (len(closed) == 3)
    if not supplied: assert not privatePath.parent.exists()
    assert (hashlib.sha256(
        state.keyPath.read_bytes(),
    ).hexdigest() == privateBefore)
    assert (publicPath.read_bytes() == publicBefore)
    for (path, before) in fileStats:
        current = path.stat()
        assert ((current.st_mode, current.st_size, current.st_mtime_ns,
                current.st_ctime_ns) == (
                    before.st_mode, before.st_size, before.st_mtime_ns,
                    before.st_ctime_ns,
                ))
    assert (ConfigSnapshot(cfg) == inputsBefore)
    assert (ConfigSnapshot(MUT._DEF__FXT_CFG) == defaultsBefore)


@pytest.mark.parametrize(
    'failure', ['generate', 'create', 'postyield', 'cleanup'],
)
def test_FxtVM_OwnedKeyFilesRemovedAfterFailure(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    state = localContext
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(
        gstCfg=NamedDict(usrCrd=NamedDict(
            sshKey=NamedDict(prv=None, pub=None),
        )),
    )
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory,
        lambda ns, **opts: VirtCtlCLI(
            'virtctl-unit', cmdExec=state.recorder, n=ns, **opts,
        ),
    )
    if (failure == 'generate'): state.fail = 'generate'
    if (failure == 'create'):
        state.cfg = state.request.param
        state.request.param = state.request.param + {'cfgFile': 'missing'}
    if (failure in ('generate', 'create')):
        with pytest.raises((ValueError, OSError)):
            next(generator)
    else:
        bundle = next(generator)
        if (failure == 'postyield'):
            with pytest.raises(OSError):
                bundle.vmCreate(
                    namePfx='failed-later', cfgFile='missing',
                )
            assert state.generated[0].exists()
            generator.close()
        else:
            def FailClose(vm: MUT.VM) -> None:
                assert Path(vm.sshKey).exists()
                raise RuntimeError('synthetic-close-failure')

            monkeypatch.setattr(MUT.VM, 'RmvSSH', FailClose)
            with pytest.raises(RuntimeError, match='synthetic-close-failure'):
                next(generator)
        assert (len(bundle.vmList) == 1)
    assert (len(state.generated) == 1)
    assert not state.generated[0].parent.exists()
    assert state.keyPath.exists()


@pytest.mark.parametrize(
    'publicFile', ['missing', 'empty', 'invalid-utf8'],
)
def test_FxtVM_PublicFileFailureIsSanitizedAndPreservesPrivateFile(
    localContext: SimpleNamespace, publicFile: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = localContext
    publicPath = state.keyPath.parent / 'public'
    if (publicFile == 'empty'): publicPath.write_text(' ')
    if (publicFile == 'invalid-utf8'): publicPath.write_bytes(b'\xff')
    before = hashlib.sha256(state.keyPath.read_bytes()).hexdigest()
    state.cfg['gstCfg']['usrCrd']['sshKey']['pub'] = str(publicPath)
    with pytest.raises(ValueError, match='Public Key resolution') as caught:
        Create(state)
    assert (str(publicPath) not in (str(caught.value) + caplog.text))
    assert (hashlib.sha256(state.keyPath.read_bytes()).hexdigest() == before)
    assert (state.keyCalls == state.genCalls == 0)
    assert (state.resources == [])


@pytest.mark.parametrize('failure', ['public', 'derive'])
def test_FxtVM_FailedResolutionLeavesInputsRetryableWithoutPartialPaths(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    state = localContext
    publicPath = state.keyPath.parent / 'corrected-public'
    cfg = NamedDict(_REGISTERED_CFG) | NamedDict(
        count=0, gstCfg=NamedDict(usrCrd=NamedDict(sshKey=NamedDict(
            prv=str(state.keyPath),
            pub=str(publicPath) if (failure == 'public') else None,
        ))),
    )
    state.request.param = cfg
    before = json.dumps(cfg)
    defaultsBefore = json.dumps(MUT._DEF__FXT_CFG)
    directories = []
    nativeDirectory = MUT.TemporaryDirectory

    def Directory(**kwargs: Any) -> Any:
        dir = nativeDirectory(**kwargs)
        directories.append(Path(dir.name))
        return dir

    monkeypatch.setattr(MUT, 'TemporaryDirectory', Directory)
    if (failure == 'derive'): state.fail = 'key-timeout'
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    with pytest.raises(ValueError, match='Public Key resolution'):
        next(generator)
    assert (json.dumps(cfg) == before)
    assert (json.dumps(MUT._DEF__FXT_CFG) == defaultsBefore)
    assert all(not dir.exists() for dir in directories)
    assert ((state.resources == []) and (state.commands == []))
    state.fail = ''
    publicPath.write_text(state.publicKey)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    try:
        resolved = bundle.vmCreate.args[1].fxtCfgObj.gstCfg.usrCrd.sshKey
        assert (resolved.prv == str(state.keyPath))
        assert (Path(resolved.pub).read_text().strip() == (
            state.publicKey.strip()
        ))
        assert ((state.resources == []) and (state.commands == []))
    finally:
        generator.close()
    assert all(not dir.exists() for dir in directories)
    assert (state.keyPath.is_file() and publicPath.is_file())
    assert (json.dumps(cfg) == before)


def test_FxtVM_FailedBatchRetainsPreparedIdentityForCorrectedRetry(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
) -> None:
    state = localContext
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(
        count=0, gstCfg=NamedDict(usrCrd=NamedDict(
            sshKey=NamedDict(prv=None, pub=None),
        )),
    )
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    try:
        assert (state.genCalls == 1)
        with pytest.raises(ValueError, match='url'):
            bundle.vmCreate(count=1, gstCfg=NamedDict(imgSrc=NamedDict(
                CDI=NamedDict(url='invalid-image-url'),
            )))
        assert (state.resources == [])
        assert state.generated[0].is_file()
        vms = bundle.vmCreate(count=1)
        assert (state.genCalls == 1)
        assert (vms[0].sshKey == str(state.generated[0]))
    finally:
        generator.close()
    assert not state.generated[0].parent.exists()


@pytest.mark.parametrize('partialFailure', [False, True])
def test_FxtVM_PytestLIFOClosesSSHBeforeOwnedFiles(
    localContext: SimpleNamespace, request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch, partialFailure: bool,
) -> None:
    state = localContext
    closed = []

    def VerifyFinalized() -> None:
        assert (len(state.generated) == 1)
        assert (closed == [(state.generated[0], True)])
        assert not state.generated[0].parent.exists()
        assert (state.present == {})

    def CloseSSH(connection: RHOVsshCon) -> None:
        path = Path(connection.idFile)
        closed.append((path, path.is_file()))

    request.addfinalizer(VerifyFinalized)
    monkeypatch.setattr(RHOVsshCon, 'Probe', lambda self, **opts: True)
    monkeypatch.setattr(RHOVsshCon, 'Close', CloseSSH)
    monkeypatch.setattr(MUT, '_DEF__FXT_CFG', MUT._DEF__FXT_CFG | NamedDict(
        count=0, ns='context-ns', gstCfg=NamedDict(
            imgSrc=NamedDict(CDI=NamedDict(credInfo=NamedDict(vaultInfo={
                'file--AccKeyId': 'access',
                'file--AccKeySecret': 'relative-secret',
            }))),
        ),
    ))
    bundle = request.getfixturevalue('fxtVM')
    primary = RuntimeError('synthetic-partial-setup')
    nativeRender = MUT.YAMLcfg.Render
    calls = []

    def Render(*args: Any, **kwargs: Any) -> str:
        calls.append(None)
        if (partialFailure and (len(calls) == 2)):
            assert (bundle.vmList[0].ssh.idFile == str(state.generated[0]))
            raise primary
        return nativeRender(*args, **kwargs)

    monkeypatch.setattr(MUT.YAMLcfg, 'Render', Render)
    if partialFailure:
        with pytest.raises(RuntimeError) as caught:
            bundle.vmCreate(count=2)
        assert (caught.value is primary)
    else:
        bundle.vmCreate(count=1)
        assert (bundle.vmList[0].ssh.idFile == str(state.generated[0]))
    assert (len(bundle.vmList) == 1)
    assert (state.generated[0].is_file() and (closed == []))


@pytest.mark.parametrize('primaryPhase', ['preparation', 'cleanup', 'none'])
def test_FxtVM_DirectoryCleanupFailurePreservesPrimaryAndSanitizesLogs(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    primaryPhase: str,
) -> None:
    state = localContext
    caplog.set_level(logging.DEBUG)
    nativeDirectory = MUT.TemporaryDirectory
    cleanupCalls = []
    secondary = OSError('synthetic-sensitive-directory-cleanup')

    def Directory(*args: Any, **kwargs: Any) -> Any:
        directory = nativeDirectory(*args, **kwargs)
        nativeCleanup = directory.cleanup

        def CleanUpDirectory() -> None:
            nativeCleanup()
            cleanupCalls.append(Path(directory.name))
            raise secondary

        directory.cleanup = CleanUpDirectory
        return directory

    monkeypatch.setattr(MUT, 'TemporaryDirectory', Directory)
    primary = FileNotFoundError('synthetic-preparation-failure')
    cleanupErrors = []
    nativeCleanupVMs = MUT.VM.CleanUpVMs

    def CleanUpVMs(vmList: list[Any], ownVMs: list[Any]) -> None:
        try: nativeCleanupVMs(vmList, ownVMs)
        except Exception as error:
            cleanupErrors.append(error)
            raise

    monkeypatch.setattr(MUT.VM, 'CleanUpVMs', staticmethod(CleanUpVMs))
    if (primaryPhase == 'preparation'):
        def FailRender(*args: Any, **kwargs: Any) -> str:
            raise primary

        monkeypatch.setattr(MUT.YAMLcfg, 'Render', FailRender)
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(
        gstCfg=NamedDict(usrCrd=NamedDict(
            sshKey=NamedDict(prv=None, pub=None),
        )),
    )
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    if (primaryPhase == 'preparation'):
        with pytest.raises(FileNotFoundError) as caught:
            next(generator)
        assert (caught.value is primary)
        assert ([item['kind'] for item in state.resources] == ['Secret'])
        assert (state.secrets == {})
    else:
        bundle = next(generator)
        if (primaryPhase == 'cleanup'):
            monkeypatch.setattr(RHOVsshCon, 'Probe', lambda self, **opts: True)

            def FailClose(connection: RHOVsshCon) -> None:
                assert Path(connection.idFile).is_file()
                raise RuntimeError('synthetic-SSH-close-failure')

            monkeypatch.setattr(RHOVsshCon, 'Close', FailClose)
            assert (bundle.vmList[0].ssh.idFile == str(state.generated[0]))
        with pytest.raises(RuntimeError) as caught:
            next(generator)
        if (primaryPhase == 'cleanup'):
            assert (len(cleanupErrors) == 1)
            assert (caught.value is cleanupErrors[0])
            assert ('VM cleanup failed' in str(caught.value))
        else:
            assert (type(caught.value) is RuntimeError)
            assert (str(caught.value) == (
                'Could not clean up fixture resources or SSH key files.'
            ))
            assert (cleanupErrors == [])
        assert (state.present == {})
    assert (len(state.generated) == 1)
    assert (cleanupCalls == [state.generated[0].parent])
    assert not state.generated[0].parent.exists()
    retained = str(caught.value) + caplog.text
    assert (caught.value is not secondary)
    assert (str(secondary) not in retained)
    assert (str(state.generated[0].parent) in caplog.text)
    assert ('Could not clean up fixture SSH key directory' in caplog.text)


@pytest.mark.parametrize('failure', ['generate-result', 'generate-timeout'])
def test_FxtVM_KeyGenerationFailureSanitizesPartialFilesAndOutput(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
    caplog: pytest.LogCaptureFixture, failure: str,
) -> None:
    state = localContext
    caplog.set_level(logging.DEBUG)
    state.fail = failure
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(
        gstCfg=NamedDict(usrCrd=NamedDict(
            sshKey=NamedDict(prv=None, pub=None),
        )),
    )
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    with pytest.raises(ValueError, match='Key generation failed') as caught:
        next(generator)
    assert ((state.genCalls == 1) and (state.keyCalls == 0))
    assert (len(state.generated) == 1)
    assert not state.generated[0].parent.exists()
    assert (state.resources == [])
    retained = str(caught.value) + caplog.text + repr(state.recorder.inputs)
    for marker in ('synthetic-sensitive-stdout', 'synthetic-sensitive-stderr',
                   'synthetic-partial-private'):
        assert (marker not in retained)


_REGISTERED_CFG = {
    'ns': 'context-ns', 'namePfx': 'registered-vm',
    'gstCfg': MUT.NamedDict(
        imgSrc=MUT.NamedDict(CDI=MUT.NamedDict(
            credInfo=MUT.NamedDict(vaultInfo={
                'file--AccKeyId': 'access',
                'file--AccKeySecret': 'relative-secret',
            }),
        )),
        usrCrd=MUT.NamedDict(sshKey=NamedDict(
            prv='$VMCTX__KEY_DIR/private-key', pub=None,
        )),
    ),
}


@pytest.mark.parametrize('fxtVM', [_REGISTERED_CFG], indirect=True)
def test_FxtVM_RegisteredFixtureSupportsPartialNestedNamedDictBatches(
    fxtVM: MUT.FxtObjVM, localContext: SimpleNamespace,
) -> None:
    state = localContext
    inputsBefore = json.loads(json.dumps(_REGISTERED_CFG))
    defaultsBefore = json.loads(json.dumps(MUT._DEF__FXT_CFG))
    assert ([vm.name for vm in fxtVM.vmList] == ['registered-vm'])
    created = fxtVM.vmCreate(namePfx='registered-batch', count=2)
    assert ([vm.name for vm in created] == [
        'registered-batch-0', 'registered-batch-1',
    ])
    assert (len(fxtVM.vmList) == 3)
    assert (state.keyCalls == 1)
    assert ([item['kind'] for item in state.resources] == [
        'Secret', 'VirtualMachine', 'VirtualMachine', 'VirtualMachine',
    ])
    assert (json.loads(json.dumps(MUT._DEF__FXT_CFG)) == defaultsBefore)
    assert (json.loads(json.dumps(_REGISTERED_CFG)) == inputsBefore)


def test_VMcreate_EmptyRenderLeavesSecretOwnedForFixtureCleanup(
    localContext: SimpleNamespace,
) -> None:
    state = localContext
    template = state.keyPath.parent / 'empty.yaml'
    template.write_text(' \n')
    state.cfg['cfgFile'] = str(template)
    with pytest.raises(ValueError, match='cfgFile'):
        Create(state)
    assert ([resource['kind'] for resource in state.resources] == ['Secret'])


def test__DEF__FXT_CFG_CDIWrapperDefaultsAndDocstringAgree() -> None:
    cfg = MUT._DEF__FXT_CFG
    for section in (cfg.gstCfg, cfg.gstCfg.imgSrc, cfg.gstCfg.imgSrc.CDI,
                    cfg.gstCfg.imgSrc.CDI.credInfo, cfg.gstCfg.usrCrd):
        assert (type(section) is MUT.NamedDict)
    source = cfg.gstCfg.imgSrc.CDI
    assert (type(source.credInfo.vaultInfo) is dict)
    assert (source.credInfo.vaultInfo == {
        'file--AccKeyId': '/secretMount/secretFileForAccKeyId',
        'file--AccKeySecret': '/secretMount/secretFileFoAccKeySecret',
    })
    assert ((source.type == 'AWS-S3') and (source.credType == 'K8s'))
    assert (source.credInfo.vaultType == 'LocalFile')
    assert (source.credInfo.name == 'aws-s3--cred')
    schema = MUT.FxtVM.__doc__.split('Complete YAML schema:')[1]
    blocks = [
        textwrap.dedent(block.split('```')[0])
        for block in schema.split('```yaml')[1:]
    ]
    assert (len(blocks) == 2)
    main = list(yaml.safe_load_all(blocks[0]))
    credentials = list(yaml.safe_load_all(blocks[1]))
    assert (main == [
        {'CDI': {'type': 'AWS-S3', 'credType': 'None', 'url': source.url}},
        {'CDI': {
            'type': 'AWS-S3', 'credType': 'K8s', 'credInfo': {},
            'url': source.url,
        }},
    ])
    assert (type(main[0]['CDI']['credType']) is str)
    assert (credentials[0] == source.credInfo)
    assert (credentials[1] == {
        'name': 'aws-s3--cred', 'vaultType': 'BitWarden',
        'vaultInfo': {
            'name': '...bwNoteName...', 'subField': '...bwSubFieldName...',
        },
    })
    assert ('# To be implemented.' in blocks[1])


@pytest.mark.parametrize('section', [
    'gstCfg', 'imgSrc', 'CDI', 'credInfo', 'usrCrd',
])
def test_VMcreate_PlainNestedOverrideFailsBeforeEffects(
    sshKeyDirs: list[TemporaryDirectory],
    localContext: SimpleNamespace, section: str,
) -> None:
    path = ('gstCfg', 'usrCrd') if (section == 'usrCrd') else (
        'gstCfg', 'imgSrc', 'CDI', 'credInfo',
    )
    cfg = {}
    for name in reversed(path[:path.index(section) + 1]):
        cfg = MUT.NamedDict({name: cfg})
    with pytest.raises(
        TypeError, match='expected `NamedDict`, received `dict`',
    ):
        MUT.VMcreate(
            localContext.request,
            MUT.FuncParsVMcreate(
                fxtCfgObj=MUT._DEF__FXT_CFG,
                vmList=[],
                ownVMs=[],
                sshKeyDirs=sshKeyDirs,
            ),
            ocCLI=localContext.ocFactory,
            virtCtlCLI=VirtCtlCLI,
            **cfg,
        )
    assert (localContext.resources == [])
    assert (localContext.commands == [])


@pytest.mark.parametrize(
    'cfgMode', ['implicit', 'partial', 'complete', 'custom-user'],
)
def test_FxtVM_ImplicitDefaultPathsAreDetachedBeforeFactoryBinding(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
    cfgMode: str,
) -> None:
    state = localContext
    cfg = NamedDict(count=0)
    if (cfgMode != 'implicit'):
        cfg.___update___(gstCfg=NamedDict(usrCrd=NamedDict(
            sshKey=NamedDict(prv=None) if (cfgMode == 'partial') else
            NamedDict(prv=None, pub=None),
        )))
    if (cfgMode == 'custom-user'):
        cfg.gstCfg.usrCrd.___update___(sshUsr='caller-user')
    state.request.param = cfg
    inputsBefore = json.dumps(cfg)
    defaultsBefore = json.dumps(MUT._DEF__FXT_CFG)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    try:
        assert (bundle.vmCreate.args[0] is state.request)
        resolved = bundle.vmCreate.args[1].fxtCfgObj
        key = resolved.gstCfg.usrCrd.sshKey
        assert (resolved.gstCfg.usrCrd.sshUsr == (
            'caller-user' if (cfgMode == 'custom-user') else 'qa-usr'
        ))
        assert (resolved.gstCfg is not MUT._DEF__FXT_CFG.gstCfg)
        assert (resolved.gstCfg.usrCrd is not MUT._DEF__FXT_CFG.gstCfg.usrCrd)
        if (cfgMode != 'implicit'):
            assert (resolved.gstCfg is not cfg.gstCfg)
            assert (resolved.gstCfg.usrCrd is not cfg.gstCfg.usrCrd)
            assert (key is not cfg.gstCfg.usrCrd.sshKey)
        assert (Path(key.prv).is_file() and Path(key.pub).is_file())
        assert (key is not MUT._DEF__FXT_CFG.gstCfg.usrCrd.sshKey)
        assert ((state.genCalls == 1) and (state.keyCalls == 0))
        assert (bundle.vmCreate() == [])
        assert (state.resources == state.commands == [])
        assert (json.dumps(cfg) == inputsBefore)
        assert (json.dumps(MUT._DEF__FXT_CFG) == defaultsBefore)
    finally:
        generator.close()
    assert not Path(key.prv).parent.exists()
    assert (json.dumps(MUT._DEF__FXT_CFG) == defaultsBefore)


@pytest.mark.parametrize(('publicText', 'encodedPublic'), [
    ('untyped-content', 'dW50eXBlZC1jb250ZW50'),
    ('arbitrary\nnonempty\n', 'YXJiaXRyYXJ5Cm5vbmVtcHR5Cg=='),
    ('  nonempty text \n\n', 'ICBub25lbXB0eSB0ZXh0IAoK'),
    ('arbitrary\r\nnonempty\r\n', 'YXJiaXRyYXJ5DQpub25lbXB0eQ0K'),
    ('arbitrary\rnonempty\r', 'YXJiaXRyYXJ5DW5vbmVtcHR5DQ=='),
])
@pytest.mark.parametrize('pathForm', ['literal', 'env', 'home', 'env-home'])
def test_FxtVM_SuppliedPublicTextAndPathsPassThroughUnchanged(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
    monkeypatch: pytest.MonkeyPatch, publicText: str, encodedPublic: str,
    pathForm: str,
) -> None:
    state = localContext
    pubPath = state.keyPath.parent / 'provided-public'
    pubPath.write_text(publicText)
    monkeypatch.setenv('HOME', str(state.keyPath.parent))
    monkeypatch.setenv('VMCTX__PUBLIC_HOME', '~/provided-public')
    publicPath = {
        'literal': str(pubPath),
        'env': '${VMCTX__KEY_DIR}/provided-public',
        'home': '~/provided-public',
        'env-home': '$VMCTX__PUBLIC_HOME',
    }[pathForm]
    cfg = NamedDict(_REGISTERED_CFG) | NamedDict(
        count=0, gstCfg=NamedDict(usrCrd=NamedDict(sshKey=NamedDict(
            prv='$VMCTX__KEY_DIR/private-key', pub=publicPath,
        ))),
    )
    state.request.param = cfg
    before = ConfigSnapshot(cfg)
    defaultsBefore = ConfigSnapshot(MUT._DEF__FXT_CFG)
    files = (state.keyPath, pubPath)
    fileStates = [(path.read_bytes(), path.stat()) for path in files]
    allocate = mock.Mock(wraps=MUT.TemporaryDirectory)
    monkeypatch.setattr(MUT, 'TemporaryDirectory', allocate)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    try:
        key = bundle.vmCreate.args[1].fxtCfgObj.gstCfg.usrCrd.sshKey
        assert (key == {'prv': str(state.keyPath), 'pub': str(pubPath)})
        assert (state.keyCalls == state.genCalls == 0)
        assert (state.resources == state.commands == [])
        assert (len(bundle.vmCreate(count=1)) == 1)
        assert (encodedPublic in yaml.safe_dump(state.resources[-1]))
        overridePath = pubPath.parent / 'override-public'
        overrideText = 'different\n' + publicText
        overridePath.write_text(overrideText)
        override = NamedDict(usrCrd=NamedDict(sshKey=NamedDict(
            pub=str(overridePath),
        )))
        overrideBefore = json.dumps(override)
        assert (len(bundle.vmCreate(
            count=1, namePfx='override', gstCfg=override,
        )) == 1)
        encoded = base64.b64encode(overrideText.encode()).decode()
        assert (encoded in yaml.safe_dump(state.resources[-1]))
        assert (json.dumps(override) == overrideBefore)
        assert (pubPath.read_bytes() == publicText.encode('utf-8'))
        assert (overridePath.read_bytes() == overrideText.encode('utf-8'))
        allocate.assert_not_called()
    finally:
        generator.close()
    assert (ConfigSnapshot(cfg) == before)
    assert (ConfigSnapshot(MUT._DEF__FXT_CFG) == defaultsBefore)
    for (path, (content, info)) in zip(files, fileStates):
        assert (path.read_bytes() == content)
        assert (path.stat().st_mode == info.st_mode)
        assert (path.stat().st_mtime_ns == info.st_mtime_ns)


def test_FxtVM_DerivedRSAPublicKeyUsesSuppliedPEMBasename(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
) -> None:
    state = localContext
    prvPath = state.keyPath.parent / 'openshift-qe.pem'
    result = state.nativeRun(
        ['ssh-keygen', '-q', '-t', 'rsa', '-b', '2048', '-m', 'PEM',
         '-N', '', '-f', str(prvPath)],
        input='', capture_output=True, text=True, timeout=30,
    )
    assert (result.returncode == 0)
    suppliedPub = Path(str(prvPath) + '.pub')
    files = (prvPath, suppliedPub)
    fileStates = [(path.read_bytes(), path.stat()) for path in files]
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(
        count=0, gstCfg=NamedDict(usrCrd=NamedDict(sshKey=NamedDict(
            prv=str(prvPath), pub=None,
        ))),
    )
    inputsBefore = ConfigSnapshot(state.request.param)
    defaultsBefore = ConfigSnapshot(MUT._DEF__FXT_CFG)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    try:
        key = bundle.vmCreate.args[1].fxtCfgObj.gstCfg.usrCrd.sshKey
        pubPath = Path(key.pub)
        assert (key.prv == str(prvPath))
        assert (pubPath.name == 'openshift-qe.pem.pub')
        assert (pubPath.parent != prvPath.parent)
        assert (pubPath.parent.parent == Path(tempfile.gettempdir()))
        assert pubPath.parent.name.startswith('fxtVM--ssh--')
        assert ((pubPath.parent.stat().st_mode & 0o777) == 0o0700)
        result = state.nativeRun(
            ['ssh-keygen', '-y', '-P', '', '-f', str(prvPath)],
            input='', capture_output=True, text=True, timeout=30,
        )
        assert (result.returncode == 0)
        assert (pubPath.read_text() == (result.stdout.strip() + '\n'))
        assert ((state.keyCalls == 1) and (state.genCalls == 0))
        assert (state.resources == state.commands == [])
        assert (bundle.vmCreate(count=1)[0].sshKey == str(prvPath))
        encoded = base64.b64encode(pubPath.read_bytes()).decode()
        assert (encoded in yaml.safe_dump(state.resources[-1]))
    finally:
        generator.close()
    assert not pubPath.parent.exists()
    assert (ConfigSnapshot(state.request.param) == inputsBefore)
    assert (ConfigSnapshot(MUT._DEF__FXT_CFG) == defaultsBefore)
    for (path, (content, info)) in zip(files, fileStates):
        assert (path.read_bytes() == content)
        assert (path.stat().st_mode == info.st_mode)
        assert (path.stat().st_mtime_ns == info.st_mtime_ns)


def test_VMcreate_ResolvedConfigurationAndCompletePairOverridesAreLocal(
    sshKeyDirs: list[TemporaryDirectory],
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = localContext
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(count=0)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    alternate = state.keyPath.parent / 'alternate'
    result = state.nativeRun(
        ['ssh-keygen', '-q', '-t', 'ed25519', '-N', '',
         '-f', str(alternate)],
        input='', capture_output=True, text=True, timeout=30,
    )
    assert (result.returncode == 0)
    publicPath = alternate.with_suffix('.pub')
    filesBefore = [(path, path.read_bytes(), path.stat())
                   for path in (alternate, publicPath)]
    resolved = bundle.vmCreate.args[1].fxtCfgObj
    before = ConfigSnapshot(resolved)
    override = NamedDict(usrCrd=NamedDict(
        sshUsr='alternate-user',
        sshKey=NamedDict(prv=str(alternate), pub=str(publicPath)),
    ))
    overrideBefore = json.dumps(override)
    Utils.AutoPatch(monkeypatch, MUT, 'TemporaryDirectory')
    Utils.AutoPatch(monkeypatch, MUT, 'CmdExec')
    try:
        first = MUT.VMcreate(
            state.request,
            MUT.FuncParsVMcreate(
                fxtCfgObj=resolved,
                vmList=bundle.vmList,
                ownVMs=bundle.vmCreate.args[1].ownVMs,
                sshKeyDirs=sshKeyDirs,
            ),
            ocCLI=state.ocFactory,
            virtCtlCLI=fxtVirtCtlCLI,
            count=1,
            namePfx='inherited',
        )
        later = bundle.vmCreate(
            count=1, namePfx='override', ns='override-ns', gstCfg=override,
        )
        assert (first[0].sshKey == str(state.keyPath))
        assert (later[0].sshKey == str(alternate))
        assert (later[0].sshUsr == 'alternate-user')
        assert (state.resources[-1]['metadata']['namespace'] == 'override-ns')
        assert (bundle.vmList == (first + later))
        assert ((state.keyCalls == 1) and (state.genCalls == 0))
        MUT.TemporaryDirectory.assert_not_called()
        MUT.CmdExec.assert_not_called()
        encoded = base64.b64encode(publicPath.read_bytes()).decode()
        assert (encoded in yaml.safe_dump(state.resources[-1]))
        assert (ConfigSnapshot(resolved) == before)
        assert (json.dumps(override) == overrideBefore)
    finally:
        generator.close()
    for (path, content, statBefore) in filesBefore:
        assert (path.read_bytes() == content)
        assert (path.stat().st_mode == statBefore.st_mode)
        assert (path.stat().st_mtime_ns == statBefore.st_mtime_ns)


@pytest.mark.parametrize('case', [
    'missing', 'empty', 'invalid-utf8',
])
def test_VMcreate_InvalidPublicOverridePreservesPreparedFixtureAndRetries(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any, case: str,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    state = localContext
    state.request.param = {
        'count': 0, 'cliCfg': state.cfg['cliCfg'],
        'gstCfg': NamedDict(
            imgSrc=NamedDict(CDI=NamedDict(credInfo=NamedDict(
                vaultInfo=state.cfg['gstCfg']['imgSrc']['CDI']['credInfo'][
                    'vaultInfo'
                ],
            ))),
            usrCrd=NamedDict(sshKey=NamedDict(prv=str(state.keyPath))),
        ),
    }
    inputsBefore = ConfigSnapshot(state.request.param)
    defaultsBefore = ConfigSnapshot(MUT._DEF__FXT_CFG)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    resolved = bundle.vmCreate.args[1].fxtCfgObj
    owned = bundle.vmCreate.args[1].ownVMs
    resolvedBefore = ConfigSnapshot(resolved)
    key = resolved.gstCfg.usrCrd.sshKey
    publicPath = Path(key.pub)
    directory = publicPath.parent
    files = (state.keyPath, publicPath)
    filesBefore = [(path.read_bytes(), path.stat()) for path in files]
    badPublic = state.keyPath.parent / ('bad-public-' + case)
    content = {
        'missing': None, 'empty': b' \n', 'invalid-utf8': b'\xff',
    }[case]
    if (content is not None): badPublic.write_bytes(content)
    override = NamedDict(usrCrd=NamedDict(sshKey=NamedDict(
        pub=str(badPublic),
    )))
    overrideBefore = json.dumps(override)
    render = mock.Mock(wraps=MUT.YAMLcfg.Render)
    monkeypatch.setattr(MUT.YAMLcfg, 'Render', render)
    allocate = mock.Mock(wraps=MUT.TemporaryDirectory)
    monkeypatch.setattr(MUT, 'TemporaryDirectory', allocate)
    cleanup = mock.Mock(wraps=MUT.VM.CleanUpVMs)
    monkeypatch.setattr(MUT.VM, 'CleanUpVMs', cleanup)
    try:
        with (
            mock.patch.object(
                OCcli, 'NewRes', autospec=True, side_effect=OCcli.NewRes,
            ) as newResource,
            caplog.at_level(logging.DEBUG),
            pytest.raises(ValueError) as raised,
        ):
            bundle.vmCreate(count=1, gstCfg=override)
        assert (str(raised.value) == (
            '`gstCfg.usrCrd.sshKey`: Public Key resolution '
            'failed. Use readable public key files.'
        ))
        assert raised.value.__suppress_context__
        diagnostic = str(raised.value) + caplog.text
        for value in (str(state.keyPath), str(publicPath), str(badPublic),
                      state.publicKey.strip(), 'BEGIN OPENSSH PRIVATE KEY'):
            assert (value not in diagnostic)
        assert (newResource.call_count == 2)
        assert (newResource.call_args_list[-1].kwargs == {
            'isSecret': True,
            'checkExist': True,
            'waitCond': None,
        })
        render.assert_not_called()
        assert ([item['kind'] for item in state.resources] == ['Secret'])
        assert (len(bundle.vmCreate.args[1].ownSecrets) == 1)
        assert ((bundle.vmList == []) and (owned == []))
        assert (ConfigSnapshot(resolved) == resolvedBefore)
        assert (ConfigSnapshot(state.request.param) == inputsBefore)
        assert (json.dumps(override) == overrideBefore)
        assert (ConfigSnapshot(MUT._DEF__FXT_CFG) == defaultsBefore)
        cleanup.assert_not_called()
        allocate.assert_not_called()
        for (path, (contentBefore, statBefore)) in zip(files, filesBefore):
            assert (path.read_bytes() == contentBefore)
            assert (path.stat().st_mode == statBefore.st_mode)
            assert (path.stat().st_mtime_ns == statBefore.st_mtime_ns)
        assert ((directory.stat().st_mode & 0o777) == 0o0700)
        created = bundle.vmCreate(count=1)
        assert ((len(created) == 1) and (created[0].sshKey == key.prv))
        assert ((bundle.vmList == created) and (len(owned) == 1))
        assert ((state.keyCalls == 1) and (state.genCalls == 0))
        allocate.assert_not_called()
        assert (ConfigSnapshot(resolved) == resolvedBefore)
        assert (ConfigSnapshot(state.request.param) == inputsBefore)
        assert (json.dumps(override) == overrideBefore)
        assert (ConfigSnapshot(MUT._DEF__FXT_CFG) == defaultsBefore)
        assert (base64.b64encode(publicPath.read_bytes()).decode() in (
            yaml.safe_dump(state.resources[-1])
        ))
    finally:
        generator.close()
    cleanup.assert_called_once()
    cleanup.assert_called_once_with(bundle.vmList, owned)
    assert (not directory.exists() and state.keyPath.is_file())


@pytest.mark.parametrize('url', [
    'https://PUBLIC-IMAGE-HOST.invalid/windows.qcow2',
    'http://PUBLIC-IMAGE-HOST.invalid/windows.qcow2',
    'https://PLACEHOLDER-USER@PUBLIC-IMAGE-HOST.invalid/windows.qcow2',
    'https://PUBLIC-IMAGE-HOST.invalid/windows.qcow2?'
    'X-Amz-Signature=PLACEHOLDER%2BONLY&part=one+two&part=%2f#placeholder',
])
@pytest.mark.parametrize('authFields', ['inherited', 'missing', 'invalid'])
def test_VMcreate_NoneCredentialsRendersHTTPWithoutCredentialReadsOrSecrets(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    url: str, authFields: str,
) -> None:
    state = localContext
    state.cfg['count'] = 2
    source = state.cfg['gstCfg']['imgSrc']['CDI']
    source.update(credType='None', url=url)
    if (authFields == 'missing'): source.pop('credInfo')
    elif (authFields == 'invalid'):
        source['credInfo'].update(
            name='', vaultType='Unsupported', vaultInfo={}
        )
    before = json.dumps(state.cfg)
    reads = mock.Mock(side_effect=AssertionError(
        'DirectURL images must not read image credential files.',
    ))
    monkeypatch.setattr(Path, 'read_text', reads)

    (vms, owned) = Create(state)

    reads.assert_not_called()
    assert (len(vms) == len(owned) == 2)
    assert (state.secrets == {})
    assert ([resource['kind'] for resource in state.resources] == [
        'VirtualMachine', 'VirtualMachine',
    ])
    for resource in state.resources:
        volume = resource['spec']['dataVolumeTemplates'][0]
        assert (volume['spec']['source'] == {'http': {'url': url}})
        assert (volume['spec']['storage']['storageClassName'] == 'gp3-csi')
        assert (resource['spec']['runStrategy'] == 'Manual')
        assert ('secretRef' not in yaml.safe_dump(resource))
        assert (base64.b64encode(state.publicKey.encode()).decode() in (
            yaml.safe_dump(resource)
        ))
    assert (json.dumps(state.cfg) == before)
    assert any(
        (yaml.safe_load(record)
        == {
            **state.resources[0],
            'metadata': {
                key: value
                for (key, value) in state.resources[0]['metadata'].items()
                if (key != 'uid')
            },
        })
        for record in state.recorder.inputs
        if record
    )
    assert not any(('Secret' in ' '.join(cmd)) for cmd in state.commands)


@pytest.mark.parametrize('authFields', ['missing', None, 'invalid'])
def test_VMcreate_NoneCredentialsIgnoresResolvedCredentialInfo(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    authFields: Any,
) -> None:
    state = localContext
    publicKey = state.keyPath.with_suffix('.pub')
    publicKey.write_text(state.publicKey)
    source = NamedDict(
        type='AWS-S3', credType='None',
        url='https://PUBLIC-IMAGE-HOST.invalid/windows.qcow2',
    )
    if (authFields != 'missing'): source = source + {'credInfo': authFields}
    cfg = MUT._DEF__FXT_CFG + {
        'count': 1, 'ns': 'context-ns', 'cliCfg': state.cfg['cliCfg'],
        'gstCfg': NamedDict(
            imgSrc=NamedDict(CDI=source),
            usrCrd=NamedDict(
                sshUsr='qa-usr',
                sshKey=NamedDict(prv=str(state.keyPath), pub=str(publicKey)),
            ),
        ),
    }
    reads = mock.Mock(side_effect=AssertionError(
        'Image credentials accessed.'
    ))
    monkeypatch.setattr(Path, 'read_text', reads)

    vms = MUT.VMcreate(
        state.request,
        MUT.FuncParsVMcreate(fxtCfgObj=cfg, sshKeyDirs=state.sshKeyDirs),
        ocCLI=state.ocFactory,
        virtCtlCLI=lambda ns, **opts: VirtCtlCLI(
            'virtctl-unit', cmdExec=state.recorder, n=ns, **opts,
        ),
    )

    assert (len(vms) == 1)
    reads.assert_not_called()
    assert (state.secrets == {})
    assert (state.resources[0]['spec']['dataVolumeTemplates'][0]['spec'][
        'source'
    ] == {'http': {'url': source.url}})


@pytest.mark.parametrize('sourceType,credType,url,field', [
    ('HTTP', 'DirectURL', 'https://example.invalid/image.qcow2', 'type'),
    ('HTTP', 'None', 'https://example.invalid/image.qcow2', 'type'),
    (
        'AWS-S3', 'Unsupported', 'https://example.invalid/image.qcow2',
        'credType',
    ),
    (
        'AWS-S3', 'FutureMode', 'https://example.invalid/image.qcow2',
        'credType',
    ),
    ('AWS-S3', '', 'https://example.invalid/image.qcow2', 'credType'),
    ('AWS-S3', 'none', 'https://example.invalid/image.qcow2', 'credType'),
    ('AWS-S3', 'DirectURL', 'https://example.invalid/image.qcow2', 'credType'),
    ('AWS-S3', None, 'https://example.invalid/image.qcow2', 'credType'),
    ('AWS-S3', 'None', '', 'url'),
    ('AWS-S3', 'None', 'ftp://example.invalid/image.qcow2', 'url'),
    ('AWS-S3', 'None', 'https://[invalid-host', 'url'),
    ('AWS-S3', 'None', 'https://:443/image.qcow2', 'url'),
])
def test_VMcreate_NoneCredentialsRejectsUnsupportedConfigurationBeforeEffects(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    sourceType: str, credType: Any, url: str, field: str,
) -> None:
    state = localContext
    source = state.cfg['gstCfg']['imgSrc']['CDI']
    source.update(type=sourceType, credType=credType, url=url)
    reads = mock.Mock(side_effect=AssertionError(
        'Invalid image configuration must fail before credential reads.',
    ))
    monkeypatch.setattr(Path, 'read_text', reads)

    with pytest.raises(ValueError, match=field): Create(state)

    reads.assert_not_called()
    assert (state.resources == [])
    assert (state.secrets == {})
    assert not any(('apply' in cmd) for cmd in state.commands)


def test_VMcreate_ChangedCredentialModeAfterValidationRejectsBeforeEffects(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = localContext
    originalGet = NamedDict.__getattribute__
    credentialAccesses = []

    def GetAttribute(config: NamedDict, key: str) -> Any:
        if (key == 'credType'):
            credentialAccesses.append(key)
            if (len(credentialAccesses) == 3): return 'FutureMode'
        return originalGet(config, key)

    monkeypatch.setattr(NamedDict, '__getattribute__', GetAttribute)
    reads = mock.Mock(side_effect=AssertionError('No credential reads.'))
    monkeypatch.setattr(Path, 'read_text', reads)

    with pytest.raises(ValueError, match='Unsupported image credential mode'):
        Create(state)

    reads.assert_not_called()
    assert (state.resources == [])
    assert (state.secrets == {})


@pytest.mark.parametrize('credType', ['None', 'K8s'])
@pytest.mark.parametrize('reuseFirst', [False, True])
def test_VMcreate_CredentialModesKeepContextAndResourceEffectsIsolated(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    credType: str, reuseFirst: bool,
) -> None:
    state = localContext
    state.cfg['count'] = 3 if reuseFirst else 2
    if reuseFirst:
        state.cfg['reuseExisting'] = True
        state.present['context-vm-0'] = 'existing-uid'
    url = 'https://PUBLIC-IMAGE-HOST.invalid/disk?part=%2f&part=one+two'
    state.cfg['gstCfg']['imgSrc']['CDI'].update(credType=credType, url=url)
    render = mock.Mock(wraps=MUT.YAMLcfg.Render)
    serialize = mock.Mock(wraps=MUT.YAMLcfg.ToYAML)
    monkeypatch.setattr(MUT.YAMLcfg, 'Render', render)
    monkeypatch.setattr(MUT.YAMLcfg, 'ToYAML', serialize)
    originalNewRes = OCcli.NewRes
    originalRead = Path.read_text
    operations = []
    reads = []
    credentialAccesses = []
    originalGet = NamedDict.__getattribute__

    def GetAttribute(config: NamedDict, key: str) -> Any:
        if (key == 'credType'): credentialAccesses.append(key)
        return originalGet(config, key)

    def NewRes(client: OCcli, manifest: str, **kwargs: Any) -> Any:
        kind = (
            'Namespace' if (manifest['subCmd'] == 'namespace') else 'Secret'
        ) if isinstance(manifest, dict) else yaml.safe_load(manifest)['kind']
        operations.append((kind, kwargs))
        return originalNewRes(client, manifest, **kwargs)

    def Read(path: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(path.resolve())
        return originalRead(path, *args, **kwargs)

    monkeypatch.setattr(OCcli, 'NewRes', NewRes)
    monkeypatch.setattr(Path, 'read_text', Read)
    monkeypatch.setattr(NamedDict, '__getattribute__', GetAttribute)

    (vms, owned) = Create(state)

    assert (len(vms) == (3 if reuseFirst else 2))
    assert (len(owned) == render.call_count == 2)
    assert (len(credentialAccesses) == {'None': 2, 'K8s': 3}[credType])
    expectedSource = {
        'None': {'http': {'url': url}},
        'K8s': {'s3': {'url': url, 'secretRef': 'aws-s3--cred'}},
    }[credType]
    for call in render.call_args_list:
        context = call.args[1]
        assert (set(context) == {
            'vmDataVolumeSource', 'vmNS', 'sshPublicKeyB64', 'vmName',
        })
        assert (context['vmDataVolumeSource'] == expectedSource)
        assert (context['vmNS'] == state.cfg['ns'])
        assert (context['sshPublicKeyB64'] == base64.b64encode(
            state.publicKey.encode(),
        ).decode('ascii'))
    if (credType == 'None'):
        assert (reads == [])
        serialize.assert_not_called()
        assert (state.secrets == {})
        assert (operations == [
            (
                'Namespace',
                {'checkExist': True, 'waitCond': None, 'isSecret': False},
            ),
            ('VirtualMachine', {'waitTime': '15m'}),
            ('VirtualMachine', {'waitTime': '15m'}),
        ])
    if (credType == 'K8s'):
        assert (reads == [
            state.keyPath.parent / 'access',
            state.keyPath.parent / 'relative-secret',
        ])
        serialize.assert_not_called()
        assert (len(state.secrets) == 1)
        assert (operations == [
            (
                'Namespace',
                {'checkExist': True, 'waitCond': None, 'isSecret': False},
            ),
            (
                'Secret',
                {'isSecret': True, 'checkExist': True, 'waitCond': None},
            ),
            ('VirtualMachine', {'waitTime': '15m'}),
            ('VirtualMachine', {'waitTime': '15m'}),
        ])


@pytest.mark.parametrize('phase', ['create', 'apply', 'wait'])
def test_VMcreate_NoneCredentialsUsesNormalDiagnosticsAndRecordings(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, phase: str,
) -> None:
    state = localContext
    url = 'https://PUBLIC-IMAGE-HOST.invalid/disk?X-Amz-Signature=PLACEHOLDER'
    state.cfg['gstCfg']['imgSrc']['CDI'].update(credType='None', url=url)
    originalRun = subprocess.run

    def Fail(command: list[str], **kwargs: Any) -> Any:
        if (
            (command[0] == 'oc-unit')
            and (phase in command)
            and (
                (
                    kwargs.get('input')
                    and (yaml.safe_load(kwargs['input']).get('kind')
                    == 'VirtualMachine')
                )
                or command[-1].startswith('VirtualMachine/')
            )
        ):
            return subprocess.CompletedProcess(command, 1, url, url)
        return originalRun(command, **kwargs)

    monkeypatch.setattr(subprocess, 'run', Fail)
    with pytest.raises(RuntimeError, match='creation failed') as caught:
        Create(state)

    assert str(caught.value).endswith(url)
    assert (url in caplog.text)
    assert any((url in (record or '')) for record in state.recorder.inputs)
    assert (state.secrets == {})


def test_VMcreate_RendersFourNativeKeysBeforeEffectsAndCachesRawReads(
    localContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = localContext
    state.cfg.update(count=3, reuseExisting=True)
    state.present['context-vm-0'] = 'existing-uid'
    access = state.keyPath.parent / 'access'
    secret = state.keyPath.parent / 'relative-secret'
    access.write_text('  synthetic-id:λ=1\n', encoding='utf-8')
    secret.write_text('synthetic-secret:雪=2\n', encoding='utf-8')
    reads = []
    contexts = []
    inputsBefore = json.loads(json.dumps(state.cfg))
    defaultsBefore = json.loads(json.dumps(MUT._DEF__FXT_CFG))
    originalRead = Path.read_text
    originalRender = MUT.YAMLcfg.Render

    def Read(path: Path, *args: Any, **kwargs: Any) -> str:
        if (path.resolve() in (access, secret)):
            assert (kwargs['encoding'] == 'utf-8')
            reads.append(path.resolve())
        return originalRead(path, *args, **kwargs)

    def Render(path: Path, context: dict[str, Any]) -> str:
        assert (set(context) == {
            'vmName', 'vmNS', 'vmDataVolumeSource', 'sshPublicKeyB64',
        })
        if not contexts:
            assert ([item['kind'] for item in state.resources] == ['Secret'])
        assert (base64.b64decode(context['sshPublicKeyB64']) == (
            state.publicKey.encode()
        ))
        contexts.append(dict(context))
        rendered = originalRender(path, context)
        context['vmName'] = 'stale-render-name'
        return rendered

    monkeypatch.setattr(Path, 'read_text', Read)
    monkeypatch.setattr(MUT.YAMLcfg, 'Render', Render)
    (vms, owned) = Create(state)
    assert ((len(vms) == 3) and (len(owned) == 2))
    assert (reads == [access, secret])
    assert ((len(contexts) == 2) and (state.keyCalls == 1))
    assert ([context['vmName'] for context in contexts] == [
        'context-vm-1', 'context-vm-2',
    ])
    assert ([item['metadata']['name'] for item in state.resources[1:]] == [
        'context-vm-1', 'context-vm-2',
    ])
    assert ([item['kind'] for item in state.resources] == [
        'Secret', 'VirtualMachine', 'VirtualMachine',
    ])
    assert (json.loads(json.dumps(state.cfg)) == inputsBefore)
    assert (json.loads(json.dumps(MUT._DEF__FXT_CFG)) == defaultsBefore)
    Create(state)
    assert ((len(contexts) == 2) and (state.keyCalls == 2))
    assert ((reads == [access, secret]) and (len(state.resources) == 3))
    state.present.pop('context-vm-1')
    state.present.pop('context-vm-2')
    Create(state)
    assert ((len(contexts) == 4) and (state.keyCalls == 3))
    assert (reads == [access, secret, access, secret])
    assert ([context['vmName'] for context in contexts[2:]] == [
        'context-vm-1', 'context-vm-2',
    ])
    assert ([item['kind'] for item in state.resources[3:]] == [
        'VirtualMachine', 'VirtualMachine',
    ])
    manifest = state.resources[0]
    assert (base64.b64decode(manifest['data']['accessKeyId']).decode() == (
        'synthetic-id:λ=1'
    ))
    assert (base64.b64decode(manifest['data']['secretKey']).decode() == (
        'synthetic-secret:雪=2'
    ))


@pytest.mark.parametrize(
    'setupMode', ['generate', 'public-only', 'derive', 'read'],
)
@pytest.mark.parametrize(
    'keyMode', ['generate', 'public-only', 'derive', 'read'],
)
def test_FxtVM_PerCallKeyModesPreserveBaseAndEarlierIdentities(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
    monkeypatch: pytest.MonkeyPatch, setupMode: str, keyMode: str,
) -> None:
    state = localContext
    sshKeyDirs = []
    nativeDirectory = MUT.TemporaryDirectory

    def Directory(*args: Any, **kwargs: Any) -> Any:
        dir = nativeDirectory(*args, **kwargs)
        sshKeyDirs.append(Path(dir.name))
        return dir

    monkeypatch.setattr(MUT, 'TemporaryDirectory', Directory)
    pubKey = state.keyPath.with_suffix('.pub')
    pubKey.write_text(state.publicKey)
    setupKey = NamedDict(
        prv=str(state.keyPath) if (setupMode in ('derive', 'read')) else None,
        pub=str(pubKey) if (setupMode in ('read', 'public-only')) else None,
    )
    cfg = NamedDict(_REGISTERED_CFG) | NamedDict(
        count=0, gstCfg=NamedDict(usrCrd=NamedDict(sshKey=setupKey)),
    )
    state.request.param = cfg
    inputsBefore = ConfigSnapshot(cfg)
    defaultsBefore = ConfigSnapshot(MUT._DEF__FXT_CFG)
    files = [(path, path.read_bytes(), path.stat())
             for path in (state.keyPath, pubKey)]
    setupAlias = cfg.gstCfg.usrCrd.sshKey
    monkeypatch.setattr(RHOVsshCon, 'Probe', lambda self, **opts: True)
    closed = []

    def Close(connection: RHOVsshCon) -> None:
        assert Path(connection.idFile).is_file()
        closed.append(connection.idFile)

    monkeypatch.setattr(RHOVsshCon, 'Close', Close)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    base = bundle.vmCreate.args[1].fxtCfgObj
    baseBefore = ConfigSnapshot(base)
    first = bundle.vmCreate(count=1, namePfx='base-first')[0]
    counts = (state.genCalls, state.keyCalls)
    directoryCount = len(sshKeyDirs)
    if (keyMode == 'generate'):
        sshKey = NamedDict(prv=None, pub=None)
    elif (keyMode == 'public-only'):
        sshKey = NamedDict(prv=None, pub=str(pubKey))
    elif (keyMode == 'derive'):
        sshKey = NamedDict(pub=None)
    else:
        sshKey = NamedDict(prv=str(state.keyPath), pub=str(pubKey))
    override = NamedDict(usrCrd=NamedDict(sshKey=sshKey))
    overrideBefore = json.dumps(override)
    later = []
    try:
        for index in range(2):
            vm = bundle.vmCreate(
                count=1, namePfx=f'override-{index}', gstCfg=override,
            )[0]
            later.append(vm)
            prvKey = Path(vm.sshKey)
            assert prvKey.is_file()
            assert (vm.ssh.idFile == str(prvKey))
            if (keyMode in ('generate', 'public-only')):
                assert prvKey.parent.name.startswith('fxtVM--ssh--')
                assert (prvKey.parent.parent == Path(tempfile.gettempdir()))
                assert ((prvKey.parent.stat().st_mode & 0o777) == 0o0700)
                assert ((prvKey.stat().st_mode & 0o777) == 0o0600)
                pubKeyVal = prvKey.with_suffix('.pub').read_bytes()
                assert (prvKey != Path(first.sshKey))
            elif (keyMode == 'derive'):
                assert (vm.sshKey == first.sshKey)
                pubKeyVal = state.nativeRun(
                    ['ssh-keygen', '-y', '-P', '', '-f', vm.sshKey],
                    input='', capture_output=True, timeout=30,
                ).stdout
                derivedPublic = sshKeyDirs[-1] / (prvKey.name + '.pub')
                assert (derivedPublic.read_bytes() == pubKeyVal)
                assert ((sshKeyDirs[-1].stat().st_mode & 0o777) == 0o0700)
            else:
                assert (vm.sshKey == str(state.keyPath))
                pubKeyVal = pubKey.read_bytes()
            assert (base64.b64encode(pubKeyVal).decode() in (
                yaml.safe_dump(state.resources[-1])
            ))
            assert Path(first.sshKey).is_file()
        inherited = bundle.vmCreate(count=1, namePfx='base-again')[0]
        assert (inherited.sshKey == first.sshKey ==
                base.gstCfg.usrCrd.sshKey.prv)
        assert (first.ssh.idFile == first.sshKey)
        if (keyMode in ('generate', 'public-only')):
            assert (later[0].sshKey != later[1].sshKey)
        assert (len(sshKeyDirs) == (
            directoryCount + (0 if (keyMode == 'read') else 2)
        ))
        assert (len(set(sshKeyDirs)) == len(sshKeyDirs))
        assert all(path.exists() for path in sshKeyDirs)
        assert ((state.genCalls, state.keyCalls) == (
            counts[0] + (2 if (keyMode in ('generate', 'public-only')) else 0),
            counts[1] + (2 if (keyMode == 'derive') else 0),
        ))
        assert (bundle.vmCreate.args[1].fxtCfgObj is base)
        assert (ConfigSnapshot(base) == baseBefore)
        assert (ConfigSnapshot(cfg) == inputsBefore)
        assert (json.dumps(override) == overrideBefore)
        assert (cfg.gstCfg.usrCrd.sshKey is setupAlias)
        assert (override.usrCrd.sshKey is sshKey)
        assert (ConfigSnapshot(MUT._DEF__FXT_CFG) == defaultsBefore)
    finally:
        generator.close()
    assert (len(closed) == 3)
    assert all(not path.exists() for path in sshKeyDirs)
    assert all(not path.parent.exists() for path in state.generated)
    for (path, content, info) in files:
        assert (path.read_bytes() == content)
        assert (path.stat().st_mode == info.st_mode)
        assert (path.stat().st_mtime_ns == info.st_mtime_ns)


@pytest.mark.parametrize('pathForm', ['env', 'home', 'env-home'])
@pytest.mark.parametrize('publicText', ['ssh-ed25519 opaque\r\n', ' opaque\r'])
def test_FxtVM_PerCallExpandedPathsPreserveLiteralPublicText(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
    monkeypatch: pytest.MonkeyPatch, pathForm: str, publicText: str,
) -> None:
    state = localContext
    monkeypatch.setenv('HOME', str(state.keyPath.parent))
    monkeypatch.setenv('VMCTX__HOME', '~')
    pubKey = state.keyPath.parent / 'literal-public'
    pubKey.write_bytes(publicText.encode())
    prefix = {'env': '${VMCTX__KEY_DIR}', 'home': '~',
              'env-home': '$VMCTX__HOME'}[pathForm]
    override = NamedDict(usrCrd=NamedDict(sshKey=NamedDict(
        prv=f'{prefix}/private-key', pub=f'{prefix}/literal-public',
    )))
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(count=0)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    before = json.dumps(override)
    files = [(path, path.read_bytes(), path.stat())
             for path in (state.keyPath, pubKey)]
    calls = (state.genCalls, state.keyCalls)
    try:
        vm = bundle.vmCreate(count=1, gstCfg=override)[0]
        assert (vm.sshKey == str(state.keyPath))
        assert (base64.b64encode(publicText.encode()).decode() in (
            yaml.safe_dump(state.resources[-1])
        ))
        assert ((state.genCalls, state.keyCalls) == calls)
        assert (json.dumps(override) == before)
    finally:
        generator.close()
    for (path, content, info) in files:
        assert (path.read_bytes() == content)
        assert (path.stat().st_mode == info.st_mode)
        assert (path.stat().st_mtime_ns == info.st_mtime_ns)


def test_FxtVM_PerCallListOverridesAreAppliedExactlyOnce(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = localContext
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(
        count=0, tags=['setup'],
    )
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    base = bundle.vmCreate.args[1].fxtCfgObj
    baseTags = base.tags
    tags = ['override']
    observed = []
    nativeResolve = MUT._ResolveSSHkeys

    def Resolve(
        fxtCfg: NamedDict, sshKeyDirs: list[TemporaryDirectory],
    ) -> NamedDict:
        resolved = nativeResolve(fxtCfg, sshKeyDirs)
        observed.append(resolved.tags)
        return resolved

    monkeypatch.setattr(MUT, '_ResolveSSHkeys', Resolve)
    try:
        bundle.vmCreate(count=1, namePfx='list-first', tags=tags)
        bundle.vmCreate(count=1, namePfx='list-second', tags=tags)
        bundle.vmCreate(count=1, namePfx='list-inherited')
        assert (observed == [
            ['setup', 'override'], ['setup', 'override'], ['setup'],
        ])
        assert (observed[0] is not observed[1])
        assert ((base.tags is baseTags) and (baseTags == ['setup']))
        assert (tags == ['override'])
        assert (bundle.vmCreate.args[1].fxtCfgObj is base)
    finally:
        generator.close()


@pytest.mark.parametrize('prvKey', [' ', '\t\r\n'])
def test_FxtVM_PerCallBlankPrivateFailsBeforeEffectsAndRetries(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any, prvKey: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = localContext
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(count=0)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    base = bundle.vmCreate.args[1].fxtCfgObj
    before = json.dumps(base)
    allocate = mock.Mock(wraps=MUT.TemporaryDirectory)
    monkeypatch.setattr(MUT, 'TemporaryDirectory', allocate)
    try:
        with pytest.raises(
            ValueError, match='sshKey.prv.*can not be empty string',
        ):
            bundle.vmCreate(count=1, gstCfg=NamedDict(usrCrd=NamedDict(
                sshKey=NamedDict(prv=prvKey),
            )))
        assert ((state.resources == []) and (bundle.vmList == []))
        allocate.assert_not_called()
        assert (json.dumps(base) == before)
        assert (bundle.vmCreate(count=1)[0].sshKey == str(state.keyPath))
    finally:
        generator.close()


@pytest.mark.parametrize('prv', ['', '.', './', '././', '$VMCTX__EMPTY'])
def test__ResolveSSHkeys_DotPrivateUsesSuppliedPublicLiterally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    sshKeyDirs: list[TemporaryDirectory], prv: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VMCTX__EMPTY', '')
    pub = tmp_path / 'supplied.pub'
    pub.write_bytes(b' opaque public\r\n')
    cfg = MUT._DEF__FXT_CFG | NamedDict(gstCfg=NamedDict(usrCrd=NamedDict(
        sshKey=NamedDict(prv=prv, pub=str(pub)),
    )))
    before = json.dumps(cfg)

    result = MUT._ResolveSSHkeys(cfg, sshKeyDirs)

    assert (result.gstCfg.usrCrd.sshKey.prv == '.')
    assert (result.gstCfg.usrCrd.sshKey.pub == str(pub))
    assert (pub.read_bytes() == b' opaque public\r\n')
    assert (sshKeyDirs == [])
    assert (json.dumps(cfg) == before)


@pytest.mark.parametrize('prv', ['', '.', './', '././', '$VMCTX__EMPTY'])
def test__ResolveSSHkeys_DotPrivateDerivationFailsNatively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    sshKeyDirs: list[TemporaryDirectory], prv: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VMCTX__EMPTY', '')
    cfg = MUT._DEF__FXT_CFG | NamedDict(gstCfg=NamedDict(usrCrd=NamedDict(
        sshKey=NamedDict(prv=prv, pub=None),
    )))
    before = json.dumps(cfg)

    with pytest.raises(ValueError, match='Public Key resolution failed'):
        MUT._ResolveSSHkeys(cfg, sshKeyDirs)

    assert (len(sshKeyDirs) == 1)
    assert Path(sshKeyDirs[0].name).is_dir()
    assert (list(Path(sshKeyDirs[0].name).iterdir()) == [])
    assert (json.dumps(cfg) == before)
    assert ('Load key' not in caplog.text)


@pytest.mark.parametrize('prv', ['', '.', './', '././', '$VMCTX__EMPTY'])
def test_FxtVM_DotPrivateUsesLiteralPublicAndConsumerIdentity(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
    monkeypatch: pytest.MonkeyPatch, prv: str,
) -> None:
    state = localContext
    monkeypatch.setenv('VMCTX__EMPTY', '')
    monkeypatch.setattr(RHOVsshCon, 'Probe', lambda self, **opts: True)
    pub = state.keyPath.parent / 'literal-public'
    text = ' opaque public\r\n'
    pub.write_bytes(text.encode())
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(count=0)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    base = bundle.vmCreate.args[1].fxtCfgObj
    before = ConfigSnapshot(base)
    calls = (state.genCalls, state.keyCalls)
    override = NamedDict(usrCrd=NamedDict(sshKey=NamedDict(
        prv=prv, pub=str(pub),
    )))
    cfgBefore = json.dumps(override)
    try:
        vm = bundle.vmCreate(count=1, gstCfg=override)[0]
        assert (vm.sshKey == '.')
        assert (vm.NewSSH().idFile == '.')
        assert (base64.b64encode(text.encode()).decode() in (
            yaml.safe_dump(state.resources[-1])
        ))
        assert ((state.genCalls, state.keyCalls) == calls)
        assert (ConfigSnapshot(base) == before)
        assert (json.dumps(override) == cfgBefore)
    finally:
        generator.close()
    assert (pub.read_bytes() == text.encode())


@pytest.mark.parametrize('failure', [
    'generate-result', 'generate-timeout', 'key-timeout', 'key-empty',
])
def test_FxtVM_PerCallResolutionFailureIsPrivateOwnedAndRetryable(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    state = localContext
    caplog.set_level(logging.DEBUG)
    sshKeyDirs = []
    nativeDirectory = MUT.TemporaryDirectory

    def Directory(*args: Any, **kwargs: Any) -> Any:
        dir = nativeDirectory(*args, **kwargs)
        sshKeyDirs.append(Path(dir.name))
        return dir

    monkeypatch.setattr(MUT, 'TemporaryDirectory', Directory)
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(count=0)
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    base = bundle.vmCreate.args[1].fxtCfgObj
    before = ConfigSnapshot(base)
    sshKey = (NamedDict(prv=None) if failure.startswith('generate')
              else NamedDict(pub=None))
    override = NamedDict(usrCrd=NamedDict(sshKey=sshKey))
    overrideBefore = json.dumps(override)
    try:
        state.fail = failure
        with pytest.raises(ValueError, match='sshKey') as caught:
            bundle.vmCreate(count=1, gstCfg=override)
        assert caught.value.__suppress_context__
        assert ((len(sshKeyDirs) == 2) and all(
            path.exists() for path in sshKeyDirs
        ))
        assert ((state.resources == []) and (bundle.vmList == []))
        assert not any(
            (('create' in cmd) or ('apply' in cmd)) for cmd in state.commands
        )
        diagnostic = str(caught.value) + caplog.text
        for value in (
            'synthetic-sensitive', 'synthetic-secret-value', 'PRIVATE KEY',
        ):
            assert (value not in diagnostic)
        assert (ConfigSnapshot(base) == before)
        assert (json.dumps(override) == overrideBefore)
        state.fail = ''
        vm = bundle.vmCreate(count=1, namePfx='retry', gstCfg=override)[0]
        assert Path(vm.sshKey).is_file()
        assert (len(sshKeyDirs) == 3)
        inherited = bundle.vmCreate(count=1, namePfx='inherited')[0]
        assert (inherited.sshKey == base.gstCfg.usrCrd.sshKey.prv)
        assert (bundle.vmCreate.args[1].fxtCfgObj is base)
        assert (ConfigSnapshot(base) == before)
    finally:
        generator.close()
    assert all(not path.exists() for path in sshKeyDirs)


@pytest.mark.parametrize('primaryPhase', ['body', 'cleanup', 'none'])
def test_FxtVM_MultipleOwnedDirectoriesFollowAllSSHCleanupAndPreservePrimary(
    localContext: SimpleNamespace, fxtVirtCtlCLI: Any,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    primaryPhase: str,
) -> None:
    state = localContext
    caplog.set_level(logging.DEBUG)
    sshKeyDirs = []
    events = []
    nativeDirectory = MUT.TemporaryDirectory
    primary = RuntimeError('synthetic-primary')

    def Directory(*args: Any, **kwargs: Any) -> Any:
        dir = nativeDirectory(*args, **kwargs)
        path = Path(dir.name)
        sshKeyDirs.append(path)
        nativeCleanup = dir.cleanup

        def Cleanup() -> None:
            assert (len([event for event in events if (event[0] == 'ssh')])
                == 3)
            nativeCleanup()
            events.append(('dir', path))
            if (path == sshKeyDirs[0]):
                raise OSError('synthetic-sensitive-directory-error')

        dir.cleanup = Cleanup
        return dir

    monkeypatch.setattr(MUT, 'TemporaryDirectory', Directory)
    monkeypatch.setattr(RHOVsshCon, 'Probe', lambda self, **opts: True)

    def Close(connection: RHOVsshCon) -> None:
        assert all(path.exists() for path in sshKeyDirs)
        events.append(('ssh', connection.idFile))
        if ((primaryPhase == 'cleanup') and (len(events) == 1)):
            raise primary

    monkeypatch.setattr(RHOVsshCon, 'Close', Close)
    nativeCleanupVMs = MUT.VM.CleanUpVMs
    cleanupErrors = []

    def CleanupVMs(vmList: list[Any], ownVMs: list[Any]) -> None:
        try:
            nativeCleanupVMs(vmList, ownVMs)
        except Exception as error:
            cleanupErrors.append(error)
            raise

    monkeypatch.setattr(MUT.VM, 'CleanUpVMs', staticmethod(CleanupVMs))
    state.request.param = NamedDict(_REGISTERED_CFG) | NamedDict(
        count=0, gstCfg=NamedDict(usrCrd=NamedDict(
            sshKey=NamedDict(prv=None),
        )),
    )
    generator = MUT.FxtVM.__wrapped__(
        state.request, state.ocFactory, fxtVirtCtlCLI,
    )
    bundle = next(generator)
    try:
        for (index, sshKey) in enumerate((
            NamedDict(), NamedDict(prv=None), NamedDict(pub=None),
        )):
            vm = bundle.vmCreate(
                count=1, namePfx=f'owned-{index}', gstCfg=NamedDict(
                    usrCrd=NamedDict(sshKey=sshKey),
                ),
            )[0]
            assert (vm.ssh.idFile == vm.sshKey)
        assert (len(sshKeyDirs) == 3)
        with pytest.raises(RuntimeError) as caught:
            if (primaryPhase == 'body'):
                generator.throw(primary)
            else:
                next(generator)
        if (primaryPhase == 'body'):
            assert (caught.value is primary)
        elif (primaryPhase == 'cleanup'):
            assert (caught.value is cleanupErrors[0])
        else:
            assert (str(caught.value) == (
                'Could not clean up fixture resources or SSH key files.'
            ))
        assert ([event[0] for event in events] == (['ssh'] * 3 + ['dir'] * 3))
        assert ([
            event[1]
            for event in events
            if (event[0] == 'dir')
        ] == sshKeyDirs)
        assert all(not path.exists() for path in sshKeyDirs)
        assert (state.present == {})
        assert ('synthetic-sensitive-directory-error' not in caplog.text)
    finally:
        generator.close()
