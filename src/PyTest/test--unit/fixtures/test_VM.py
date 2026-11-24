from dataclasses import FrozenInstanceError
import json
from types import SimpleNamespace
from typing import Any, get_args, get_origin
from unittest import mock

import pytest

import fixtures.VM as MUT


def Request(rootpath: Any, param: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(rootpath=rootpath),
        param={} if (param is None) else param,
        addfinalizer=mock.Mock(),
    )


def ConfigSnapshot(cfg: Any) -> dict[str, Any]:
    '''Snapshot every config value except the root double's merge control.'''
    values = {
        key: value for (key, value) in vars(cfg).items()
        if (key != '_merge')
    }
    return json.loads(json.dumps(values, default=vars))


def CommandResult(success: bool, stdout: str = '', stderr: str = '') -> Any:
    return SimpleNamespace(success=success, stdout=stdout, stderr=stderr)


def AssertRootSSHMerge(
    ctx: SimpleNamespace,
    cfg: Any,
    private: str,
    public: str,
) -> None:
    '''Assert nested constructor inputs and the root's forwarded merge call.'''
    made = ctx.namedDictMade
    assert (ctx.namedDict.call_args_list == [
        mock.call(prv=private, pub=public),
        mock.call(sshKey=made[0]),
        mock.call(usrCrd=made[1]),
        mock.call(gstCfg=made[2]),
        mock.call(fxtInf=made[3]),
    ])
    cfg._merge.assert_called_once_with(made[4])


def test__DEF__FXT_CFG_UsesNestedVMConfiguration() -> None:
    cfg = MUT._DEF__FXT_CFG
    assert (cfg.fxtInf.vmCfg.cfgTmpl == 'cfg-template--vm.yaml')
    assert (type(cfg.fxtInf.vmCfg.ctxData) is dict)
    assert (cfg.fxtInf.vmCfg.ctxData == {})
    assert (cfg.fxtInf.vmCfg.timeout == {
        'vmCreate': '15m', 'dvImport': '30m', 'vmStart': '15m',
        'vmRemove': '3m',
    })
    assert ('configFile' not in cfg)
    assert ('timeout' not in cfg)


def test_FuncParsVMcreate_RequiresConfiguration() -> None:
    with pytest.raises(TypeError, match='fxtCfgObj'):
        MUT.FuncParsVMcreate()


@pytest.mark.parametrize('fld', [
    'vmList', 'NSs', 'Secrets', 'VMs', 'sshKeyDirs',
])
def test_FuncParsVMcreate_DefaultListsAreIndependentAndMutable(
    vmTestContext: SimpleNamespace,
    fld: str,
) -> None:
    ctx = vmTestContext
    cfg = ctx.config()
    first = MUT.FuncParsVMcreate(fxtCfgObj=cfg)
    second = MUT.FuncParsVMcreate(fxtCfgObj=cfg)
    if (fld in ('NSs', 'Secrets', 'VMs')):
        firstList = getattr(first.ownResTrk, fld)
        secondList = getattr(second.ownResTrk, fld)
    else:
        firstList = getattr(first, fld)
        secondList = getattr(second, fld)
    assert (type(first.ownResTrk) is SimpleNamespace)
    assert (firstList is not secondList)
    assert (firstList == secondList == [])
    assert (ctx.namedDict.call_args_list == [
        mock.call(NSs=[], Secrets=[], VMs=[]),
        mock.call(NSs=[], Secrets=[], VMs=[]),
    ])
    firstList.append(mock.sentinel.entry)
    assert (firstList == [mock.sentinel.entry])
    assert (secondList == [])
    assert (first.fxtCfgObj is second.fxtCfgObj is cfg)


@pytest.mark.parametrize('fld', [
    'fxtCfgObj', 'vmList', 'ownResTrk', 'sshKeyDirs',
])
def test_FuncParsVMcreate_RejectsFieldRebinding(
    vmTestContext: SimpleNamespace,
    fld: str,
) -> None:
    fPars = MUT.FuncParsVMcreate(fxtCfgObj=vmTestContext.config())
    before = getattr(fPars, fld)
    with pytest.raises(FrozenInstanceError):
        setattr(fPars, fld, object())
    assert (getattr(fPars, fld) is before)


def test_FuncParsVMcreate_PreservesSuppliedListsAndConfiguration(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    cfg = ctx.config(count=0)
    before = ConfigSnapshot(cfg)
    vmList = []
    namespaces = []
    secrets = []
    vms = []
    sshKeyDirs = []
    ownResTrk = SimpleNamespace(NSs=namespaces, Secrets=secrets, VMs=vms)
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=cfg,
        vmList=vmList,
        ownResTrk=ownResTrk,
        sshKeyDirs=sshKeyDirs,
    )
    assert (fPars.fxtCfgObj is cfg)
    assert (fPars.vmList is vmList)
    assert (fPars.ownResTrk is ownResTrk)
    assert (fPars.ownResTrk.NSs is namespaces)
    assert (fPars.ownResTrk.Secrets is secrets)
    assert (fPars.ownResTrk.VMs is vms)
    assert (fPars.sshKeyDirs is sshKeyDirs)
    assert (vmList == namespaces == secrets == vms == sshKeyDirs == [])
    ctx.namedDict.assert_not_called()
    assert (ConfigSnapshot(cfg) == before)


def test_ConfigSnapshot_PreservesCompleteCustomContext(
    vmTestContext: SimpleNamespace,
) -> None:
    ctxData = {
        'vmNS': 'caller-namespace',
        'vmDataVolumeSource': {'http': {'url': 'caller-url'}},
        'sshPublicKeyB64': 'caller-key',
        'vmName': 'custom-name',
        'custom': {'nested': {'retained': True}},
    }
    cfg = vmTestContext.config(ctxData=ctxData)
    before = ConfigSnapshot(cfg)
    assert (before['fxtInf']['vmCfg']['ctxData'] == ctxData)
    assert (before['fxtInf']['vmCfg']['timeout'] == {
        'vmCreate': '15m', 'dvImport': '30m', 'vmStart': '15m',
        'vmRemove': '3m',
    })
    assert (ConfigSnapshot(cfg) == before)


@pytest.mark.parametrize('private', ['', ' \t', '$UNIT_EMPTY_KEY'])
def test__ResolveSSHkeys_BlankExpandedPrivate_ReviewRegression(
    vmTestContext: SimpleNamespace,
    private: str,
) -> None:
    ctx = vmTestContext
    if (private == '$UNIT_EMPTY_KEY'): ctx.expansions[private] = ''
    cfg = ctx.config(private=private, public='literal.pub')
    with pytest.raises(ValueError, match='can not be empty') as failure:
        MUT._ResolveSSHkeys(cfg, [])
    if (private == '$UNIT_EMPTY_KEY'):
        assert (private not in str(failure.value))
    ctx.cmdExec.assert_not_called()
    MUT.TemporaryDirectory.assert_not_called()
    MUT.Path.assert_not_called()
    ctx.namedDict.assert_not_called()
    cfg._merge.assert_not_called()


def test__ResolveSSHkeys_ReturnsNewConfigurationAndPreservesCaller(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    private = '/synthetic/source/id_rsa'
    privatePath = ctx.addPath(private, name='id_rsa')
    publicValue = '/synthetic/temp/id_rsa.pub'
    tempPath = ctx.paths['/synthetic/temp']
    publicPath = ctx.addPath(publicValue)
    tempPath.__truediv__.return_value = publicPath
    ctx.cmdExec.return_value.Run.return_value = CommandResult(
        True, 'ssh-ed25519 derived-public\n',
    )
    cfg = ctx.config(private=private, public=None)
    peer = ctx.config(private=private, public=publicValue)
    cfg._merge.return_value = peer
    before = ConfigSnapshot(cfg)
    sshKeyDirs = []

    resolved = MUT._ResolveSSHkeys(cfg, sshKeyDirs)

    assert (resolved is peer)
    assert (resolved.fxtInf.gstCfg.usrCrd.sshKey.pub == publicValue)
    assert (ConfigSnapshot(cfg) == before)
    assert (sshKeyDirs == [ctx.tempDir])
    AssertRootSSHMerge(ctx, cfg, private, publicValue)
    MUT.TemporaryDirectory.assert_called_once_with(prefix='fxtVM--ssh--')
    ctx.cmdExec.assert_called_once_with()
    ctx.cmdExec.return_value.Run.assert_called_once_with(
        ['ssh-keygen', '-y', '-P', '', '-f', private],
        input='', isSecret=True, spPars={'timeout': 30},
    )
    tempPath.chmod.assert_called_once_with(0o0700)
    publicPath.write_text.assert_called_once_with(
        'ssh-ed25519 derived-public\n', encoding='utf-8',
    )
    assert (privatePath.expanduser.call_count == 1)


@pytest.mark.parametrize('prv', ['', '.', './', '././', '$VMCTX__EMPTY'])
def test__ResolveSSHkeys_DotPrivateUsesSuppliedPublicLiterally(
    vmTestContext: SimpleNamespace,
    prv: str,
) -> None:
    ctx = vmTestContext
    if (prv == '$VMCTX__EMPTY'): ctx.expansions[prv] = ''
    privatePath = ctx.addPath(prv, name='')
    privatePath.__str__.return_value = '.'
    publicPath = ctx.addPath('literal.pub')
    cfg = ctx.config(private=prv, public='literal.pub')
    before = ConfigSnapshot(cfg)
    sshKeyDirs = []

    if (prv in ('', '$VMCTX__EMPTY')):
        with pytest.raises(ValueError, match='can not be empty'):
            MUT._ResolveSSHkeys(cfg, sshKeyDirs)
        assert (sshKeyDirs == [])
        assert (ConfigSnapshot(cfg) == before)
        MUT.Path.assert_not_called()
        MUT.TemporaryDirectory.assert_not_called()
        ctx.cmdExec.assert_not_called()
        ctx.namedDict.assert_not_called()
        cfg._merge.assert_not_called()
        return

    peer = ctx.config(private='.', public='literal.pub')
    cfg._merge.return_value = peer
    resolved = MUT._ResolveSSHkeys(cfg, sshKeyDirs)

    assert (resolved is peer)
    assert (resolved.fxtInf.gstCfg.usrCrd.sshKey.prv == '.')
    assert (resolved.fxtInf.gstCfg.usrCrd.sshKey.pub == 'literal.pub')
    assert (sshKeyDirs == [])
    assert (ConfigSnapshot(cfg) == before)
    AssertRootSSHMerge(ctx, cfg, '.', 'literal.pub')
    assert (MUT.Path.call_args_list == [
        mock.call(prv), mock.call('literal.pub'),
    ])
    ctx.cmdExec.assert_not_called()
    publicPath.open.assert_not_called()
    publicPath.read_text.assert_not_called()
    privatePath.expanduser.assert_called_once_with()
    publicPath.expanduser.assert_called_once_with()


@pytest.mark.parametrize('prv', ['', '.', './', '././', '$VMCTX__EMPTY'])
def test__ResolveSSHkeys_DotPrivateDerivationFailsWithCannedKeygenResult(
    vmTestContext: SimpleNamespace,
    prv: str,
) -> None:
    ctx = vmTestContext
    if (prv == '$VMCTX__EMPTY'): ctx.expansions[prv] = ''
    privatePath = ctx.addPath(prv, name='')
    privatePath.__str__.return_value = '.'
    tempPath = ctx.paths['/synthetic/temp']
    publicPath = ctx.addPath('/synthetic/temp/.pub')
    tempPath.__truediv__.return_value = publicPath
    privateOutput = 'PRIVATE-KEYGEN-STDOUT'
    privateError = 'PRIVATE-KEYGEN-STDERR'
    ctx.cmdExec.return_value.Run.return_value = CommandResult(
        False, privateOutput, privateError,
    )
    cfg = ctx.config(private=prv, public=None)
    before = ConfigSnapshot(cfg)
    sshKeyDirs = []

    if (prv in ('', '$VMCTX__EMPTY')):
        with pytest.raises(ValueError, match='can not be empty'):
            MUT._ResolveSSHkeys(cfg, sshKeyDirs)
        assert (sshKeyDirs == [])
        assert (ConfigSnapshot(cfg) == before)
        MUT.Path.assert_not_called()
        MUT.TemporaryDirectory.assert_not_called()
        ctx.cmdExec.assert_not_called()
        ctx.namedDict.assert_not_called()
        cfg._merge.assert_not_called()
        return

    with pytest.raises(
        ValueError, match='Public Key resolution failed',
    ) as failure:
        MUT._ResolveSSHkeys(cfg, sshKeyDirs)
    assert (privateOutput not in str(failure.value))
    assert (privateError not in str(failure.value))
    assert (privateOutput not in str(MUT.logs.mock_calls))
    assert (privateError not in str(MUT.logs.mock_calls))
    assert (sshKeyDirs == [ctx.tempDir])
    assert (ConfigSnapshot(cfg) == before)
    ctx.namedDict.assert_not_called()
    cfg._merge.assert_not_called()
    tempPath.chmod.assert_called_once_with(0o0700)
    assert (MUT.Path.call_args_list == [
        mock.call('/synthetic/temp'),
        mock.call(prv),
        mock.call('/synthetic/temp'),
    ])
    tempPath.__truediv__.assert_called_once_with('.pub')
    ctx.cmdExec.assert_called_once_with()
    ctx.cmdExec.return_value.Run.assert_called_once_with(
        ['ssh-keygen', '-y', '-P', '', '-f', '.'],
        input='', isSecret=True, spPars={'timeout': 30},
    )
    publicPath.write_text.assert_not_called()
    privatePath.expanduser.assert_called_once_with()


def VMRequest(
    ctx: SimpleNamespace,
    cfgTmpl: str = 'cfg-template--vm.yaml',
) -> Any:
    rootPath = ctx.paths.get('/synthetic/repo')
    if (rootPath is None): rootPath = ctx.addPath('/synthetic/repo')
    vmDir = ctx.paths.get('/synthetic/repo/conf/VM')
    if (vmDir is None): vmDir = ctx.addPath('/synthetic/repo/conf/VM')
    templateName = f'/synthetic/repo/conf/VM/{cfgTmpl}'
    templatePath = ctx.paths.get(templateName)
    if (templatePath is None): templatePath = ctx.addPath(templateName)
    rootPath.__truediv__.return_value = vmDir
    vmDir.__truediv__.return_value = templatePath
    return Request(rootPath)


def VMUserCredentials(cfg: Any) -> Any:
    '''Provide only a canned VM-wrapper `+` result; no merge logic.'''
    usrCrd = mock.MagicMock(name='userCredentials')
    sshKey = SimpleNamespace(
        prv='/synthetic/private-key', pub='/synthetic/public-key',
    )
    usrCrd.sshUsr = 'unit-user'
    usrCrd.sshKey = sshKey
    usrCrd.__add__.return_value = {'sshUsr': 'unit-user', 'sshKey': sshKey.prv}
    cfg.fxtInf.gstCfg.usrCrd = usrCrd
    return usrCrd


def RegisterVMSSHPaths(ctx: SimpleNamespace, cfg: Any) -> None:
    sshKey = cfg.fxtInf.gstCfg.usrCrd.sshKey
    ctx.addPath(sshKey.prv, name='private-key')
    ctx.addPath(sshKey.pub, name='public-key')


def AssertVMBaseHandoff(ctx: SimpleNamespace, base: Any) -> None:
    assert (ctx.namedDict.call_args_list[0] == mock.call({}))
    base._merge.assert_called_once_with(ctx.namedDictMade[0])


def AssertVMOverrideHandoff(
    ctx: SimpleNamespace,
    base: Any,
    afterArgs: Any,
    overrides: dict[str, Any],
    resolvedKey: Any = None,
) -> None:
    '''
    Assert the override and resolved SSH key handoff merge calls.

    :param ctx:         VM test context with the mocked `NamedDict`.
    :param base:        Base fixture configuration merged with `overrides`.
    :param afterArgs:   Configuration merged with the resolved SSH key pair.
    :param overrides:   Per-call fixture configuration overrides.
    :param resolvedKey:
        Expected resolved key pair (default: the `afterArgs` key pair).
    '''
    made = ctx.namedDictMade
    sshKey = (
        afterArgs.fxtInf.gstCfg.usrCrd.sshKey if (resolvedKey is None)
        else resolvedKey
    )
    assert (ctx.namedDict.call_args_list[0] == mock.call(overrides))
    assert (ctx.namedDict.call_args_list[1:6] == [
        mock.call(prv=sshKey.prv, pub=sshKey.pub),
        mock.call(sshKey=made[1]),
        mock.call(usrCrd=made[2]),
        mock.call(gstCfg=made[3]),
        mock.call(fxtInf=made[4]),
    ])
    base._merge.assert_called_once_with(made[0])
    afterArgs._merge.assert_called_once_with(made[5])


def NoneCredVMContext(
    cfg: Any,
    custom: dict[str, Any],
    vmName: str,
) -> dict[str, Any]:
    '''Return the expected render context for `credType` `None` (HTTP).'''
    return {
        **custom,
        'vmDataVolumeSource': {
            'http': {'url': cfg.fxtInf.gstCfg.imgSrc.CDI.url},
        },
        'vmNS': cfg.ns,
        'sshPublicKeyB64': 'canned-base64',
        'vmName': vmName,
    }


@pytest.mark.parametrize('errorType', [RuntimeError, ValueError])
def test_VMcreate_FailedCreationStopsBatchAndPreservesError(
    vmTestContext: SimpleNamespace,
    errorType: type[Exception],
) -> None:
    ctx = vmTestContext
    base = ctx.config()
    effective = ctx.config(
        count=2, namePfx='lpqe-test', ctxData={'fixture': 'canned'},
    )
    usrCrd = VMUserCredentials(effective)
    base._merge.return_value = effective
    vmList = []
    namespaces = []
    ownVMs = []
    sshKeyDirs = []
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=vmList,
        ownResTrk=SimpleNamespace(NSs=namespaces, Secrets=[], VMs=ownVMs),
        sshKeyDirs=sshKeyDirs,
    )
    client = mock.Mock(name='oc-client')
    ctx.ocFactory.return_value = client
    ctx.ocFactory.GetResUID.return_value = (True, '', '')
    MUT.YAMLcfg.Render.return_value = 'canned-manifest'
    primary = errorType('canned resource-create failure')
    MUT.VirtualMachine.Create.side_effect = primary
    templatePath = ctx.addPath('/synthetic/repo/conf/VM/cfg-template--vm.yaml')
    publicPath = ctx.addPath(usrCrd.sshKey.pub)
    SetVMPathText(publicPath, 'ssh-ed25519 canned-public\n')

    with pytest.raises(errorType) as caught:
        MUT.VMcreate(
            VMRequest(ctx), fPars,
            ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
        )

    assert (caught.value is primary)
    AssertVMBaseHandoff(ctx, base)
    assert (ctx.ocFactory.call_args_list == [mock.call(
        ns=effective.ns, kubeconfig=effective.kCfg,
    )])
    ctx.ocFactory.GetResUID.assert_called_once_with(
        client, 'VirtualMachine/lpqe-test-0',
    )
    MUT.Namespace.Create.assert_called_once()
    publicPath.open.assert_called_once_with(encoding='utf-8', newline='')
    MUT.base64.b64encode.assert_called_once_with(
        b'ssh-ed25519 canned-public\n',
    )
    assert (MUT.YAMLcfg.Render.call_args_list == [mock.call(
        templatePath,
        NoneCredVMContext(effective, {'fixture': 'canned'}, 'lpqe-test-0'),
    )])
    assert (MUT.OwnInf.call_args_list == [
        mock.call(owned=namespaces), mock.call(owned=ownVMs),
    ])
    createCall = MUT.VirtualMachine.Create.call_args
    assert (createCall.args[0] is client)
    assert (createCall.args[1] is MUT.ResCfg.return_value)
    assert (createCall.args[2] is MUT.OwnInf.return_value)
    assert (createCall.kwargs == {'optsNewRes': {'waitTime': '15m'}})
    assert (MUT.ResCfg.call_args_list[-1] == mock.call(yaml='canned-manifest'))
    usrCrd.__add__.assert_not_called()
    assert (vmList == ownVMs == sshKeyDirs == [])
    ctx.vmFactory.assert_not_called()
    ctx.virtCtlFactory.assert_not_called()


@pytest.mark.parametrize('count', [1, 2])
@pytest.mark.parametrize('dvNames', [
    [], ['actual-disk'], ['os', 'data', 'os'],
])
@pytest.mark.parametrize('reuse', [False, True])
@pytest.mark.parametrize('startVM', [False, True])
def test_VMcreate_WaitsReferencedImportsAfterTrackingAllVMs(
    vmTestContext: SimpleNamespace,
    count: int,
    dvNames: list[str],
    reuse: bool,
    startVM: bool,
) -> None:
    ctx = vmTestContext
    overrides = {
        'fxtInf': {
            'vmCfg': {
                'ctxData': {'custom': 'context'},
                'timeout': {'dvImport': '7m'},
                'count': count,
                'reuseExisting': reuse,
                'startVM': startVM,
            },
            'gstCfg': {'imgSrc': {'CDI': {'credType': 'None'}}},
        },
    }
    base = ctx.config()
    afterArgs = ctx.config(
        count=count, startVM=startVM, reuseExisting=reuse,
        namePfx='lpqe-test', ctxData={'custom': 'context'},
    )
    afterArgs.fxtInf.vmCfg.timeout.dvImport = '7m'
    effective = ctx.config(
        count=count, startVM=startVM, reuseExisting=reuse,
        namePfx='lpqe-test', ctxData={'custom': 'context'},
    )
    effective.fxtInf.vmCfg.timeout.dvImport = '7m'
    base._merge.return_value = afterArgs
    afterArgs._merge.return_value = effective
    afterUser = VMUserCredentials(afterArgs)
    usrCrd = VMUserCredentials(effective)
    RegisterVMSSHPaths(ctx, afterArgs)
    publicPath = ctx.paths[usrCrd.sshKey.pub]
    SetVMPathText(publicPath, 'ssh-ed25519 canned-public\n')
    vmList = []
    namespaces = []
    ownVMs = []
    sshKeyDirs = []
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=vmList,
        ownResTrk=SimpleNamespace(NSs=namespaces, Secrets=[], VMs=ownVMs),
        sshKeyDirs=sshKeyDirs,
    )
    clients = [mock.Mock(name=f'oc-{index}') for index in range(count)]
    wrappers = [mock.Mock(name=f'vm-{index}') for index in range(count)]
    virtClients = [mock.Mock(name=f'virt-{index}') for index in range(count)]
    ctx.ocFactory.side_effect = clients
    ctx.ocFactory.GetResUID.side_effect = [
        (True, 'existing' if reuse else '', '') for _ in range(count)
    ]
    ctx.vmFactory.side_effect = wrappers
    ctx.virtCtlFactory.side_effect = virtClients
    MUT.YAMLcfg.Render.return_value = 'canned-manifest'
    if not reuse:
        resources = []
        for index in range(count):
            volumes = [
                {'name': 'ignored', 'persistentVolumeClaim': {
                    'claimName': 'not-a-datavolume',
                }},
            ]
            for name in dvNames:
                volumes.append({
                    'name': name,
                    'dataVolume': {'name': f'{index}-{name}'},
                })
            resources.append({
                'metadata': {'uid': f'uid-{index}'},
                'spec': {'template': {'spec': {'volumes': volumes}}},
            })
        MUT.VirtualMachine.Create.side_effect = resources

    events = []

    def MakeWait(index: int) -> Any:
        def Wait(*args: Any, **kwargs: Any) -> Any:
            assert (len(vmList) == count)
            assert (len(MUT.VirtualMachine.Create.call_args_list) == count)
            events.append(('wait', index, args, kwargs))
            return CommandResult(True)
        return Wait

    def MakeStart(index: int) -> Any:
        def Start(**kwargs: Any) -> bool:
            assert (vmList == wrappers)
            assert (kwargs == {'timeout': '15m'})
            events.append(('start', index))
            return True
        return Start

    for (index, client) in enumerate(clients):
        client.Run.side_effect = MakeWait(index)
        wrappers[index].Start.side_effect = MakeStart(index)

    templatePath = ctx.addPath('/synthetic/repo/conf/VM/cfg-template--vm.yaml')
    result = MUT.VMcreate(
        VMRequest(ctx), fPars,
        ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
        **overrides,
    )

    AssertVMOverrideHandoff(ctx, base, afterArgs, overrides)
    assert (result == wrappers)
    assert (result is not vmList)
    assert (vmList == wrappers)
    expectedNames = (
        ['lpqe-test'] if (count == 1)
        else ['lpqe-test-0', 'lpqe-test-1']
    )
    assert (ctx.ocFactory.GetResUID.call_args_list == [
        mock.call(client, f'VirtualMachine/{name}')
        for (client, name) in zip(clients, expectedNames)
    ])
    assert (ctx.ocFactory.call_args_list == [mock.call(
        ns=effective.ns, kubeconfig=effective.kCfg,
    ) for _ in range(count)])
    assert (ctx.virtCtlFactory.call_args_list == [mock.call(
        ns=effective.ns, kubeconfig=effective.kCfg,
    ) for _ in range(count)])
    assert (MUT.YAMLcfg.Render.call_count == (0 if reuse else count))
    assert (MUT.VirtualMachine.Create.call_count == (0 if reuse else count))
    assert (MUT.OwnInf.call_args_list == [
        mock.call(owned=namespaces),
        *([] if reuse else [mock.call(owned=ownVMs) for _ in range(count)]),
    ])
    assert all(
        (call.kwargs['owned'] is ownVMs)
        for call in MUT.OwnInf.call_args_list[1:]
    )
    assert (ownVMs == [])
    assert (usrCrd.__add__.call_args_list == [
        mock.call({'sshKey': '/synthetic/private-key'})
        for _ in range(count)
    ])
    assert (MUT.base64.b64encode.call_args_list == (
        [] if reuse else [mock.call(b'ssh-ed25519 canned-public\n')]
    ))
    if not reuse:
        assert (MUT.YAMLcfg.Render.call_args_list == [
            mock.call(templatePath, NoneCredVMContext(
                effective, {'custom': 'context'}, name,
            )) for name in expectedNames
        ])
    uniqueNames = {
        (): [],
        ('actual-disk',): ['actual-disk'],
        ('os', 'data', 'os'): ['os', 'data'],
    }[tuple(dvNames)]
    expectedEvents = []
    for index in range(count):
        if not reuse:
            for name in uniqueNames:
                expectedEvents.append((
                    'wait', index,
                    ('wait', f'DataVolume/{index}-{name}'),
                    {'for': 'jsonpath={.status.phase}=Succeeded',
                     'timeout': '7m'},
                ))
        if startVM: expectedEvents.append(('start', index))
        else: wrappers[index].Start.assert_not_called()
    assert (events == expectedEvents)
    for (index, client) in enumerate(clients):
        expectedWaits = [] if reuse else [
            mock.call(
                'wait', f'DataVolume/{index}-{name}',
                **{'for': 'jsonpath={.status.phase}=Succeeded'}, timeout='7m',
            ) for name in uniqueNames
        ]
        assert (client.Run.call_args_list == expectedWaits)


def test_VMcreate_NegativeCountIsRejectedBeforeAnyOperation(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    overrides = {'fxtInf': {'vmCfg': {'count': -1}}}
    base = ctx.config()
    afterArgs = ctx.config(count=-1)
    effective = ctx.config(count=-1)
    VMUserCredentials(afterArgs)
    VMUserCredentials(effective)
    base._merge.return_value = afterArgs
    afterArgs._merge.return_value = effective
    RegisterVMSSHPaths(ctx, afterArgs)
    vmList = []
    namespaces = []
    ownVMs = []
    sshKeyDirs = []
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=vmList,
        ownResTrk=SimpleNamespace(NSs=namespaces, Secrets=[], VMs=ownVMs),
        sshKeyDirs=sshKeyDirs,
    )

    with pytest.raises(ValueError, match='must not be negative'):
        MUT.VMcreate(
            Request(None), fPars,
            ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
            **overrides,
        )

    AssertVMOverrideHandoff(ctx, base, afterArgs, overrides)
    assert (vmList == ownVMs == sshKeyDirs == [])
    assert (ctx.ocFactory.call_count == 0)
    ctx.ocFactory.GetResUID.assert_not_called()
    ctx.vmFactory.assert_not_called()
    ctx.virtCtlFactory.assert_not_called()
    MUT.Namespace.Create.assert_not_called()
    MUT.YAMLcfg.Render.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    assert (ctx.paths['/synthetic/private-key'].expanduser.call_count == 1)
    assert (ctx.paths['/synthetic/public-key'].expanduser.call_count == 1)
    MUT.TemporaryDirectory.assert_not_called()


def test_VMcreate_ZeroCountReturnsEmptyWithoutAnyOperation(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    overrides = {'fxtInf': {'vmCfg': {'count': 0}}}
    base = ctx.config()
    afterArgs = ctx.config(count=0)
    effective = ctx.config(count=0)
    VMUserCredentials(afterArgs)
    VMUserCredentials(effective)
    base._merge.return_value = afterArgs
    afterArgs._merge.return_value = effective
    RegisterVMSSHPaths(ctx, afterArgs)
    vmList = []
    namespaces = []
    ownVMs = []
    sshKeyDirs = []
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=vmList,
        ownResTrk=SimpleNamespace(NSs=namespaces, Secrets=[], VMs=ownVMs),
        sshKeyDirs=sshKeyDirs,
    )

    result = MUT.VMcreate(
        Request(None), fPars,
        ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
        **overrides,
    )

    AssertVMOverrideHandoff(ctx, base, afterArgs, overrides)
    assert (result == [])
    assert (vmList == ownVMs == sshKeyDirs == [])
    assert (ctx.ocFactory.call_count == 0)
    ctx.ocFactory.GetResUID.assert_not_called()
    ctx.vmFactory.assert_not_called()
    ctx.virtCtlFactory.assert_not_called()
    MUT.Namespace.Create.assert_not_called()
    MUT.YAMLcfg.Render.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    MUT.TemporaryDirectory.assert_not_called()


@pytest.mark.parametrize('argument', ['request', 'fPars'])
def test_VMcreate_RejectsInternalKeywordReplacementBeforeOperations(
    vmTestContext: SimpleNamespace,
    argument: str,
) -> None:
    ctx = vmTestContext
    base = ctx.config(count=0)
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    with pytest.raises(TypeError, match='multiple values'):
        MUT.VMcreate(
            Request(None), fPars, ocCLI=ctx.ocFactory,
            virtCtlCLI=ctx.virtCtlFactory, **{argument: object()},
        )

    ctx.namedDict.assert_not_called()
    base._merge.assert_not_called()
    ctx.ocFactory.assert_not_called()
    ctx.virtCtlFactory.assert_not_called()
    assert (fPars.vmList == fPars.ownResTrk.VMs == fPars.sshKeyDirs == [])


def test_VMcreate_BatchReturnsNewVMsAndAppendsConfirmedOwnership(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    overrides = {
        'cliCfg': {
            'ocCfg': {'context': 'oc-context'},
            'vcCfg': {'kubeconfig': '/virt-kubeconfig'},
        },
        'fxtInf': {
            'vmCfg': {
                'timeout': {'vmCreate': '4m'},
                'count': 2, 'namePfx': 'unit', 'cfgTmpl': 'template.yaml',
            },
            'gstCfg': {'usrCrd': {'sshUsr': 'unit-user'}},
        },
    }
    base = ctx.config(ctxData={'custom': 'context'})
    afterArgs = ctx.config(
        count=2, cfgTmpl='template.yaml', namePfx='unit',
        ctxData={'custom': 'context'},
    )
    effective = ctx.config(
        count=2, cfgTmpl='template.yaml', namePfx='unit',
        ctxData={'custom': 'context'},
    )
    afterArgs.cliCfg.ocCfg = {'context': 'oc-context'}
    effective.cliCfg.ocCfg = {'context': 'oc-context'}
    afterArgs.cliCfg.vcCfg = {'kubeconfig': '/virt-kubeconfig'}
    effective.cliCfg.vcCfg = {'kubeconfig': '/virt-kubeconfig'}
    afterArgs.fxtInf.vmCfg.timeout.vmCreate = '4m'
    effective.fxtInf.vmCfg.timeout.vmCreate = '4m'
    VMUserCredentials(afterArgs)
    usrCrd = VMUserCredentials(effective)
    base._merge.return_value = afterArgs
    afterArgs._merge.return_value = effective
    RegisterVMSSHPaths(ctx, afterArgs)
    publicPath = ctx.paths[usrCrd.sshKey.pub]
    SetVMPathText(publicPath, 'ssh-ed25519 canned-public\n')
    clients = [mock.Mock(name='oc-zero'), mock.Mock(name='oc-one')]
    virtClients = [mock.Mock(name='virt-zero'), mock.Mock(name='virt-one')]
    wrappers = [mock.Mock(name='vm-zero'), mock.Mock(name='vm-one')]
    resources = [
        {'metadata': {'uid': 'uid-zero'},
         'spec': {'template': {'spec': {'volumes': []}}}},
        {'metadata': {'uid': 'uid-one'},
         'spec': {'template': {'spec': {'volumes': []}}}},
    ]
    ctx.ocFactory.side_effect = clients
    ctx.ocFactory.GetResUID.side_effect = [(True, '', ''), (True, '', '')]
    ctx.virtCtlFactory.side_effect = virtClients
    ctx.vmFactory.side_effect = wrappers
    MUT.YAMLcfg.Render.return_value = 'canned-manifest'
    MUT.VirtualMachine.Create.side_effect = resources
    vmList = []
    namespaces = []
    ownVMs = []
    sshKeyDirs = []
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=vmList,
        ownResTrk=SimpleNamespace(NSs=namespaces, Secrets=[], VMs=ownVMs),
        sshKeyDirs=sshKeyDirs,
    )
    templatePath = ctx.addPath('/synthetic/repo/conf/VM/template.yaml')

    created = MUT.VMcreate(
        VMRequest(ctx, 'template.yaml'), fPars,
        ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
        **overrides,
    )

    AssertVMOverrideHandoff(ctx, base, afterArgs, overrides)
    assert (created == wrappers)
    assert (created is not vmList)
    assert (vmList == wrappers)
    assert (ctx.ocFactory.call_args_list == [
        mock.call(ns=effective.ns, kubeconfig=effective.kCfg,
                  context='oc-context'),
        mock.call(ns=effective.ns, kubeconfig=effective.kCfg,
                  context='oc-context'),
    ])
    assert (ctx.virtCtlFactory.call_args_list == [
        mock.call(ns=effective.ns, kubeconfig=effective.kCfg),
        mock.call(ns=effective.ns, kubeconfig=effective.kCfg),
    ])
    assert (ctx.ocFactory.GetResUID.call_args_list == [
        mock.call(clients[0], 'VirtualMachine/unit-0'),
        mock.call(clients[1], 'VirtualMachine/unit-1'),
    ])
    MUT.base64.b64encode.assert_called_once_with(
        b'ssh-ed25519 canned-public\n',
    )
    assert (MUT.YAMLcfg.Render.call_args_list == [
        mock.call(templatePath, NoneCredVMContext(
            effective, {'custom': 'context'}, vmName,
        )) for vmName in ('unit-0', 'unit-1')
    ])
    assert (MUT.VirtualMachine.Create.call_count == 2)
    assert (MUT.OwnInf.call_args_list == [
        mock.call(owned=namespaces),
        mock.call(owned=ownVMs),
        mock.call(owned=ownVMs),
    ])
    assert (MUT.OwnInf.call_args_list[1].kwargs['owned'] is ownVMs)
    assert (MUT.OwnInf.call_args_list[2].kwargs['owned'] is ownVMs)
    assert (ownVMs == [])
    assert ([item.kwargs['name'] for item in ctx.vmFactory.call_args_list] == [
        'unit-0', 'unit-1',
    ])
    assert ([item.kwargs['oc'] for item in ctx.vmFactory.call_args_list] ==
            clients)
    assert ([item.kwargs['virtCtl'] for item in ctx.vmFactory.call_args_list]
            == virtClients)
    assert (usrCrd.__add__.call_args_list == [
        mock.call({'sshKey': '/synthetic/private-key'}),
        mock.call({'sshKey': '/synthetic/private-key'}),
    ])
    assert (MUT.VirtualMachine.Create.call_args_list[0].args[2]
            is MUT.OwnInf.return_value)
    assert (MUT.VirtualMachine.Create.call_args_list[1].args[2]
            is MUT.OwnInf.return_value)
    assert (ctx.namedDictMade[-1].name == effective.ns)
    assert (ctx.namedDict.call_args_list[-1] == mock.call(name=effective.ns))


def test_VMcreate_ReusesExistingVMWithoutRenderOrApply(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    overrides = {'fxtInf': {'vmCfg': {
        'reuseExisting': True, 'namePfx': 'single',
    }}}
    base = ctx.config(ctxData={'custom': 'context'})
    afterArgs = ctx.config(
        reuseExisting=True, namePfx='single', ctxData={'custom': 'context'},
    )
    effective = ctx.config(
        reuseExisting=True, namePfx='single', ctxData={'custom': 'context'},
    )
    VMUserCredentials(afterArgs)
    VMUserCredentials(effective)
    base._merge.return_value = afterArgs
    afterArgs._merge.return_value = effective
    RegisterVMSSHPaths(ctx, afterArgs)
    client = mock.Mock(name='existing-oc-client')
    ctx.ocFactory.return_value = client
    ctx.ocFactory.GetResUID.return_value = (True, 'existing-uid', '')
    vmList = []
    namespaces = []
    ownVMs = []
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=vmList,
        ownResTrk=SimpleNamespace(NSs=namespaces, Secrets=[], VMs=ownVMs),
        sshKeyDirs=[],
    )

    created = MUT.VMcreate(
        VMRequest(ctx), fPars,
        ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
        **overrides,
    )

    AssertVMOverrideHandoff(ctx, base, afterArgs, overrides)
    assert (created == [ctx.vmFactory.return_value])
    assert (vmList == created)
    assert (ownVMs == [])
    ctx.ocFactory.GetResUID.assert_called_once_with(
        client, 'VirtualMachine/single',
    )
    MUT.YAMLcfg.Render.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    assert (MUT.OwnInf.call_args_list == [mock.call(owned=namespaces)])


def test_VMcreate_PreflightLookupFailureRaisesWithoutTracking(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    base = ctx.config(ctxData={'custom': 'context'})
    effective = ctx.config(ctxData={'custom': 'context'})
    VMUserCredentials(effective)
    base._merge.return_value = effective
    client = mock.Mock(name='oc-client')
    ctx.ocFactory.return_value = client
    ctx.ocFactory.GetResUID.return_value = (False, '', 'lookup failed')
    vmList = []
    namespaces = []
    ownVMs = []
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=vmList,
        ownResTrk=SimpleNamespace(NSs=namespaces, Secrets=[], VMs=ownVMs),
        sshKeyDirs=[],
    )

    with pytest.raises(RuntimeError, match='lookup failed'):
        MUT.VMcreate(
            VMRequest(ctx), fPars,
            ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
        )

    AssertVMBaseHandoff(ctx, base)
    assert (ctx.ocFactory.call_args_list == [mock.call(
        ns=effective.ns, kubeconfig=effective.kCfg,
    )])
    ctx.ocFactory.GetResUID.assert_called_once_with(
        client, 'VirtualMachine/unit-vm',
    )
    assert (vmList == ownVMs == [])
    MUT.VirtualMachine.Create.assert_not_called()
    MUT.VM.assert_not_called()
    MUT.YAMLcfg.Render.assert_not_called()


def test_VMcreate_ExistingVMWithoutReuseIsRejected(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    base = ctx.config(ctxData={'custom': 'context'})
    effective = ctx.config(ctxData={'custom': 'context'})
    VMUserCredentials(effective)
    base._merge.return_value = effective
    client = mock.Mock(name='existing-oc-client')
    ctx.ocFactory.return_value = client
    ctx.ocFactory.GetResUID.return_value = (True, 'existing-uid', '')
    vmList = []
    namespaces = []
    ownVMs = []
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=vmList,
        ownResTrk=SimpleNamespace(NSs=namespaces, Secrets=[], VMs=ownVMs),
        sshKeyDirs=[],
    )

    with pytest.raises(RuntimeError, match='already exists'):
        MUT.VMcreate(
            VMRequest(ctx), fPars,
            ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
        )

    AssertVMBaseHandoff(ctx, base)
    ctx.ocFactory.GetResUID.assert_called_once_with(
        client, 'VirtualMachine/unit-vm',
    )
    assert (vmList == ownVMs == [])
    MUT.VirtualMachine.Create.assert_not_called()
    MUT.VM.assert_not_called()
    MUT.YAMLcfg.Render.assert_not_called()


@pytest.mark.parametrize('failureMessage', [
    '`EnsureRes()`: Failed at `WAITED`.',
    '`EnsureRes()`: Failed at `DRY_RUN`.',
    'Invalid identity',
])
def test_VMcreate_FailedNewResRaisesWithoutRecordingOwnership(
    vmTestContext: SimpleNamespace,
    failureMessage: str,
) -> None:
    ctx = vmTestContext
    base = ctx.config(ctxData={'custom': 'context'})
    effective = ctx.config(ctxData={'custom': 'context'})
    VMUserCredentials(effective)
    base._merge.return_value = effective
    client = mock.Mock(name='oc-client')
    ctx.ocFactory.return_value = client
    ctx.ocFactory.GetResUID.return_value = (True, '', '')
    MUT.YAMLcfg.Render.return_value = 'canned-manifest'
    primary = RuntimeError(failureMessage)
    MUT.VirtualMachine.Create.side_effect = primary
    publicPath = ctx.addPath(effective.fxtInf.gstCfg.usrCrd.sshKey.pub)
    SetVMPathText(publicPath, 'ssh-ed25519 canned-public\n')
    vmList = []
    namespaces = []
    ownVMs = []
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=vmList,
        ownResTrk=SimpleNamespace(NSs=namespaces, Secrets=[], VMs=ownVMs),
        sshKeyDirs=[],
    )

    with pytest.raises(RuntimeError) as caught:
        MUT.VMcreate(
            VMRequest(ctx), fPars,
            ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
        )

    AssertVMBaseHandoff(ctx, base)
    assert (caught.value is primary)
    publicPath.open.assert_called_once_with(encoding='utf-8', newline='')
    MUT.base64.b64encode.assert_called_once_with(
        b'ssh-ed25519 canned-public\n',
    )
    assert (MUT.OwnInf.call_args_list == [
        mock.call(owned=namespaces), mock.call(owned=ownVMs),
    ])
    assert (MUT.VirtualMachine.Create.call_count == 1)
    assert (vmList == ownVMs == [])
    MUT.VM.assert_not_called()
    ctx.virtCtlFactory.assert_not_called()


@pytest.mark.parametrize('outcome', ['false', 'exception'])
@pytest.mark.parametrize('customTimeout', [None, '7m'])
def test_VMcreate_StartFailureRaisesAfterImports(
    vmTestContext: SimpleNamespace,
    outcome: str,
    customTimeout: str | None,
) -> None:
    ctx = vmTestContext
    startTimeout = (customTimeout or '15m')
    base = ctx.config(startVM=True, ctxData={'custom': 'context'})
    effective = ctx.config(
        count=2, startVM=True, ctxData={'custom': 'context'},
    )
    effective.fxtInf.vmCfg.timeout.vmStart = startTimeout
    VMUserCredentials(effective)
    base._merge.return_value = effective
    client = mock.Mock(name='oc-client')
    ctx.ocFactory.return_value = client
    ctx.ocFactory.GetResUID.return_value = (True, '', '')
    wrapper = mock.Mock(name='vm-wrapper')
    laterWrapper = mock.Mock(name='later-vm-wrapper')
    wrapper.name = 'unit-vm'
    wrapper.namespace = effective.ns
    wrapper.Start.return_value = False
    primary = RuntimeError('canned start exception')
    if (outcome == 'exception'): wrapper.Start.side_effect = primary
    ctx.vmFactory.side_effect = [wrapper, laterWrapper]
    MUT.YAMLcfg.Render.return_value = 'canned-manifest'
    MUT.VirtualMachine.Create.return_value = {
        'metadata': {'uid': 'canned-uid'},
        'spec': {'template': {'spec': {'volumes': [
            {'dataVolume': {'name': 'boot-dv'}},
        ]}}},
    }
    client.Run.return_value = CommandResult(True)
    publicPath = ctx.addPath(effective.fxtInf.gstCfg.usrCrd.sshKey.pub)
    SetVMPathText(publicPath, 'ssh-ed25519 canned-public\n')
    vmList = []
    namespaces = []
    ownVMs = []
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=vmList,
        ownResTrk=SimpleNamespace(NSs=namespaces, Secrets=[], VMs=ownVMs),
        sshKeyDirs=[],
    )

    with pytest.raises(RuntimeError) as caught:
        MUT.VMcreate(
            VMRequest(ctx), fPars,
            ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
        )

    if (outcome == 'exception'): assert (caught.value is primary)
    else:
        assert (str(caught.value) == (
            f'VM `{effective.ns}/unit-vm` failed to start.'
        ))
    AssertVMBaseHandoff(ctx, base)
    publicPath.open.assert_called_once_with(encoding='utf-8', newline='')
    MUT.base64.b64encode.assert_called_once_with(
        b'ssh-ed25519 canned-public\n',
    )
    client.Run.assert_called_once_with(
        'wait', 'DataVolume/boot-dv',
        **{'for': 'jsonpath={.status.phase}=Succeeded'}, timeout='30m',
    )
    wrapper.Start.assert_called_once_with(timeout=startTimeout)
    laterWrapper.Start.assert_not_called()
    assert (vmList == [wrapper, laterWrapper])
    assert (MUT.OwnInf.call_args_list == [
        mock.call(owned=namespaces), mock.call(owned=ownVMs),
        mock.call(owned=ownVMs),
    ])
    assert (ownVMs == [])


def test_VMcreate_FailedImportRaisesAfterTrackingAllVMs(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    base = ctx.config(
        count=2, startVM=True, namePfx='unit', ctxData={'custom': 'context'},
    )
    effective = ctx.config(
        count=2, startVM=True, namePfx='unit', ctxData={'custom': 'context'},
    )
    usrCrd = VMUserCredentials(effective)
    base._merge.return_value = effective
    clients = [mock.Mock(name='oc-zero'), mock.Mock(name='oc-one')]
    wrappers = [mock.Mock(name='vm-zero'), mock.Mock(name='vm-one')]
    ctx.ocFactory.side_effect = clients
    ctx.ocFactory.GetResUID.side_effect = [(True, '', ''), (True, '', '')]
    ctx.vmFactory.side_effect = wrappers
    MUT.YAMLcfg.Render.return_value = 'canned-manifest'
    MUT.VirtualMachine.Create.side_effect = [
        {
            'metadata': {'uid': f'uid-{index}'},
            'spec': {'template': {'spec': {'volumes': [
                {'dataVolume': {'name': f'boot-{index}'}},
            ]}}},
        } for index in range(2)
    ]
    clients[0].Run.return_value = CommandResult(
        False, 'synthetic-import-stdout', 'synthetic-import-error',
    )
    publicPath = ctx.addPath(usrCrd.sshKey.pub)
    SetVMPathText(publicPath, 'ssh-ed25519 canned-public\n')
    vmList = []
    ownVMs = []
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=vmList,
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=ownVMs),
        sshKeyDirs=[],
    )

    with pytest.raises(RuntimeError) as caught:
        MUT.VMcreate(
            VMRequest(ctx), fPars,
            ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
        )

    assert (str(caught.value) == (
        'DataVolume `boot-0` import failed: synthetic-import-error'
    ))
    AssertVMBaseHandoff(ctx, base)
    assert (vmList == wrappers)
    assert (MUT.VirtualMachine.Create.call_count == 2)
    clients[0].Run.assert_called_once_with(
        'wait', 'DataVolume/boot-0',
        **{'for': 'jsonpath={.status.phase}=Succeeded'}, timeout='30m',
    )
    clients[1].Run.assert_not_called()
    for wrapper in wrappers: wrapper.Start.assert_not_called()
    assert (MUT.OwnInf.call_args_list == [
        mock.call(owned=fPars.ownResTrk.NSs),
        mock.call(owned=ownVMs), mock.call(owned=ownVMs),
    ])
    assert (ownVMs == [])


def PrepareVMSection(
    ctx: SimpleNamespace,
    base: Any,
    effective: Any,
) -> tuple[SimpleNamespace, Any, Any]:
    base._merge.return_value = effective
    effective._merge.return_value = effective
    rootPath = mock.MagicMock(name='requestRoot')
    configPath = mock.MagicMock(name='vmConfigPath')
    templatePath = mock.MagicMock(name='vmTemplatePath')
    rootPath.__truediv__.return_value = configPath
    configPath.__truediv__.return_value = templatePath
    templatePath.resolve.return_value = templatePath
    request = Request(rootPath)
    private = ctx.addPath(effective.fxtInf.gstCfg.usrCrd.sshKey.prv)
    public = ctx.addPath(effective.fxtInf.gstCfg.usrCrd.sshKey.pub)
    ctx.addPath('/synthetic/access').read_text.return_value = 'access-value'
    ctx.addPath('/synthetic/secret').read_text.return_value = 'secret-value'
    publicStream = mock.MagicMock(name='publicKeyStream')
    public.open.return_value.__enter__.return_value = publicStream
    publicStream.read.return_value = 'ssh-ed25519 canned-public\n'
    guest = mock.MagicMock(name='guestCredentials')
    guest.sshUsr = effective.fxtInf.gstCfg.usrCrd.sshUsr
    guest.sshKey = effective.fxtInf.gstCfg.usrCrd.sshKey
    guest.__add__.return_value = {
        'sshUsr': guest.sshUsr,
        'sshKey': guest.sshKey.prv,
    }
    effective.fxtInf.gstCfg.usrCrd = guest
    ctx.ocFactory.GetResUID.return_value = (True, '', '')
    ctx.vmFactory.return_value = mock.sentinel.vm
    ctx.virtCtlFactory.return_value = mock.sentinel.virtCtl
    MUT.Namespace.Create.reset_mock()
    MUT.Secret.Create.reset_mock()
    MUT.VirtualMachine.Create.return_value = {
        'metadata': {'uid': 'canned-vm-uid'}, 'spec': {},
    }
    MUT.YAMLcfg.Render.reset_mock()
    MUT.YAMLcfg.Render.return_value = 'canned-vm-manifest'
    return request, configPath, templatePath


def InvokeVMSection(
    ctx: SimpleNamespace,
    request: SimpleNamespace,
    pars: Any,
    **overrides: Any,
) -> Any:
    return MUT.VMcreate(
        request,
        pars,
        ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory,
        **overrides,
    )


@pytest.mark.parametrize('cfgTmpl', [
    'custom.yaml',
    'nested/custom.yaml',
    '/absolute/custom.yaml',
])
@pytest.mark.parametrize('perCall', [False, True])
def test_VMcreate_ResolvesCustomTemplateFromRequestRoot(
    vmTestContext: SimpleNamespace,
    cfgTmpl: str,
    perCall: bool,
) -> None:
    ctx = vmTestContext
    base = ctx.config()
    effective = ctx.config(cfgTmpl=cfgTmpl)
    baseBefore = ConfigSnapshot(base)
    (request, configPath, templatePath) = PrepareVMSection(
        ctx, base, effective,
    )
    failure = OSError('canned render failure')
    MUT.YAMLcfg.Render.side_effect = failure
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    overrides = {'fxtInf': {'vmCfg': {'cfgTmpl': cfgTmpl}}} if perCall else {}
    with pytest.raises(OSError) as caught:
        InvokeVMSection(ctx, request, pars, **overrides)
    assert (caught.value is failure)
    request.config.rootpath.__truediv__.assert_called_once_with('conf/VM')
    configPath.__truediv__.assert_called_once_with(cfgTmpl)
    templatePath.resolve.assert_called_once_with()
    assert (MUT.YAMLcfg.Render.call_args.args[0] is templatePath)
    assert (MUT.YAMLcfg.Render.call_args.args[1] == {
        'vmDataVolumeSource': {
            'http': {
                'url': effective.fxtInf.gstCfg.imgSrc.CDI.url,
            },
        },
        'vmNS': effective.ns,
        'sshPublicKeyB64': 'canned-base64',
        'vmName': 'unit-vm',
    })
    assert (mock.call(overrides) in ctx.namedDict.call_args_list)
    assert (MUT.urlsplit.call_args == mock.call(
        effective.fxtInf.gstCfg.imgSrc.CDI.url,
    ))
    MUT.Namespace.Create.assert_called_once()
    MUT.Secret.Create.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    assert (ConfigSnapshot(base) == baseBefore)


@pytest.mark.parametrize('baseContext', [
    {},
    {
        'inherited': 'kept',
        'vmMemory': '2Gi',
        'custom': {'inherited': 'kept', 'selection': 'base'},
    },
])
@pytest.mark.parametrize('context', [
    'omitted',
    {},
    {
        'vmNS': 'ignored-ns',
        'sshPublicKeyB64': 'ignored-public',
        'vmDataVolumeSource': {'http': {'url': 'ignored-url'}},
        'vmName': 'ignored-name',
        'vmMemory': '4Gi',
        'custom': {'selection': 'override'},
    },
])
@pytest.mark.parametrize('count', [1, 2])
def test_VMcreate_ContextMergesCustomFieldsAndGeneratesManagedFields(
    vmTestContext: SimpleNamespace,
    baseContext: dict[str, Any],
    context: dict[str, Any] | str,
    count: int,
) -> None:
    ctx = vmTestContext
    base = ctx.config(ctxData=baseContext)
    baseBefore = ConfigSnapshot(base)
    contextBefore = (
        json.loads(json.dumps(context))
        if (context != 'omitted') else context
    )
    mergedContext = dict(baseContext)
    if (context != 'omitted'):
        mergedContext.update(context)
        if ('custom' in context):
            mergedContext['custom'] = {
                **baseContext.get('custom', {}),
                **context['custom'],
            }
    effective = ctx.config(
        count=count, namePfx='custom', ctxData=mergedContext,
    )
    (request, _, templatePath) = PrepareVMSection(ctx, base, effective)
    overrides = (
        {'fxtInf': {'vmCfg': {'ctxData': context}}}
        if (context != 'omitted') else {}
    )
    expectedSource = {'http': {'url': effective.fxtInf.gstCfg.imgSrc.CDI.url}}
    expectedContext = {
        **mergedContext,
        'vmNS': effective.ns,
        'sshPublicKeyB64': 'canned-base64',
        'vmDataVolumeSource': expectedSource,
    }
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    result = InvokeVMSection(ctx, request, pars, **overrides)
    assert (result == pars.vmList == ([mock.sentinel.vm] * count))
    assert (MUT.YAMLcfg.Render.call_count == count)
    assert (MUT.YAMLcfg.Render.call_args_list == [
        mock.call(templatePath, {
            **expectedContext,
            'vmName': f'custom-{index}' if (count > 1) else 'custom',
        })
        for index in range(count)
    ])
    assert (ctx.namedDict.call_args_list[1] == mock.call(overrides))
    base._merge.assert_called_once_with(ctx.namedDictMade[1])
    assert (MUT.urlsplit.call_args == mock.call(
        effective.fxtInf.gstCfg.imgSrc.CDI.url,
    ))
    assert (MUT.Secret.Create.call_count == 0)
    assert (MUT.Namespace.Create.call_count == 1)
    assert (MUT.VirtualMachine.Create.call_count == count)
    assert (mergedContext == expectedContext)
    assert (effective.fxtInf.vmCfg.ctxData is mergedContext)
    assert (ConfigSnapshot(base) == baseBefore)
    assert (base.fxtInf.vmCfg.ctxData is baseContext)
    if (context != 'omitted'):
        assert (json.loads(json.dumps(context)) == contextBefore)


@pytest.mark.parametrize('context', [
    SimpleNamespace(),
    [],
    'invalid',
    1,
    False,
])
def test_VMcreate_DefaultContextRejectsMismatchedTypes(
    vmTestContext: SimpleNamespace,
    context: Any,
) -> None:
    ctx = vmTestContext
    base = ctx.config()
    effective = ctx.config()
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    override = {'fxtInf': {'vmCfg': {'ctxData': context}}}
    failure = TypeError('canned NamedDict context type failure')
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    ctx.namedDict.side_effect = failure
    with pytest.raises(TypeError) as caught:
        InvokeVMSection(ctx, request, pars, **override)
    assert (caught.value is failure)
    assert (ctx.namedDict.call_args == mock.call(override))
    base._merge.assert_not_called()
    ctx.ocFactory.assert_not_called()
    ctx.vmFactory.assert_not_called()
    ctx.virtCtlFactory.assert_not_called()
    MUT.Path.assert_not_called()
    MUT.urlsplit.assert_not_called()
    MUT.Namespace.Create.assert_not_called()
    MUT.Secret.Create.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    MUT.YAMLcfg.Render.assert_not_called()
    assert (pars.vmList == pars.ownResTrk.VMs == [])


@pytest.mark.parametrize('credType', ['None', 'K8s'])
def test_VMcreate_NativeSecretFilesPreserveNormalizedNUL(
    vmTestContext: SimpleNamespace,
    credType: str,
) -> None:
    ctx = vmTestContext
    effective = ctx.config(credType=credType)
    effective.fxtInf.gstCfg.imgSrc.CDI.credInfo.vaultInfo = {
        'file--AccKeyId': '/synthetic/access',
        'file--AccKeySecret': '/synthetic/secret',
    }
    base = ctx.config()
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    access = ctx.paths['/synthetic/access']
    secret = ctx.paths['/synthetic/secret']
    access.read_text.return_value = ' \taccess\x00value\r\n'
    secret.read_text.return_value = ' secret\x00value \r\n'
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    InvokeVMSection(ctx, request, pars)
    if (credType == 'K8s'):
        assert (access.read_text.call_args == mock.call(encoding='utf-8'))
        assert (secret.read_text.call_args == mock.call(encoding='utf-8'))
        assert (MUT.base64.b64encode.call_args_list == [
            mock.call(b'access\x00value'),
            mock.call(b'secret\x00value'),
            mock.call(b'ssh-ed25519 canned-public\n'),
        ])
        MUT.Secret.Create.assert_called_once()
        assert (pars.ownResTrk.Secrets == [])
    else:
        access.read_text.assert_not_called()
        secret.read_text.assert_not_called()
        MUT.Secret.Create.assert_not_called()


@pytest.mark.parametrize('credType', ['None', 'K8s'])
def test_VMcreate_CustomContextRetainsTemplateFieldsAndManagedCredentials(
    vmTestContext: SimpleNamespace,
    credType: str,
) -> None:
    ctx = vmTestContext
    context = {
        'vmName': 'ignored-name',
        'vmNS': 'ignored-ns',
        'sshPublicKeyB64': 'ignored-public',
        'vmDataVolumeSource': {'http': {'url': 'ignored-url'}},
        'vmCpuCores': 2,
        'vmDataVolumeName': 'custom-disk',
        'vmStorageClass': 'custom-storage',
        'vmStorageSize': '42Gi',
    }
    effective = ctx.config(credType=credType, ctxData=dict(context))
    base = ctx.config()
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    InvokeVMSection(ctx, request, pars)
    rendered = MUT.YAMLcfg.Render.call_args.args[1]
    assert (rendered['vmName'] == 'unit-vm')
    assert (rendered['vmNS'] == effective.ns)
    assert (rendered['vmCpuCores'] == 2)
    assert (rendered['vmDataVolumeName'] == 'custom-disk')
    assert (rendered['vmStorageClass'] == 'custom-storage')
    assert (rendered['vmStorageSize'] == '42Gi')
    assert (rendered['sshPublicKeyB64'] == 'canned-base64')
    if (credType == 'K8s'):
        assert (rendered['vmDataVolumeSource'] == {
            's3': {
                'secretRef': 'unit-image-secret',
                'url': effective.fxtInf.gstCfg.imgSrc.CDI.url,
            },
        })
        MUT.Secret.Create.assert_called_once()
    else:
        assert (rendered['vmDataVolumeSource'] == {
            'http': {'url': effective.fxtInf.gstCfg.imgSrc.CDI.url},
        })
        MUT.Secret.Create.assert_not_called()
    assert (context['vmName'] == 'ignored-name')
    assert (context['vmNS'] == 'ignored-ns')
    assert (context['sshPublicKeyB64'] == 'ignored-public')


_VM_SECTION_TEXT_FIELDS = [
    ('fxtInf', 'gstCfg', 'imgSrc', 'CDI', 'url'),
    ('fxtInf', 'gstCfg', 'imgSrc', 'CDI', 'credInfo', 'name'),
    ('fxtInf', 'gstCfg', 'imgSrc', 'CDI', 'credInfo', 'vaultInfo',
     'file--AccKeyId'),
    ('fxtInf', 'gstCfg', 'imgSrc', 'CDI', 'credInfo', 'vaultInfo',
     'file--AccKeySecret'),
    ('fxtInf', 'gstCfg', 'usrCrd', 'sshKey', 'prv'),
]


@pytest.mark.parametrize('fieldPath', _VM_SECTION_TEXT_FIELDS)
@pytest.mark.parametrize('badValue', ['', ' '])
def test_VMcreate_EmptyValuesFailBeforeResourceEffects(
    vmTestContext: SimpleNamespace,
    fieldPath: tuple[str, ...],
    badValue: str,
) -> None:
    ctx = vmTestContext
    effective = ctx.config(credType='K8s')
    if (fieldPath[-2] == 'vaultInfo'):
        effective.fxtInf.gstCfg.imgSrc.CDI.credInfo.vaultInfo[
            fieldPath[-1]
        ] = badValue
    else:
        parent = effective
        for field in fieldPath[:-1]:
            parent = getattr(parent, field)
        setattr(parent, fieldPath[-1], badValue)
    base = ctx.config()
    baseBefore = ConfigSnapshot(base)
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    if (fieldPath[-1] == 'url'):
        MUT.urlsplit.return_value = SimpleNamespace(scheme='', hostname=None)
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    with pytest.raises(ValueError) as caught:
        InvokeVMSection(ctx, request, pars, fxtInf={})
    assert (fieldPath[-1] in str(caught.value))
    assert (ctx.namedDict.call_args_list[1] == mock.call({'fxtInf': {}}))
    if (fieldPath[-1] == 'url'): MUT.urlsplit.assert_called_once_with(badValue)
    elif (fieldPath[-1] == 'prv'):
        MUT.urlsplit.assert_not_called()
        MUT.Namespace.Create.assert_not_called()
    else:
        MUT.urlsplit.assert_called_once_with(
            effective.fxtInf.gstCfg.imgSrc.CDI.url,
        )
        MUT.Namespace.Create.assert_called_once()
    MUT.Secret.Create.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    MUT.YAMLcfg.Render.assert_not_called()
    assert (pars.vmList == pars.ownResTrk.VMs == [])
    assert (ConfigSnapshot(base) == baseBefore)


@pytest.mark.parametrize('field,value', [
    ('type', 'HTTP'),
    ('credType', 'BitWarden'),
    ('vaultType', 'BitWarden'),
    ('vaultType', 'None'),
    ('vaultType', 'FutureVault'),
    ('vaultType', ''),
    ('vaultType', None),
    ('url', 'not-a-url'),
    ('url', 'https://[invalid-host'),
    ('url', 'https://:443/image.qcow2'),
    ('url', 'https://user@:443/image.qcow2'),
])
@pytest.mark.parametrize('customContext', [False, True])
def test_VMcreate_UnsupportedSourceFailsBeforeResourceEffects(
    vmTestContext: SimpleNamespace,
    field: str,
    value: Any,
    customContext: bool,
) -> None:
    ctx = vmTestContext
    effective = ctx.config(credType='K8s')
    source = effective.fxtInf.gstCfg.imgSrc.CDI
    if (field == 'vaultType'): source.credInfo.vaultType = value
    else: setattr(source, field, value)
    parseFailure = None
    parseResult = None
    if (field == 'url'):
        MUT.urlsplit.side_effect = None
        if (value == 'not-a-url'):
            parseResult = SimpleNamespace(
                scheme='ftp', hostname='invalid-scheme.invalid',
            )
        elif (value == 'https://[invalid-host'):
            parseFailure = ValueError('canned URL parse failure')
            MUT.urlsplit.side_effect = parseFailure
        elif (value == 'https://:443/image.qcow2'):
            parseResult = SimpleNamespace(scheme='https', hostname=None)
        else: parseResult = SimpleNamespace(scheme='https', hostname='')
        if (parseResult is not None): MUT.urlsplit.return_value = parseResult
    base = ctx.config()
    baseBefore = ConfigSnapshot(base)
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    overrides = {}
    if customContext:
        overrides = {'fxtInf': {'vmCfg': {'ctxData': {
            'customTemplateField': 'retained',
            'vmDataVolumeSource': {'http': {'url': 'ignored-url'}},
        }}}}
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    with pytest.raises(ValueError) as caught:
        InvokeVMSection(ctx, request, pars, **overrides)
    assert (field in str(caught.value))
    assert (ctx.namedDict.call_args_list[1] == mock.call(overrides))
    if (field == 'url'):
        MUT.urlsplit.assert_called_once_with(value)
        if (parseFailure is None):
            assert (MUT.urlsplit.return_value is parseResult)
        else:
            assert (MUT.urlsplit.side_effect is parseFailure)
            assert (caught.value is not parseFailure)
    elif (field in ('type', 'credType')): MUT.urlsplit.assert_not_called()
    else:
        MUT.urlsplit.assert_called_once_with(
            effective.fxtInf.gstCfg.imgSrc.CDI.url,
        )
    MUT.Namespace.Create.assert_called_once()
    MUT.Secret.Create.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    MUT.YAMLcfg.Render.assert_not_called()
    assert (pars.ownResTrk.Secrets == [])
    assert (ConfigSnapshot(base) == baseBefore)


@pytest.mark.parametrize('field', ['file--AccKeyId', 'file--AccKeySecret'])
@pytest.mark.parametrize('case', [
    'missing', 'directory', 'empty', 'binary', 'multiline',
])
def test_VMcreate_UnreadableOrInvalidRawValueFailsBeforeEffects(
    vmTestContext: SimpleNamespace,
    field: str,
    case: str,
) -> None:
    ctx = vmTestContext
    effective = ctx.config(credType='K8s')
    badPath = '/synthetic/bad-credential'
    badFile = ctx.addPath(badPath)
    if (case == 'missing'):
        badFile.read_text.side_effect = OSError('canned missing file')
    elif (case == 'directory'):
        badFile.read_text.side_effect = IsADirectoryError('canned directory')
    elif (case == 'empty'): badFile.read_text.return_value = ' \n'
    elif (case == 'binary'):
        badFile.read_text.side_effect = UnicodeDecodeError(
            'utf-8', b'\xff', 0, 1, 'canned invalid text',
        )
    else: badFile.read_text.return_value = 'one\ntwo'
    effective.fxtInf.gstCfg.imgSrc.CDI.credInfo.vaultInfo[field] = badPath
    base = ctx.config()
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    with pytest.raises(ValueError, match=field):
        InvokeVMSection(ctx, request, pars)
    assert (badFile.read_text.call_args == mock.call(encoding='utf-8'))
    MUT.Secret.Create.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    MUT.YAMLcfg.Render.assert_not_called()
    assert (pars.ownResTrk.Secrets == [])


@pytest.mark.parametrize('phase', [
    'create', 'os-error', 'exception', 'secret-timeout',
])
def test_VMcreate_SecretFailureSuppressesPrivateProcessDiagnostics(
    vmTestContext: SimpleNamespace,
    phase: str,
) -> None:
    ctx = vmTestContext
    effective = ctx.config(credType='K8s')
    base = ctx.config()
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    failureTypes = {
        'create': RuntimeError,
        'os-error': OSError,
        'exception': Exception,
        'secret-timeout': TimeoutError,
    }
    secretFailure = failureTypes[phase](
        f'private diagnostic {phase} credential-value',
    )
    MUT.Secret.Create.side_effect = secretFailure
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    with pytest.raises(
        RuntimeError, match='Could not prepare image Secret',
    ) as caught:
        InvokeVMSection(ctx, request, pars)
    assert (caught.value is not secretFailure)
    assert caught.value.__suppress_context__
    assert ('credential-value' not in str(caught.value))
    assert ('credential-value' not in str(MUT.logs.mock_calls))
    MUT.YAMLcfg.Render.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    assert (pars.ownResTrk.Secrets == [])


def test_VMcreate_RenderFailureLeavesSecretOwnedForFixtureCleanup(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    effective = ctx.config(credType='K8s')
    base = ctx.config()
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    owner = pars.ownResTrk.Secrets
    secretTuple = (
        mock.sentinel.client,
        'Secret/unit-image-secret',
        'secret-uid',
    )

    def Own(owned: Any) -> Any:
        return SimpleNamespace(owned=owned)

    def CreateSecret(client: Any, spec: Any, info: Any) -> None:
        assert (info.owned is owner)
        owner.append(secretTuple)

    MUT.OwnInf.side_effect = Own
    MUT.Secret.Create.side_effect = CreateSecret
    failure = OSError('canned render failure')
    MUT.YAMLcfg.Render.side_effect = failure
    with pytest.raises(OSError) as caught:
        InvokeVMSection(ctx, request, pars)
    assert (caught.value is failure)
    assert (owner == [secretTuple])
    MUT.Secret.Create.assert_called_once()
    MUT.VirtualMachine.Create.assert_not_called()
    assert (pars.ownResTrk.VMs == [])


def test_VMcreate_EmptyRenderLeavesSecretOwnedForFixtureCleanup(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    effective = ctx.config(credType='K8s')
    base = ctx.config()
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    owner = pars.ownResTrk.Secrets
    secretTuple = (
        mock.sentinel.client,
        'Secret/unit-image-secret',
        'secret-uid',
    )

    def Own(owned: Any) -> Any:
        return SimpleNamespace(owned=owned)

    def CreateSecret(client: Any, spec: Any, info: Any) -> None:
        assert (info.owned is owner)
        owner.append(secretTuple)

    MUT.OwnInf.side_effect = Own
    MUT.Secret.Create.side_effect = CreateSecret
    MUT.YAMLcfg.Render.return_value = ' \n'
    with pytest.raises(ValueError, match='render nonempty'):
        InvokeVMSection(ctx, request, pars)
    assert (owner == [secretTuple])
    MUT.VirtualMachine.Create.assert_not_called()
    assert (pars.ownResTrk.VMs == [])


@pytest.mark.parametrize('url', [
    'https://PUBLIC-IMAGE-HOST.invalid/windows.qcow2',
    'http://PUBLIC-IMAGE-HOST.invalid/windows.qcow2',
    'https://PLACEHOLDER-USER@PUBLIC-IMAGE-HOST.invalid/windows.qcow2',
    'https://PUBLIC-IMAGE-HOST.invalid/windows.qcow2?'
    'X-Amz-Signature=PLACEHOLDER%2BONLY&part=one+two&part=%2f#placeholder',
])
@pytest.mark.parametrize('authFields', ['inherited', 'missing', 'invalid'])
def test_VMcreate_NoneCredentialsRendersHTTPWithoutCredentialReadsOrSecrets(
    vmTestContext: SimpleNamespace,
    url: str,
    authFields: str,
) -> None:
    ctx = vmTestContext
    effective = ctx.config(count=2, credType='None', url=url)
    source = effective.fxtInf.gstCfg.imgSrc.CDI
    if (authFields == 'missing'): source.credInfo = None
    elif (authFields == 'invalid'):
        source.credInfo = SimpleNamespace(
            name='', vaultType='Unsupported', vaultInfo={},
        )
    base = ctx.config()
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    access = ctx.paths['/synthetic/access']
    secret = ctx.paths['/synthetic/secret']
    access.read_text.side_effect = AssertionError('image read is not expected')
    secret.read_text.side_effect = AssertionError('image read is not expected')
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    result = InvokeVMSection(ctx, request, pars)
    assert (len(result) == len(pars.vmList) == 2)
    access.read_text.assert_not_called()
    secret.read_text.assert_not_called()
    MUT.Secret.Create.assert_not_called()
    assert (MUT.YAMLcfg.Render.call_count == 2)
    assert ([call.args[1]['vmDataVolumeSource']
             for call in MUT.YAMLcfg.Render.call_args_list] == [
        {'http': {'url': url}}, {'http': {'url': url}},
    ])
    assert (MUT.urlsplit.call_args == mock.call(url))


@pytest.mark.parametrize('override', ['private-only', 'pair', 'unchanged'])
def test_VMcreate_PrivateOverridePublicIdentity_ReviewRegression(
    vmTestContext: SimpleNamespace,
    override: str,
) -> None:
    ctx = vmTestContext
    basePrivate = '/synthetic/base-private'
    basePublic = '/synthetic/base-public'
    alternatePrivate = '/synthetic/alternate-private'
    explicitPublic = '/synthetic/explicit-public'
    derivedPublic = '/synthetic/temp/alternate-private.pub'
    base = ctx.config(private=basePrivate, public=basePublic, credType='None')
    if (override == 'private-only'):
        afterArgs = ctx.config(
            private=alternatePrivate, public=basePublic, credType='None',
        )
        effective = ctx.config(
            private=alternatePrivate, public=None, credType='None',
        )
        resolved = ctx.config(
            private=alternatePrivate, public=derivedPublic, credType='None',
        )
        overrides = {'fxtInf': {'gstCfg': {'usrCrd': {'sshKey': {
            'prv': alternatePrivate,
        }}}}}
        (request, _, _) = PrepareVMSection(ctx, effective, resolved)
        afterArgs._merge.return_value = effective
        ctx.paths[alternatePrivate].name = 'alternate-private'
        ctx.addPath(ctx.tempDir.name).__truediv__.return_value = (
            ctx.paths[derivedPublic]
        )
        oldPublic = ctx.addPath(basePublic)
        expectedPrivate = alternatePrivate
        expectedPublicPath = derivedPublic
        expectedPublic = 'ssh-ed25519 canned-public\n'
    elif (override == 'pair'):
        afterArgs = ctx.config(
            private=alternatePrivate, public=explicitPublic, credType='None',
        )
        resolved = ctx.config(
            private=alternatePrivate, public=explicitPublic, credType='None',
        )
        overrides = {'fxtInf': {'gstCfg': {'usrCrd': {'sshKey': {
            'prv': alternatePrivate, 'pub': explicitPublic,
        }}}}}
        (request, _, _) = PrepareVMSection(ctx, afterArgs, resolved)
        expectedPrivate = alternatePrivate
        expectedPublicPath = explicitPublic
        expectedPublic = 'ssh-ed25519 explicit-pair\n'
    else:
        afterArgs = ctx.config(
            private=basePrivate, public=basePublic, credType='None',
        )
        resolved = ctx.config(
            private=basePrivate, public=basePublic, credType='None',
        )
        overrides = {'fxtInf': {'gstCfg': {'usrCrd': {'sshKey': {
            'prv': basePrivate,
        }}}}}
        (request, _, _) = PrepareVMSection(ctx, afterArgs, resolved)
        expectedPrivate = basePrivate
        expectedPublicPath = basePublic
        expectedPublic = 'ssh-ed25519 canned-public\n'
    base._merge.return_value = afterArgs
    publicPath = ctx.paths[expectedPublicPath]
    publicStream = publicPath.open.return_value.__enter__.return_value
    publicStream.read.return_value = expectedPublic
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    result = InvokeVMSection(ctx, request, pars, **overrides)

    assert (len(result) == 1)
    assert (MUT.VM.call_args.kwargs['sshKey'] == expectedPrivate)
    assert (MUT.base64.b64encode.call_args_list == [
        mock.call(expectedPublic.encode()),
    ])
    assert (
        MUT.YAMLcfg.Render.call_args.args[1]['sshPublicKeyB64'] ==
        'canned-base64'
    )
    assert (publicPath.open.call_args == mock.call(
        encoding='utf-8', newline='',
    ))
    MUT.Secret.Create.assert_not_called()
    if (override == 'private-only'):
        assert (ctx.namedDict.call_args_list[0] == mock.call(overrides))
        assert (ctx.namedDict.call_args_list[1] == mock.call(pub=None))
        assert (base._merge.call_args == mock.call(ctx.namedDictMade[0]))
        assert (afterArgs._merge.call_args == mock.call(ctx.namedDictMade[5]))
        assert (effective._merge.call_args == mock.call(ctx.namedDictMade[10]))
        assert (oldPublic.open.call_count == 0)
        MUT.TemporaryDirectory.assert_called_once_with(prefix='fxtVM--ssh--')
        ctx.cmdExec.return_value.Run.assert_called_once_with(
            ['ssh-keygen', '-y', '-P', '', '-f', alternatePrivate],
            input='', isSecret=True, spPars={'timeout': 30},
        )
    else:
        assert (ctx.namedDict.call_args_list[0] == mock.call(overrides))
        assert (base._merge.call_args == mock.call(ctx.namedDictMade[0]))
        assert (afterArgs._merge.call_args == mock.call(ctx.namedDictMade[5]))
        MUT.TemporaryDirectory.assert_not_called()
        ctx.cmdExec.assert_not_called()
    assert (base.fxtInf.gstCfg.usrCrd.sshKey.prv == basePrivate)
    assert (base.fxtInf.gstCfg.usrCrd.sshKey.pub == basePublic)


def test_VMcreate_K8sRawSecretPassesCannedInputsToMockedHandler(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    base = ctx.config()
    effective = ctx.config(credType='K8s')
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    access = ctx.paths['/synthetic/access']
    secret = ctx.paths['/synthetic/secret']
    public = ctx.paths['/synthetic/public-key']
    accessRaw = ' access\x00value \n'
    secretRaw = ' secret\x00value \n'
    access.read_text.return_value = accessRaw
    secret.read_text.return_value = secretRaw
    publicStream = public.open.return_value.__enter__.return_value
    publicStream.read.return_value = 'ssh-ed25519 public\r\n'
    MUT.base64.b64encode.side_effect = [
        b'access-b64', b'secret-b64', b'public-b64',
    ]
    MUT.ResCfg.side_effect = [
        mock.sentinel.nsCfg, mock.sentinel.secretCfg, mock.sentinel.vmCfg,
    ]
    MUT.OwnInf.side_effect = [
        mock.sentinel.nsInfo, mock.sentinel.secretInfo, mock.sentinel.vmInfo,
    ]
    MUT.YAMLcfg.Render.return_value = 'canned-vm-manifest'
    ordered = mock.Mock()
    ordered.attach_mock(MUT.Namespace.Create, 'namespace')
    ordered.attach_mock(MUT.Secret.Create, 'secret')
    ordered.attach_mock(MUT.YAMLcfg.Render, 'render')
    ordered.attach_mock(MUT.VirtualMachine.Create, 'vm')
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    result = InvokeVMSection(ctx, request, pars)

    assert (len(result) == 1)
    assert ([call[0] for call in ordered.mock_calls] == [
        'namespace', 'secret', 'render', 'vm',
    ])
    assert (access.read_text.call_args == mock.call(encoding='utf-8'))
    assert (secret.read_text.call_args == mock.call(encoding='utf-8'))
    assert (MUT.base64.b64encode.call_args_list == [
        mock.call(b'access\x00value'),
        mock.call(b'secret\x00value'),
        mock.call(b'ssh-ed25519 public\r\n'),
    ])
    secretCall = MUT.ResCfg.call_args_list[1]
    secretSpec = secretCall.kwargs['spec']
    assert (vars(secretSpec) == {
        'type': 'Opaque',
        'data': {'accessKeyId': 'access-b64', 'secretKey': 'secret-b64'},
    })
    assert (secretCall.kwargs['meta'].namespace == 'unit-namespace')
    assert (secretCall.kwargs['meta'].name == 'unit-image-secret')
    assert (MUT.Secret.Create.call_args == mock.call(
        ctx.ocFactory.return_value,
        mock.sentinel.secretCfg,
        mock.sentinel.secretInfo,
    ))
    assert (MUT.OwnInf.call_args_list == [
        mock.call(owned=pars.ownResTrk.NSs),
        mock.call(owned=pars.ownResTrk.Secrets),
        mock.call(owned=pars.ownResTrk.VMs),
    ])
    assert (pars.ownResTrk.Secrets == pars.ownResTrk.VMs == [])
    retained = repr((
        MUT.logs.mock_calls, MUT.ResCfg.call_args_list,
        MUT.YAMLcfg.Render.call_args_list,
        MUT.VirtualMachine.Create.call_args_list,
    ))
    assert (accessRaw.strip() not in retained)
    assert (secretRaw.strip() not in retained)


def test_VMcreate_NestedOverridesDoNotMutateDefaults(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    base = ctx.config()
    effectiveCustom = ctx.config(reuseExisting=True)
    effectiveCustom.cliCfg.ocCfg = {'context': 'custom-context'}
    (request, _, _) = PrepareVMSection(ctx, base, effectiveCustom)
    customUser = effectiveCustom.fxtInf.gstCfg.usrCrd
    customUser.sshUsr = 'custom-user'
    customUser.__add__.return_value = {
        'sshUsr': 'custom-user', 'sshKey': '/synthetic/private-key',
    }
    effectiveDefault = ctx.config(reuseExisting=True)
    defaultUser = VMUserCredentials(effectiveDefault)
    overrides = {
        'cliCfg': {'ocCfg': {'context': 'custom-context'}},
        'fxtInf': {
            'vmCfg': {'reuseExisting': True},
            'gstCfg': {'usrCrd': {'sshUsr': 'custom-user'}},
        },
    }
    overridesBefore = json.dumps(overrides)
    base._merge.side_effect = [effectiveCustom, effectiveDefault]
    ctx.ocFactory.GetResUID.return_value = (True, 'existing-uid', '')
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    first = InvokeVMSection(ctx, request, pars, **overrides)
    second = InvokeVMSection(ctx, request, pars)

    assert (len(first) == len(second) == 1)
    assert (ctx.namedDict.call_args_list[0] == mock.call(overrides))
    assert (ctx.namedDict.call_args_list.count(mock.call(overrides)) == 1)
    assert (ctx.namedDict.call_args_list[7] == mock.call({}))
    assert (base._merge.call_args_list == [
        mock.call(ctx.namedDictMade[0]),
        mock.call(ctx.namedDictMade[7]),
    ])
    assert (effectiveCustom._merge.call_args == mock.call(
        ctx.namedDictMade[5],
    ))
    assert (ctx.ocFactory.call_args_list == [
        mock.call(ns='unit-namespace', kubeconfig='/synthetic/kubeconfig',
                  context='custom-context'),
        mock.call(ns='unit-namespace', kubeconfig='/synthetic/kubeconfig'),
    ])
    assert (ctx.virtCtlFactory.call_args_list == [
        mock.call(ns='unit-namespace', kubeconfig='/synthetic/kubeconfig'),
        mock.call(ns='unit-namespace', kubeconfig='/synthetic/kubeconfig'),
    ])
    assert (MUT.VM.call_args_list[0].kwargs['sshUsr'] == 'custom-user')
    assert (MUT.VM.call_args_list[1].kwargs['sshUsr'] == 'unit-user')
    assert (
        MUT.VM.call_args_list[0].kwargs['sshKey'] == '/synthetic/private-key'
    )
    assert (
        MUT.VM.call_args_list[1].kwargs['sshKey'] == '/synthetic/private-key'
    )
    assert (base.cliCfg.ocCfg == base.cliCfg.vcCfg == {})
    assert (base.fxtInf.gstCfg.usrCrd.sshUsr == 'unit-user')
    assert (effectiveDefault.fxtInf.gstCfg.usrCrd is defaultUser)
    assert (json.dumps(overrides) == overridesBefore)
    MUT.YAMLcfg.Render.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()


@pytest.mark.parametrize('keyForm', [
    'literal', 'env', 'braced-env', 'home', 'env-home', 'normalized',
])
def test_VMcreate_IdentityPathMatchesGuestConsumer(
    vmTestContext: SimpleNamespace,
    keyForm: str,
) -> None:
    ctx = vmTestContext
    canonicalPrivate = '/synthetic/private-key'
    public = '/synthetic/public-key'
    keyInput = {
        'literal': canonicalPrivate,
        'env': '$VMCTX__KEY_DIR/private-key',
        'braced-env': '${VMCTX__KEY_DIR}/private-key',
        'home': '~/private-key',
        'env-home': '$VMCTX__KEY_HOME',
        'normalized': '/synthetic//./private-key',
    }[keyForm]
    if (keyForm in ('env', 'braced-env')):
        ctx.expansions[keyInput] = canonicalPrivate
    elif (keyForm == 'env-home'): ctx.expansions[keyInput] = '~/private-key'
    effective = ctx.config(private=keyInput, public=public, credType='None')
    resolved = ctx.config(
        private=canonicalPrivate, public=public, credType='None',
    )
    base = ctx.config(
        private=canonicalPrivate, public=public, credType='None',
    )
    overrides = {'fxtInf': {}}
    (request, _, _) = PrepareVMSection(ctx, effective, resolved)
    base._merge.return_value = effective
    expandedPrivate = ctx.expansions.get(keyInput, keyInput)
    privatePath = ctx.paths.get(expandedPrivate)
    if (privatePath is None): privatePath = ctx.addPath(expandedPrivate)
    privatePath.expanduser.return_value = ctx.paths[canonicalPrivate]
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    result = InvokeVMSection(ctx, request, pars, **overrides)

    assert (len(result) == 1)
    AssertVMOverrideHandoff(
        ctx, base, effective, overrides, resolved.fxtInf.gstCfg.usrCrd.sshKey,
    )
    assert (MUT.VM.call_args.kwargs['sshKey'] == canonicalPrivate)
    assert (MUT.os.path.expandvars.call_args_list == [
        mock.call(keyInput), mock.call(public),
    ])
    assert (privatePath.expanduser.call_args == mock.call())
    assert (ctx.paths[public].open.call_args == mock.call(
        encoding='utf-8', newline='',
    ))
    assert (MUT.base64.b64encode.call_args_list == [
        mock.call(b'ssh-ed25519 canned-public\n'),
    ])
    MUT.TemporaryDirectory.assert_not_called()
    ctx.cmdExec.assert_not_called()


def test_VMcreate_CannedRenderRawS3PublicKeyAndClientContext(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    url = 'https://PUBLIC-IMAGE-HOST.invalid/windows.qcow2?part=one+two'
    base = ctx.config()
    effective = ctx.config(credType='K8s', url=url)
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    access = ctx.paths['/synthetic/access']
    secret = ctx.paths['/synthetic/secret']
    public = ctx.paths['/synthetic/public-key']
    accessRaw = 'synthetic-access-value'
    secretRaw = 'synthetic-secret-value'
    access.read_text.return_value = f' {accessRaw} \n'
    secret.read_text.return_value = f' {secretRaw} \n'
    MUT.base64.b64encode.return_value = b'canned-base64'
    MUT.Secret.Create.side_effect = [None, MUT.Secret.SpecMismatch('changed')]
    MUT.YAMLcfg.Render.return_value = 'canned-vm-manifest'
    ordered = mock.Mock()
    ordered.attach_mock(MUT.Namespace.Create, 'namespace')
    ordered.attach_mock(MUT.Secret.Create, 'secret')
    ordered.attach_mock(MUT.YAMLcfg.Render, 'render')
    ordered.attach_mock(MUT.VirtualMachine.Create, 'vm')
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    result = InvokeVMSection(ctx, request, pars)

    assert (len(result) == 1)
    rendered = MUT.YAMLcfg.Render.call_args.args[1]
    assert (rendered['vmDataVolumeSource'] == {'s3': {
        'secretRef': 'unit-image-secret', 'url': url,
    }})
    assert (rendered['vmNS'] == 'unit-namespace')
    assert (rendered['sshPublicKeyB64'] == 'canned-base64')
    assert (rendered['vmName'] == 'unit-vm')
    assert (MUT.base64.b64encode.call_args_list[:3] == [
        mock.call(accessRaw.encode()), mock.call(secretRaw.encode()),
        mock.call('ssh-ed25519 canned-public\n'.encode()),
    ])
    secretCall = MUT.ResCfg.call_args_list[1]
    assert (vars(secretCall.kwargs['meta']) == {
        'namespace': 'unit-namespace', 'name': 'unit-image-secret',
    })
    assert (vars(secretCall.kwargs['spec']) == {
        'type': 'Opaque',
        'data': {'accessKeyId': 'canned-base64',
                 'secretKey': 'canned-base64'},
    })
    assert (ctx.ocFactory.call_args == mock.call(
        ns='unit-namespace', kubeconfig='/synthetic/kubeconfig',
    ))
    assert (ctx.virtCtlFactory.call_args == mock.call(
        ns='unit-namespace', kubeconfig='/synthetic/kubeconfig',
    ))
    assert (MUT.VM.call_args.kwargs['sshKey'] == '/synthetic/private-key')
    assert ([call[0] for call in ordered.mock_calls] == [
        'namespace', 'secret', 'render', 'vm',
    ])
    assert (access.read_text.call_count == secret.read_text.call_count == 1)
    assert (public.open.call_count == 1)
    retained = repr((
        MUT.logs.mock_calls, MUT.ResCfg.call_args_list,
        MUT.YAMLcfg.Render.call_args_list,
        MUT.VirtualMachine.Create.call_args_list,
    ))
    assert (accessRaw not in retained)
    assert (secretRaw not in retained)

    access.read_text.return_value = 'changed-access-value'
    secret.read_text.return_value = 'changed-secret-value'
    with pytest.raises(RuntimeError, match='different data'):
        InvokeVMSection(ctx, request, pars)
    assert ([call[0] for call in ordered.mock_calls] == [
        'namespace', 'secret', 'render', 'vm', 'namespace', 'secret',
    ])
    assert (MUT.YAMLcfg.Render.call_count == 1)
    assert (MUT.VirtualMachine.Create.call_count == 1)
    assert (public.open.call_count == 1)


def test_VMcreate_SuppliedPrivateReuseBypassesPublicAndImageReads(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    base = ctx.config()
    effective = ctx.config(
        private='/never/read/key', public='/never/read/public',
        reuseExisting=True, credType='Unsupported',
        url='not-an-image-url',
    )
    source = effective.fxtInf.gstCfg.imgSrc.CDI
    source.type = 'Unsupported'
    source.credInfo = None
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    access = ctx.paths['/synthetic/access']
    secret = ctx.paths['/synthetic/secret']
    public = ctx.paths['/never/read/public']
    access.read_text.side_effect = AssertionError('unexpected access read')
    secret.read_text.side_effect = AssertionError('unexpected secret read')
    public.open.side_effect = AssertionError('unexpected public-key read')
    ctx.ocFactory.GetResUID.return_value = (True, 'reuse-uid', '')
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    result = InvokeVMSection(ctx, request, pars)

    assert (result == [ctx.vmFactory.return_value])
    assert (MUT.VM.call_args.kwargs['sshKey'] == '/never/read/key')
    assert (ctx.ocFactory.GetResUID.call_args == mock.call(
        ctx.ocFactory.return_value, 'VirtualMachine/unit-vm',
    ))
    MUT.Path.assert_not_called()
    MUT.urlsplit.assert_not_called()
    MUT.Secret.Create.assert_not_called()
    MUT.YAMLcfg.Render.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    MUT.TemporaryDirectory.assert_not_called()
    ctx.cmdExec.assert_not_called()
    access.read_text.assert_not_called()
    secret.read_text.assert_not_called()
    public.open.assert_not_called()
    assert (pars.ownResTrk.VMs == [])


@pytest.mark.parametrize('section', [
    'gstCfg', 'imgSrc', 'CDI', 'credInfo', 'usrCrd',
])
def test_VMcreate_PlainNestedOverrideFailsBeforeEffects(
    vmTestContext: SimpleNamespace,
    section: str,
) -> None:
    ctx = vmTestContext
    path = (
        ('gstCfg', 'usrCrd') if (section == 'usrCrd') else
        ('gstCfg', 'imgSrc', 'CDI', 'credInfo')
    )
    nested = {}
    for name in reversed(path[:(path.index(section) + 1)]):
        nested = {name: nested}
    overrides = {'fxtInf': nested}
    cursor = overrides['fxtInf']
    for name in path[:(path.index(section) + 1)]:
        cursor = cursor[name]
    assert (cursor == {})
    failure = TypeError('expected `NamedDict`, received `dict`')
    ctx.namedDict.side_effect = failure
    base = ctx.config()
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )
    rootPath = ctx.addPath('/synthetic/repo')

    with pytest.raises(
        TypeError, match='expected `NamedDict`, received `dict`',
    ):
        InvokeVMSection(ctx, Request(rootPath), pars, **overrides)

    assert (ctx.namedDict.call_args == mock.call(overrides))
    base._merge.assert_not_called()
    ctx.ocFactory.assert_not_called()
    MUT.Namespace.Create.assert_not_called()
    MUT.Secret.Create.assert_not_called()
    MUT.YAMLcfg.Render.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    MUT.Path.assert_not_called()
    assert (pars.vmList == pars.ownResTrk.VMs == [])


def test_VMcreate_ResolvedConfigurationAndCompletePairOverridesAreLocal(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    base = ctx.config()
    inherited = ctx.config(namePfx='inherited', ctxData={'custom': 'retained'})
    alternatePrivate = '/synthetic/alternate-private'
    alternatePublic = '/synthetic/alternate-public'
    afterArgs = ctx.config(
        private=alternatePrivate, public=alternatePublic,
        namePfx='override', ctxData={'custom': 'retained'},
    )
    resolved = ctx.config(
        private=alternatePrivate, public=alternatePublic,
        namePfx='override', ctxData={'custom': 'retained'},
    )
    afterArgs.fxtInf.gstCfg.usrCrd.sshUsr = 'alternate-user'
    resolved.fxtInf.gstCfg.usrCrd.sshUsr = 'alternate-user'
    (inheritedRequest, _, inheritedTemplate) = PrepareVMSection(
        ctx, base, inherited,
    )
    (request, _, overrideTemplate) = PrepareVMSection(ctx, afterArgs, resolved)
    override = {'fxtInf': {
        'vmCfg': {'count': 1, 'namePfx': 'override'},
        'gstCfg': {'usrCrd': {
            'sshUsr': 'alternate-user',
            'sshKey': {'prv': alternatePrivate, 'pub': alternatePublic},
        }},
    }}
    overrideBefore = json.dumps(override)
    baseBefore = (
        base.fxtInf.gstCfg.usrCrd.sshKey.prv,
        base.fxtInf.gstCfg.usrCrd.sshKey.pub,
        dict(base.cliCfg.ocCfg), dict(base.cliCfg.vcCfg),
    )
    base._merge.side_effect = [inherited, afterArgs]
    ctx.ocFactory.GetResUID.side_effect = [
        (True, '', ''), (True, '', ''),
    ]
    MUT.YAMLcfg.Render.side_effect = [
        'canned-inherited-manifest', 'canned-override-manifest',
    ]
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    first = InvokeVMSection(ctx, inheritedRequest, pars)
    later = InvokeVMSection(ctx, request, pars, **override)

    assert (len(first) == len(later) == 1)
    assert (
        MUT.VM.call_args_list[0].kwargs['sshKey'] == '/synthetic/private-key'
    )
    assert (MUT.VM.call_args_list[1].kwargs['sshKey'] == alternatePrivate)
    assert (MUT.VM.call_args_list[1].kwargs['sshUsr'] == 'alternate-user')
    assert (ctx.paths[alternatePublic].open.call_count == 1)
    ctx.cmdExec.assert_not_called()
    MUT.TemporaryDirectory.assert_not_called()
    assert (base._merge.call_args_list == [
        mock.call(ctx.namedDictMade[0]), mock.call(ctx.namedDictMade[2]),
    ])
    assert (afterArgs._merge.call_args == mock.call(ctx.namedDictMade[7]))
    assert (json.dumps(override) == overrideBefore)
    assert ((base.fxtInf.gstCfg.usrCrd.sshKey.prv,
             base.fxtInf.gstCfg.usrCrd.sshKey.pub,
             dict(base.cliCfg.ocCfg), dict(base.cliCfg.vcCfg)) == baseBefore)

    assert (afterArgs.fxtInf.vmCfg.ctxData == {'custom': 'retained'})
    assert (base.fxtInf.vmCfg.ctxData == {})
    assert (inherited.fxtInf.vmCfg.ctxData['custom'] == 'retained')
    assert (resolved.fxtInf.vmCfg.ctxData['custom'] == 'retained')

    assert (MUT.YAMLcfg.Render.call_args_list == [
        mock.call(inheritedTemplate, {
            'custom': 'retained',
            'vmDataVolumeSource': {'http': {
                'url': 'https://synthetic.invalid/image.qcow2',
            }},
            'vmNS': 'unit-namespace',
            'sshPublicKeyB64': 'canned-base64',
            'vmName': 'inherited',
        }),
        mock.call(overrideTemplate, {
            'custom': 'retained',
            'vmDataVolumeSource': {'http': {
                'url': 'https://synthetic.invalid/image.qcow2',
            }},
            'vmNS': 'unit-namespace',
            'sshPublicKeyB64': 'canned-base64',
            'vmName': 'override',
        }),
    ])


@pytest.mark.parametrize('case', ['missing', 'empty', 'invalid-utf8'])
def test_VMcreate_InvalidPublicOverridePreservesPreparedFixtureAndRetries(
    vmTestContext: SimpleNamespace,
    case: str,
) -> None:
    ctx = vmTestContext
    base = ctx.config(credType='K8s')
    badPublic = f'/synthetic/bad-public-{case}'
    afterArgs = ctx.config(public=badPublic, credType='K8s')
    invalidResolved = ctx.config(public=badPublic, credType='K8s')
    retryBase = ctx.config()
    retryConfig = ctx.config(credType='K8s')
    (firstRequest, _, _) = PrepareVMSection(ctx, afterArgs, invalidResolved)
    (request, _, _) = PrepareVMSection(ctx, retryBase, retryConfig)
    base._merge.side_effect = [afterArgs, retryConfig]
    badPath = ctx.paths[badPublic]
    if (case == 'missing'):
        badPath.open.side_effect = OSError('canned missing public key')
    elif (case == 'empty'):
        badPath.open.return_value.__enter__.return_value.read.return_value = (
            ' \n'
        )
    else:
        badPath.open.return_value.__enter__.return_value.read.side_effect = (
            UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'canned invalid text')
        )
    overrides = {'fxtInf': {'gstCfg': {'usrCrd': {'sshKey': {
        'pub': badPublic,
    }}}}}
    overridesBefore = json.dumps(overrides)
    baseKey = (
        base.fxtInf.gstCfg.usrCrd.sshKey.prv,
        base.fxtInf.gstCfg.usrCrd.sshKey.pub,
    )
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    with pytest.raises(ValueError, match='Public Key resolution') as caught:
        InvokeVMSection(ctx, firstRequest, pars, **overrides)
    assert caught.value.__suppress_context__
    assert (badPublic not in str(caught.value))
    assert (MUT.Secret.Create.call_count == 1)
    MUT.YAMLcfg.Render.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    assert (pars.vmList == pars.ownResTrk.VMs == [])
    assert ((base.fxtInf.gstCfg.usrCrd.sshKey.prv,
             base.fxtInf.gstCfg.usrCrd.sshKey.pub) == baseKey)
    assert (json.dumps(overrides) == overridesBefore)
    ctx.cmdExec.assert_not_called()
    MUT.TemporaryDirectory.assert_not_called()

    result = InvokeVMSection(ctx, request, pars)

    assert (len(result) == 1)
    assert (pars.vmList == result)
    assert (MUT.Secret.Create.call_count == 2)
    assert (MUT.YAMLcfg.Render.call_count == 1)
    assert (MUT.VirtualMachine.Create.call_count == 1)
    ctx.cmdExec.assert_not_called()
    assert (json.dumps(overrides) == overridesBefore)


def test_VMcreate_PerCallKeyResolutionFailurePreservesFixtureAndRetries(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = vmTestContext
    base = ctx.config(count=0)
    callCfg = ctx.config(private=None, public=None, count=0)
    retryCfg = ctx.config(count=0)
    retryResolved = ctx.config(count=0)
    base._merge.side_effect = [callCfg, retryCfg, base]
    directory = mock.Mock(name='per-call-key-directory')
    failure = ValueError('canned per-call key resolution failure')

    def ResolveSSHkeys(fxtCfg: Any, sshKeyDirs: list[Any]) -> Any:
        if (fxtCfg is callCfg):
            sshKeyDirs.append(directory)
            raise failure
        return retryResolved

    resolve = mock.create_autospec(
        MUT._ResolveSSHkeys, side_effect=ResolveSSHkeys,
    )
    monkeypatch.setattr(MUT, '_ResolveSSHkeys', resolve)
    sshKeyDirs = []
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=sshKeyDirs,
    )
    request = VMRequest(ctx)
    keyArgs = {'fxtInf': {'vmCfg': {'count': 0}, 'gstCfg': {
        'usrCrd': {'sshKey': {'prv': None, 'pub': None}},
    }}}
    retryArgs = {'fxtInf': {'vmCfg': {'count': 0}, 'gstCfg': {
        'usrCrd': {'sshKey': {
            'prv': '/synthetic/retry-private',
            'pub': '/synthetic/retry-public',
        }},
    }}}
    keyArgsBefore = json.dumps(keyArgs)
    baseBefore = ConfigSnapshot(base)

    with pytest.raises(ValueError) as caught:
        MUT.VMcreate(
            request, fPars, ocCLI=ctx.ocFactory,
            virtCtlCLI=ctx.virtCtlFactory, **keyArgs,
        )

    assert (caught.value is failure)
    assert (sshKeyDirs == [directory])
    assert (fPars.vmList == fPars.ownResTrk.VMs == [])
    assert (ConfigSnapshot(base) == baseBefore)
    assert (json.dumps(keyArgs) == keyArgsBefore)
    directory.cleanup.assert_not_called()
    ctx.ocFactory.assert_not_called()
    MUT.Namespace.Create.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()

    assert (MUT.VMcreate(
        request, fPars, ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory, **retryArgs,
    ) == [])
    assert (MUT.VMcreate(
        request, fPars, ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
    ) == [])

    assert (resolve.call_count == 2)
    assert (resolve.call_args_list[0].args[0] is callCfg)
    assert (resolve.call_args_list[1].args[0] is retryCfg)
    assert all((call.args[1] is sshKeyDirs) for call in resolve.call_args_list)
    assert (ctx.namedDict.call_args_list == [
        mock.call(keyArgs), mock.call(retryArgs), mock.call({}),
    ])
    assert (sshKeyDirs == [directory])
    assert (ConfigSnapshot(base) == baseBefore)


@pytest.mark.parametrize('authFields', ['missing', None, 'invalid'])
def test_VMcreate_NoneCredentialsIgnoresResolvedCredentialInfo(
    vmTestContext: SimpleNamespace,
    authFields: Any,
) -> None:
    ctx = vmTestContext
    url = 'https://PUBLIC-IMAGE-HOST.invalid/windows.qcow2?part=one+two'
    base = ctx.config()
    effective = ctx.config(
        credType='None', url=url, ctxData={'custom': 'kept'},
    )
    source = effective.fxtInf.gstCfg.imgSrc.CDI
    if (authFields == 'missing'): delattr(source, 'credInfo')
    elif (authFields is None): source.credInfo = None
    elif (authFields == 'invalid'):
        source.credInfo = SimpleNamespace(
            name='', vaultType='Unsupported', vaultInfo={},
        )
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    access = ctx.paths['/synthetic/access']
    secret = ctx.paths['/synthetic/secret']
    access.read_text.side_effect = AssertionError('unexpected image read')
    secret.read_text.side_effect = AssertionError('unexpected image read')
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    result = InvokeVMSection(ctx, request, pars)

    assert (len(result) == 1)
    assert (MUT.urlsplit.call_args == mock.call(url))
    assert (MUT.YAMLcfg.Render.call_args.args[1]['custom'] == 'kept')
    assert (MUT.YAMLcfg.Render.call_args.args[1]['vmDataVolumeSource'] == {
        'http': {'url': url},
    })
    assert (MUT.base64.b64encode.call_args_list == [
        mock.call(b'ssh-ed25519 canned-public\n'),
    ])
    access.read_text.assert_not_called()
    secret.read_text.assert_not_called()
    MUT.Secret.Create.assert_not_called()
    assert (pars.ownResTrk.Secrets == [])


@pytest.mark.parametrize('sourceType,credType,url,field', [
    ('HTTP', 'DirectURL', 'https://example.invalid/image.qcow2', 'type'),
    ('HTTP', 'None', 'https://example.invalid/image.qcow2', 'type'),
    ('AWS-S3', 'Unsupported', 'https://example.invalid/image.qcow2',
     'credType'),
    ('AWS-S3', 'FutureMode', 'https://example.invalid/image.qcow2',
     'credType'),
    ('AWS-S3', '', 'https://example.invalid/image.qcow2', 'credType'),
    ('AWS-S3', 'none', 'https://example.invalid/image.qcow2', 'credType'),
    ('AWS-S3', 'DirectURL', 'https://example.invalid/image.qcow2',
     'credType'),
    ('AWS-S3', None, 'https://example.invalid/image.qcow2', 'credType'),
    ('AWS-S3', 'None', '', 'url'),
    ('AWS-S3', 'None', 'ftp://example.invalid/image.qcow2', 'url'),
    ('AWS-S3', 'None', 'https://[invalid-host', 'url'),
    ('AWS-S3', 'None', 'https://:443/image.qcow2', 'url'),
])
def test_VMcreate_NoneCredentialsRejectsUnsupportedConfigurationBeforeEffects(
    vmTestContext: SimpleNamespace,
    sourceType: str,
    credType: Any,
    url: str,
    field: str,
) -> None:
    ctx = vmTestContext
    base = ctx.config()
    effective = ctx.config(credType=credType, url=url)
    source = effective.fxtInf.gstCfg.imgSrc.CDI
    source.type = sourceType
    if (field == 'url'):
        if (url == 'https://[invalid-host'):
            MUT.urlsplit.side_effect = ValueError('canned parser failure')
        else:
            scheme = (
                'ftp' if url.startswith('ftp:')
                else 'https' if url.startswith('https:') else ''
            )
            hostname = (
                None if (url in ('', 'https://:443/image.qcow2'))
                else 'example.invalid'
            )
            MUT.urlsplit.return_value = SimpleNamespace(
                scheme=scheme, hostname=hostname,
            )
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    access = ctx.paths['/synthetic/access']
    secret = ctx.paths['/synthetic/secret']
    access.read_text.side_effect = AssertionError('invalid config read image')
    secret.read_text.side_effect = AssertionError('invalid config read image')
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    with pytest.raises(ValueError, match=field):
        InvokeVMSection(ctx, request, pars)

    access.read_text.assert_not_called()
    secret.read_text.assert_not_called()
    assert (MUT.Path.call_count == 0)
    MUT.Secret.Create.assert_not_called()
    MUT.YAMLcfg.Render.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    assert (pars.vmList == pars.ownResTrk.Secrets == [])
    if (field == 'url'): assert (MUT.urlsplit.call_args == mock.call(url))
    else: MUT.urlsplit.assert_not_called()
    assert (MUT.Namespace.Create.call_count == 1)
    assert (ctx.ocFactory.GetResUID.call_count == 1)


def test_VMcreate_ChangedCredentialModeAfterValidationRejectsBeforeEffects(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    url = 'https://example.invalid/image.qcow2'
    base = ctx.config()
    effective = ctx.config(credType='K8s', url=url)
    image = mock.MagicMock(name='imageSource')
    image.type = 'AWS-S3'
    image.url = url
    image.credInfo = SimpleNamespace(
        name='unit-image-secret', vaultType='LocalFile', vaultInfo={
            'file--AccKeyId': '/synthetic/access',
            'file--AccKeySecret': '/synthetic/secret',
        },
    )
    mode = mock.PropertyMock(
        side_effect=['K8s', 'K8s', 'FutureMode'],
    )
    type(image).credType = mode
    effective.fxtInf.gstCfg.imgSrc.CDI = image
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    access = ctx.paths['/synthetic/access']
    secret = ctx.paths['/synthetic/secret']
    access.read_text.side_effect = AssertionError('unexpected credential read')
    secret.read_text.side_effect = AssertionError('unexpected credential read')
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    with pytest.raises(ValueError, match='Unsupported image credential mode'):
        InvokeVMSection(ctx, request, pars)

    assert (mode.call_count == 3)
    assert (MUT.urlsplit.call_args == mock.call(url))
    access.read_text.assert_not_called()
    secret.read_text.assert_not_called()
    MUT.Secret.Create.assert_not_called()
    MUT.YAMLcfg.Render.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    assert (pars.vmList == pars.ownResTrk.Secrets == [])


@pytest.mark.parametrize('credType', ['None', 'K8s'])
@pytest.mark.parametrize('reuseFirst', [False, True])
def test_VMcreate_CredentialModesKeepContextAndResourceEffectsIsolated(
    vmTestContext: SimpleNamespace,
    credType: str,
    reuseFirst: bool,
) -> None:
    ctx = vmTestContext
    url = 'https://PUBLIC-IMAGE-HOST.invalid/disk?part=%2f&part=one+two'
    count = 3 if reuseFirst else 2
    base = ctx.config()
    effective = ctx.config(
        count=count, reuseExisting=reuseFirst, credType=credType,
        url=url, ctxData={},
    )
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    if reuseFirst:
        ctx.ocFactory.GetResUID.side_effect = [
            (True, 'existing-uid', ''), (True, '', ''), (True, '', ''),
        ]
    else:
        ctx.ocFactory.GetResUID.side_effect = [
            (True, '', ''), (True, '', ''),
        ]
    access = ctx.paths['/synthetic/access']
    secret = ctx.paths['/synthetic/secret']
    access.read_text.return_value = 'access-value'
    secret.read_text.return_value = 'secret-value'
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    result = InvokeVMSection(ctx, request, pars)

    newNames = (
        ['unit-vm-1', 'unit-vm-2'] if reuseFirst
        else ['unit-vm-0', 'unit-vm-1']
    )
    expectedSource = (
        {'http': {'url': url}} if (credType == 'None')
        else {'s3': {'url': url, 'secretRef': 'unit-image-secret'}}
    )
    assert (len(result) == count)
    assert (len(MUT.YAMLcfg.Render.call_args_list) == 2)
    assert (MUT.VirtualMachine.Create.call_count == 2)
    assert (MUT.Secret.Create.call_count == (1 if (credType == 'K8s') else 0))
    assert (access.read_text.call_count == (1 if (credType == 'K8s') else 0))
    assert (secret.read_text.call_count == (1 if (credType == 'K8s') else 0))
    assert ([call.args[1]['vmName'] for call in (
        MUT.YAMLcfg.Render.call_args_list
    )] == newNames)
    for renderCall in MUT.YAMLcfg.Render.call_args_list:
        context = renderCall.args[1]
        assert (set(context) == {
            'vmName', 'vmNS', 'vmDataVolumeSource', 'sshPublicKeyB64',
        })
        assert (context['vmDataVolumeSource'] == expectedSource)
        assert (context['vmNS'] == 'unit-namespace')
        assert (context['sshPublicKeyB64'] == 'canned-base64')
    assert (MUT.urlsplit.call_args == mock.call(url))
    if (credType == 'None'):
        assert (MUT.base64.b64encode.call_args_list == [
            mock.call(b'ssh-ed25519 canned-public\n'),
        ])
        assert (pars.ownResTrk.Secrets == [])
    else:
        assert (MUT.base64.b64encode.call_args_list == [
            mock.call(b'access-value'), mock.call(b'secret-value'),
            mock.call(b'ssh-ed25519 canned-public\n'),
        ])
        assert (vars(MUT.ResCfg.call_args_list[1].kwargs['spec']) == {
            'type': 'Opaque',
            'data': {'accessKeyId': 'canned-base64',
                     'secretKey': 'canned-base64'},
        })
    assert (MUT.OwnInf.call_count == (3 + (credType == 'K8s')))
    assert all(
        (call.kwargs['owned'] is pars.ownResTrk.VMs)
        for call in MUT.OwnInf.call_args_list[-2:]
    )
    assert (pars.ownResTrk.VMs == [])


@pytest.mark.parametrize('phase', ['create', 'apply', 'wait'])
def test_VMcreate_NoneCredentialsUsesNormalDiagnosticsAndRecordings(
    vmTestContext: SimpleNamespace,
    phase: str,
) -> None:
    ctx = vmTestContext
    url = 'https://PUBLIC-IMAGE-HOST.invalid/disk?X-Amz-Signature=PLACEHOLDER'
    base = ctx.config()
    effective = ctx.config(credType='None', url=url)
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    access = ctx.paths['/synthetic/access']
    secret = ctx.paths['/synthetic/secret']
    access.read_text.side_effect = AssertionError('unexpected image read')
    secret.read_text.side_effect = AssertionError('unexpected image read')
    failure = RuntimeError(f'canned {phase} resource failure')
    MUT.VirtualMachine.Create.side_effect = failure
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    with pytest.raises(RuntimeError) as caught:
        InvokeVMSection(ctx, request, pars)

    assert (caught.value is failure)
    assert (url not in str(caught.value))
    assert (url in repr(MUT.YAMLcfg.Render.call_args_list))
    assert (MUT.logs.mock_calls == [])
    access.read_text.assert_not_called()
    secret.read_text.assert_not_called()
    MUT.Secret.Create.assert_not_called()
    assert (MUT.YAMLcfg.Render.call_count == 1)
    assert (pars.vmList == pars.ownResTrk.Secrets == [])


def test_VMcreate_RendersFourNativeKeysBeforeEffectsAndCachesRawReads(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    base = ctx.config()
    effective = ctx.config(count=3, reuseExisting=True, credType='K8s')
    (request, _, _) = PrepareVMSection(ctx, base, effective)
    access = ctx.paths['/synthetic/access']
    secret = ctx.paths['/synthetic/secret']
    public = ctx.paths['/synthetic/public-key']
    access.read_text.return_value = '  synthetic-id:λ=1\n'
    secret.read_text.return_value = 'synthetic-secret:雪=2\n'
    MUT.base64.b64encode.return_value = b'canned-base64'
    MUT.VirtualMachine.Create.return_value = {
        'metadata': {'uid': 'canned-vm-uid'}, 'spec': {},
    }
    renderContexts = []

    def Render(path: Any, context: dict[str, Any]) -> str:
        assert (set(context) == {
            'vmName', 'vmNS', 'vmDataVolumeSource', 'sshPublicKeyB64',
        })
        assert (context['sshPublicKeyB64'] == 'canned-base64')
        renderContexts.append(dict(context))
        context['vmName'] = 'stale-render-name'
        return f'canned-render-{len(renderContexts)}'

    MUT.YAMLcfg.Render.side_effect = Render
    ordered = mock.Mock()
    ordered.attach_mock(MUT.Namespace.Create, 'namespace')
    ordered.attach_mock(MUT.Secret.Create, 'secret')
    ordered.attach_mock(MUT.YAMLcfg.Render, 'render')
    ordered.attach_mock(MUT.VirtualMachine.Create, 'vm')
    ctx.ocFactory.GetResUID.side_effect = [
        (True, 'existing-uid', ''), (True, '', ''), (True, '', ''),
        (True, 'existing-uid', ''), (True, 'existing-uid', ''),
        (True, 'existing-uid', ''),
        (True, 'existing-uid', ''), (True, '', ''), (True, '', ''),
    ]
    pars = MUT.FuncParsVMcreate(
        fxtCfgObj=base,
        vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )

    first = InvokeVMSection(ctx, request, pars)
    assert (len(first) == 3)
    assert (access.read_text.call_count == secret.read_text.call_count == 1)
    assert (len(renderContexts) == 2)
    assert ([context['vmName'] for context in renderContexts] == [
        'unit-vm-1', 'unit-vm-2',
    ])
    assert ([call[0] for call in ordered.mock_calls] == [
        'namespace', 'secret', 'render', 'vm', 'render', 'vm',
    ])
    assert (MUT.base64.b64encode.call_args_list == [
        mock.call('synthetic-id:λ=1'.encode()),
        mock.call('synthetic-secret:雪=2'.encode()),
        mock.call('ssh-ed25519 canned-public\n'.encode()),
    ])
    assert (MUT.Secret.Create.call_count == 1)
    second = InvokeVMSection(ctx, request, pars)
    assert (len(second) == 3)
    assert (access.read_text.call_count == secret.read_text.call_count == 1)
    assert (len(renderContexts) == 2)
    assert ([call[0] for call in ordered.mock_calls] == [
        'namespace', 'secret', 'render', 'vm', 'render', 'vm', 'namespace',
    ])
    third = InvokeVMSection(ctx, request, pars)
    assert (len(third) == 3)
    assert (access.read_text.call_count == secret.read_text.call_count == 2)
    assert (len(renderContexts) == 4)
    assert ([context['vmName'] for context in renderContexts[2:]] == [
        'unit-vm-1', 'unit-vm-2',
    ])
    assert ([call[0] for call in ordered.mock_calls] == [
        'namespace', 'secret', 'render', 'vm', 'render', 'vm', 'namespace',
        'namespace', 'secret', 'render', 'vm', 'render', 'vm',
    ])
    assert (MUT.Secret.Create.call_count == 2)
    assert (MUT.VirtualMachine.Create.call_count == 4)
    assert (MUT.base64.b64encode.call_args_list == [
        mock.call('synthetic-id:λ=1'.encode()),
        mock.call('synthetic-secret:雪=2'.encode()),
        mock.call('ssh-ed25519 canned-public\n'.encode()),
        mock.call('synthetic-id:λ=1'.encode()),
        mock.call('synthetic-secret:雪=2'.encode()),
        mock.call('ssh-ed25519 canned-public\n'.encode()),
    ])
    assert (public.open.call_count == 2)
    assert ('vmName' not in effective.fxtInf.vmCfg.ctxData)
    assert (base.fxtInf.vmCfg.ctxData == {})
    assert (MUT._DEF__FXT_CFG.fxtInf.vmCfg.ctxData == {})
    retained = repr((
        MUT.logs.mock_calls, MUT.ResCfg.call_args_list,
        MUT.YAMLcfg.Render.call_args_list,
        MUT.VirtualMachine.Create.call_args_list,
    ))
    assert ('synthetic-id:λ=1' not in retained)
    assert ('synthetic-secret:雪=2' not in retained)


@pytest.mark.parametrize('ocConfig,vcConfig', [
    ({}, {}),
    ({'kubeconfig': 'oc-explicit'}, {'kubeconfig': 'vc-explicit'}),
])
def test_VMcreate_ClientKubeconfig_ReviewRegression(
    vmTestContext: SimpleNamespace,
    ocConfig: dict,
    vcConfig: dict,
) -> None:
    ctx = vmTestContext
    base = ctx.config()
    effective = ctx.config(reuseExisting=True)
    effective.kCfg = 'fixture-config'
    effective.cliCfg.ocCfg = dict(ocConfig)
    effective.cliCfg.vcCfg = dict(vcConfig)
    VMUserCredentials(effective)
    base._merge.return_value = effective
    ctx.ocFactory.GetResUID.return_value = (True, 'reuse-uid', '')
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    result = MUT.VMcreate(
        VMRequest(ctx), pars,
        ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
    )
    ctx.ocFactory.assert_called_once_with(
        ns='unit-namespace', kubeconfig='fixture-config',
    )
    ctx.virtCtlFactory.assert_called_once_with(
        ns='unit-namespace', kubeconfig='fixture-config',
    )
    assert (result == [ctx.vmFactory.return_value])
    assert (effective.cliCfg.ocCfg == ocConfig)
    assert (effective.cliCfg.vcCfg == vcConfig)
    MUT.YAMLcfg.Render.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()


def test_VMcreate_ResourceHandlersReceiveNamedDictAndSharedOwnership(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    base = ctx.config()
    effective = ctx.config(credType='K8s')
    VMUserCredentials(effective)
    base._merge.return_value = effective
    ctx.addPath('/synthetic/access').read_text.return_value = ' access \n'
    ctx.addPath('/synthetic/secret').read_text.return_value = ' secret \n'
    public = ctx.addPath('/synthetic/public-key')
    stream = public.open.return_value.__enter__.return_value
    stream.read.return_value = 'public\r\n'
    MUT.base64.b64encode.side_effect = [
        b'access-b64', b'secret-b64', b'public-b64',
    ]
    MUT.ResCfg.side_effect = [
        mock.sentinel.nsCfg, mock.sentinel.secretCfg, mock.sentinel.vmCfg,
    ]
    MUT.OwnInf.side_effect = [
        mock.sentinel.nsInfo, mock.sentinel.secretInfo, mock.sentinel.vmInfo,
    ]
    MUT.YAMLcfg.Render.return_value = 'canned-manifest'
    ordered = mock.Mock()
    ordered.attach_mock(MUT.Namespace.Create, 'namespace')
    ordered.attach_mock(MUT.Secret.Create, 'secret')
    ordered.attach_mock(MUT.YAMLcfg.Render, 'render')
    ordered.attach_mock(MUT.VirtualMachine.Create, 'vm')
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    MUT.VMcreate(
        VMRequest(ctx), pars,
        ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
    )
    client = ctx.ocFactory.return_value
    assert ([call[0] for call in ordered.mock_calls] == [
        'namespace', 'secret', 'render', 'vm',
    ])
    MUT.Namespace.Create.assert_called_once_with(
        client, mock.sentinel.nsCfg, mock.sentinel.nsInfo,
    )
    MUT.Secret.Create.assert_called_once_with(
        client, mock.sentinel.secretCfg, mock.sentinel.secretInfo,
    )
    MUT.VirtualMachine.Create.assert_called_once_with(
        client, mock.sentinel.vmCfg, mock.sentinel.vmInfo,
        optsNewRes={'waitTime': '15m'},
    )
    assert (MUT.OwnInf.call_args_list == [
        mock.call(owned=pars.ownResTrk.NSs),
        mock.call(owned=pars.ownResTrk.Secrets),
        mock.call(owned=pars.ownResTrk.VMs),
    ])
    namespaceMeta = MUT.ResCfg.call_args_list[0].kwargs['meta']
    secretCall = MUT.ResCfg.call_args_list[1]
    secretMeta = secretCall.kwargs['meta']
    secretSpec = secretCall.kwargs['spec']
    assert (ctx.namedDict.call_args_list == [
        mock.call(NSs=[], Secrets=[], VMs=[]),
        mock.call({}),
        mock.call(name='unit-namespace'),
        mock.call(namespace='unit-namespace', name='unit-image-secret'),
        mock.call(type='Opaque', data={
            'accessKeyId': 'access-b64', 'secretKey': 'secret-b64',
        }),
    ])
    assert (vars(namespaceMeta) == {'name': 'unit-namespace'})
    assert (vars(secretMeta) == {
        'namespace': 'unit-namespace', 'name': 'unit-image-secret',
    })
    assert (vars(secretSpec) == {
        'type': 'Opaque',
        'data': {'accessKeyId': 'access-b64', 'secretKey': 'secret-b64'},
    })
    assert (MUT.base64.b64encode.call_args_list == [
        mock.call(b'access'), mock.call(b'secret'), mock.call(b'public\r\n'),
    ])
    public.open.assert_called_once_with(encoding='utf-8', newline='')
    assert (pars.ownResTrk.NSs == pars.ownResTrk.Secrets == [])
    assert (pars.ownResTrk.VMs == [])


@pytest.mark.parametrize('value', ['', ' '])
def test_VMcreate_InvalidNamespaceFailsBeforeResourceEffects(
    vmTestContext: SimpleNamespace,
    value: str,
) -> None:
    ctx = vmTestContext
    base = ctx.config()
    effective = ctx.config()
    effective.ns = value
    base._merge.return_value = effective
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    with pytest.raises(ValueError, match='Namespace must not be empty'):
        MUT.VMcreate(
            Request(mock.sentinel.root), pars,
            ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
        )
    ctx.ocFactory.assert_not_called()
    ctx.virtCtlFactory.assert_not_called()
    MUT.Namespace.Create.assert_not_called()
    MUT.Secret.Create.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    MUT.YAMLcfg.Render.assert_not_called()
    MUT.Path.assert_not_called()
    assert (pars.vmList == pars.ownResTrk.VMs == [])


def test_VMcreate_MixedReuseAndMultipleNewVMsRenderAndOwnOnlyNew(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = vmTestContext
    base = ctx.config()
    effective = ctx.config(
        count=3, reuseExisting=True, ctxData={'custom': 'retained'},
    )
    VMUserCredentials(effective)
    base._merge.return_value = effective
    public = ctx.addPath('/synthetic/public-key')
    stream = public.open.return_value.__enter__.return_value
    stream.read.return_value = 'ssh-ed25519 canned-public\n'
    ctx.ocFactory.GetResUID.side_effect = [
        (True, 'existing-uid', ''), (True, '', ''), (True, '', ''),
    ]
    wrappers = [mock.sentinel.first, mock.sentinel.second, mock.sentinel.third]
    ctx.vmFactory.side_effect = wrappers
    MUT.YAMLcfg.Render.side_effect = ['manifest-one', 'manifest-two']
    pars = MUT.FuncParsVMcreate(fxtCfgObj=base)
    request = VMRequest(ctx)
    template = ctx.paths['/synthetic/repo/conf/VM/cfg-template--vm.yaml']
    result = MUT.VMcreate(
        request, pars, ocCLI=ctx.ocFactory, virtCtlCLI=ctx.virtCtlFactory,
    )
    assert (result == pars.vmList == wrappers)
    assert ([call.kwargs['name'] for call in ctx.vmFactory.call_args_list] == [
        'unit-vm-0', 'unit-vm-1', 'unit-vm-2',
    ])
    assert (MUT.YAMLcfg.Render.call_args_list == [
        mock.call(template, {
            'custom': 'retained',
            'vmDataVolumeSource': {'http': {
                'url': 'https://synthetic.invalid/image.qcow2',
            }},
            'vmNS': 'unit-namespace',
            'sshPublicKeyB64': 'canned-base64',
            'vmName': vmName,
        })
        for vmName in ('unit-vm-1', 'unit-vm-2')
    ])
    assert (MUT.VirtualMachine.Create.call_count == 2)
    assert (MUT.ResCfg.call_args_list[-2:] == [
        mock.call(yaml='manifest-one'), mock.call(yaml='manifest-two'),
    ])
    assert (MUT.OwnInf.call_args_list == [
        mock.call(owned=pars.ownResTrk.NSs),
        mock.call(owned=pars.ownResTrk.VMs),
        mock.call(owned=pars.ownResTrk.VMs),
    ])
    assert (pars.ownResTrk.VMs == [])
    MUT.Secret.Create.assert_not_called()


def PrepareFxtVM(
    ctx: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    initialConfig: Any,
    param: dict[str, Any] | None = None,
) -> SimpleNamespace:
    '''Prepare only explicit VM fixture inputs and MUT-bound collaborators.'''
    rootConfig = ctx.config()
    rootConfig._merge.return_value = initialConfig
    monkeypatch.setattr(MUT, '_DEF__FXT_CFG', rootConfig)
    def ResolveSSHkeys(config: Any, sshKeyDirs: list[Any]) -> Any:
        return config

    resolver = mock.create_autospec(
        MUT._ResolveSSHkeys, side_effect=ResolveSSHkeys,
    )
    monkeypatch.setattr(MUT, '_ResolveSSHkeys', resolver)
    RegisterVMSSHPaths(ctx, initialConfig)
    publicPath = ctx.paths[initialConfig.fxtInf.gstCfg.usrCrd.sshKey.pub]
    publicStream = mock.MagicMock()
    publicStream.read.return_value = 'ssh-ed25519 synthetic-public\n'
    publicPath.open.return_value.__enter__.return_value = publicStream

    request = VMRequest(ctx)
    request.param = {} if (param is None) else param
    excInfo = mock.Mock(return_value=(None, None, None))
    monkeypatch.setattr(MUT.sys, 'exc_info', excInfo)
    vmCreate = mock.create_autospec(MUT.VMcreate, return_value=[])
    monkeypatch.setattr(MUT, 'VMcreate', vmCreate)
    return SimpleNamespace(
        rootConfig=rootConfig,
        initialConfig=initialConfig,
        request=request,
        resolver=resolver,
        excInfo=excInfo,
        vmCreate=vmCreate,
    )


@pytest.mark.parametrize('case,retained', [
    ('empty', False),
    ('whitespace', False),
    ('present', True),
    ('failed', True),
    ('exception', True),
])
def test__RetainNS_RequiresConfirmedVMabsence(
    vmTestContext: SimpleNamespace,
    case: str,
    retained: bool,
) -> None:
    client = mock.Mock()
    client.Run.return_value = CommandResult(
        (case != 'failed'),
        {'whitespace': ' \n', 'present': 'replacement-uid'}.get(case, ''),
    )
    if (case == 'exception'):
        client.Run.side_effect = RuntimeError('synthetic lookup failure')

    assert (MUT._RetainNS(client, 'Namespace/owned-ns') is retained)

    client.Run.assert_called_once_with(
        'get',
        'VirtualMachines',
        n='owned-ns',
        namespace=None,
        o='jsonpath={.items[*].metadata.uid}',
    )
    client.DelRes.assert_not_called()
    if retained:
        expectedWarning = (
            'Retaining `%s` because VMs remain.' if (case == 'present')
            else 'Retaining `%s` because VM lookup failed.'
        )
        MUT.logs.warning.assert_called_once_with(
            expectedWarning,
            'Namespace/owned-ns',

        )
    else: MUT.logs.warning.assert_not_called()
    if (case == 'exception'):
        MUT.logs.exception.assert_called_once_with(
            'Could not check VMs in `%s`.', 'Namespace/owned-ns',
        )
    else: MUT.logs.exception.assert_not_called()


@pytest.mark.parametrize('namespaceOpts', [
    {'n': 'old-short'},
    {'namespace': 'old-long'},
    {'n': 'old-short', 'namespace': 'old-long'},
])
def test__RetainNS_OverridesAliasesWithoutChangingClient(
    vmTestContext: SimpleNamespace,
    namespaceOpts: dict[str, str],
) -> None:
    options = namespaceOpts | {
        'kubeconfig': '/selected/config',
        'context': 'selected-context',
    }
    before = options.copy()
    client = mock.Mock()
    client.defOpts = options
    client.Run.return_value = CommandResult(True)

    assert not MUT._RetainNS(client, 'Namespace/owned-ns')

    assert (client.defOpts is options)
    assert (options == before)
    client.Run.assert_called_once_with(
        'get',
        'VirtualMachines',
        n='owned-ns',
        namespace=None,
        o='jsonpath={.items[*].metadata.uid}',
    )
    client.DelRes.assert_not_called()


def test_FxtVM_InitialNegativeFailsBeforeYieldWithoutCleanup(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = vmTestContext
    initialConfig = ctx.config(count=-1)
    harness = PrepareFxtVM(ctx, monkeypatch, initialConfig)
    error = ValueError('VM count must not be negative.')
    harness.vmCreate.side_effect = error
    generator = MUT.FxtVM.__wrapped__(
        harness.request, ctx.ocFactory, ctx.virtCtlFactory,
    )

    with pytest.raises(ValueError) as caught:
        next(generator)

    assert (caught.value is error)
    fPars = harness.vmCreate.call_args_list[0].args[1]
    assert isinstance(fPars, MUT.FuncParsVMcreate)
    harness.vmCreate.assert_called_once_with(
        harness.request, fPars, ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory,
    )
    assert (fPars.fxtCfgObj is initialConfig)
    harness.resolver.assert_called_once_with(initialConfig, fPars.sshKeyDirs)
    assert (fPars.vmList == fPars.ownResTrk.VMs == [])
    MUT.VM.CleanUpVMs.assert_not_called()
    ctx.ocFactory.assert_not_called()


def test_FxtVM_InitialZeroYieldsFrozenEmptyBundleWithoutCleanup(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = vmTestContext
    initialConfig = ctx.config(count=0)
    initialConfig._merge.return_value = initialConfig
    harness = PrepareFxtVM(ctx, monkeypatch, initialConfig)
    generator = MUT.FxtVM.__wrapped__(
        harness.request, ctx.ocFactory, ctx.virtCtlFactory,
    )

    bundle = next(generator)
    fPars = harness.vmCreate.call_args_list[0].args[1]
    assert isinstance(bundle, MUT.FxtObjVM)
    assert isinstance(fPars, MUT.FuncParsVMcreate)
    assert (bundle.vmList is fPars.vmList)
    assert (bundle.vmList == [])
    with pytest.raises(FrozenInstanceError):
        bundle.vmList = []
    bundle.vmList.append(mock.sentinel.sharedVM)
    assert (fPars.vmList == [mock.sentinel.sharedVM])
    bundle.vmList.pop()
    assert (fPars.vmList == [])

    exitError = GeneratorExit()
    harness.excInfo.return_value = (GeneratorExit, exitError, None)
    generator.close()

    harness.excInfo.assert_called_once_with()
    MUT.VM.assert_not_called()
    MUT.VM.CleanUpVMs.assert_not_called()
    assert (ctx.ocFactory.CleanUpRes.call_args_list == [
        mock.call([]), mock.call([]),
    ])


def test_FxtVM_InitialSuccessUsesSelectedConfigAndCleansOnClose(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = vmTestContext
    initialConfig = ctx.config(
        count=1, startVM=False, namePfx='initial', ctxData={},
    )
    initialConfig.ns = 'initial-ns'
    initialConfig.fxtInf.vmCfg.timeout.vmRemove = '11m'
    requestParam = {'ns': 'initial-ns'}
    harness = PrepareFxtVM(ctx, monkeypatch, initialConfig, requestParam)
    wrapper = mock.Mock(name='initialVM')
    record = (mock.sentinel.initialOC, 'VirtualMachine/initial', 'uid')
    created = [wrapper]

    def Create(request: Any, fPars: Any, **kwargs: Any) -> list[Any]:
        fPars.vmList.append(wrapper)
        fPars.ownResTrk.VMs.append(record)
        return created

    harness.vmCreate.side_effect = Create
    generator = MUT.FxtVM.__wrapped__(
        harness.request, ctx.ocFactory, ctx.virtCtlFactory,
    )

    bundle = next(generator)
    fPars = harness.vmCreate.call_args_list[0].args[1]
    assert (harness.request.param is requestParam)
    assert (harness.request.param == {'ns': 'initial-ns'})
    assert (bundle.vmList is fPars.vmList)
    assert (bundle.vmList == [wrapper])
    assert (fPars.ownResTrk.VMs == [record])
    harness.vmCreate.assert_called_once_with(
        harness.request, fPars, ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory,
    )
    assert (ctx.namedDict.call_args_list == [
        mock.call(ns='initial-ns'),
        mock.call(NSs=[], Secrets=[], VMs=[record]),
    ])
    assert (harness.rootConfig._merge.call_args.args[0]
            is ctx.namedDictMade[0])
    assert (fPars.ownResTrk is ctx.namedDictMade[1])
    assert (fPars.fxtCfgObj is initialConfig)
    harness.resolver.assert_called_once_with(initialConfig, fPars.sshKeyDirs)
    ctx.ocFactory.assert_not_called()
    ctx.virtCtlFactory.assert_not_called()

    ordered = mock.Mock()
    ordered.attach_mock(MUT.VM.CleanUpVMs, 'vms')
    ordered.attach_mock(ctx.ocFactory.CleanUpRes, 'resources')
    exitError = GeneratorExit()
    harness.excInfo.return_value = (GeneratorExit, exitError, None)
    generator.close()

    assert (harness.request.param is requestParam)
    assert (harness.request.param == {'ns': 'initial-ns'})
    assert (MUT.VM.CleanUpVMs.call_count == 1)
    MUT.VM.CleanUpVMs.assert_called_once_with(
        bundle.vmList,
        fPars.ownResTrk.VMs,
        optsCleanUpRes={'optsDelRes': {'waitTime': '11m'}},
    )
    assert (ctx.ocFactory.CleanUpRes.call_args_list == [
        mock.call([]), mock.call([]),
    ])
    assert ([call[0] for call in ordered.mock_calls] == [
        'vms', 'resources', 'resources',
    ])
    harness.excInfo.assert_called_once_with()


def test_FxtVM_PerCallReturnsAreAggregatedAcrossBatches(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = vmTestContext
    initialConfig = ctx.config(count=0, namePfx='initial')
    harness = PrepareFxtVM(ctx, monkeypatch, initialConfig)
    vmOne = mock.Mock(name='vmOne')
    vmTwo = mock.Mock(name='vmTwo')
    vmThree = mock.Mock(name='vmThree')
    client = mock.Mock(name='oc-client')
    firstOwned = (client, 'VirtualMachine/first', 'first-uid')
    secondOwned = (client, 'VirtualMachine/second-0', 'second-uid')
    thirdOwned = (client, 'VirtualMachine/second-1', 'third-uid')
    batches = [[], [vmOne], [vmTwo, vmThree]]
    ownershipBatches = [[], [firstOwned], [secondOwned, thirdOwned]]
    targetCalls = []

    def Create(
        request: Any,
        fPars: MUT.FuncParsVMcreate,
        *,
        ocCLI: Any,
        virtCtlCLI: Any,
        **overrides: Any,
    ) -> list[Any]:
        index = len(targetCalls)
        targetCalls.append((request, fPars, overrides))
        fPars.vmList.extend(batches[index])
        fPars.ownResTrk.VMs.extend(ownershipBatches[index])
        return batches[index]

    target = harness.vmCreate
    target.side_effect = Create
    generator = MUT.FxtVM.__wrapped__(
        harness.request, ctx.ocFactory, ctx.virtCtlFactory,
    )
    bundle = next(generator)
    fPars = target.call_args_list[0].args[1]

    firstBatch = bundle.vmCreate(fxtInf={
        'vmCfg': {'count': 1, 'timeout': {'vmRemove': '71s'}},
    })
    secondBatch = bundle.vmCreate(fxtInf={
        'vmCfg': {'count': 2, 'namePfx': 'second'},
    })

    assert (firstBatch == [vmOne])
    assert (secondBatch == [vmTwo, vmThree])
    assert (firstBatch is not bundle.vmList)
    assert (secondBatch is not bundle.vmList)
    assert (bundle.vmList is fPars.vmList)
    assert (bundle.vmList == [vmOne, vmTwo, vmThree])
    assert (fPars.ownResTrk.VMs == [firstOwned, secondOwned, thirdOwned])
    assert (target.call_args_list[0] == mock.call(
        harness.request, fPars, ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory,
    ))
    assert all((call[0] is harness.request) for call in targetCalls)
    assert all((call[1] is fPars) for call in targetCalls)
    assert (targetCalls[1][2] == {'fxtInf': {
        'vmCfg': {'count': 1, 'timeout': {'vmRemove': '71s'}},
    }})

    exitError = GeneratorExit()
    harness.excInfo.return_value = (GeneratorExit, exitError, None)
    generator.close()

    MUT.VM.CleanUpVMs.assert_called_once_with(
        bundle.vmList,
        fPars.ownResTrk.VMs,
        optsCleanUpRes={'optsDelRes': {'waitTime': '3m'}},
    )


@pytest.mark.parametrize('vmRemove', [None, '420s'])
@pytest.mark.parametrize('phase', ['normal', 'setup', 'later', 'body'])
@pytest.mark.parametrize('cleanupFails', [False, True])
def test_FxtVM_RemovalTimeoutUsesInitialConfigAcrossFailurePaths(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    vmRemove: str | None,
    phase: str,
    cleanupFails: bool,
) -> None:
    ctx = vmTestContext
    initialTimeout = (vmRemove or '3m')
    initialConfig = ctx.config(count=0)
    initialConfig.fxtInf.vmCfg.timeout.vmRemove = initialTimeout
    harness = PrepareFxtVM(ctx, monkeypatch, initialConfig)
    primary = RuntimeError('primary fixture failure')
    secondary = RuntimeError('cleanup fixture failure')
    wrapper = mock.Mock(name='vm-wrapper')
    client = mock.Mock(name='oc-client')
    owned = (client, 'VirtualMachine/owned', 'owned-uid')
    targetCalls = []

    def Create(
        request: Any,
        fPars: MUT.FuncParsVMcreate,
        *,
        ocCLI: Any,
        virtCtlCLI: Any,
        **overrides: Any,
    ) -> list[Any]:
        targetCalls.append((request, fPars, overrides))
        if (len(targetCalls) == 1):
            assert (fPars.fxtCfgObj is initialConfig)
            fPars.vmList.append(wrapper)
            fPars.ownResTrk.VMs.append(owned)
            if (phase == 'setup'): raise primary
            return [wrapper]
        assert (phase == 'later')
        raise primary

    target = harness.vmCreate
    target.side_effect = Create
    if cleanupFails: MUT.VM.CleanUpVMs.side_effect = secondary
    generator = MUT.FxtVM.__wrapped__(
        harness.request, ctx.ocFactory, ctx.virtCtlFactory,
    )

    if (phase == 'setup'):
        harness.excInfo.return_value = (RuntimeError, primary, None)
        with pytest.raises(RuntimeError) as caught:
            next(generator)
        assert (caught.value is primary)
    else:
        harness.excInfo.return_value = (None, None, None)
        bundle = next(generator)
        fPars = target.call_args_list[0].args[1]
        assert (bundle.vmList is fPars.vmList)
        if (phase == 'later'):
            override = {'fxtInf': {'vmCfg': {'timeout': {'vmRemove': '1s'}}}}
            with pytest.raises(RuntimeError) as caught:
                bundle.vmCreate(**override)
            assert (caught.value is primary)
            assert (targetCalls[1][2] == override)
            assert (initialConfig.fxtInf.vmCfg.timeout.vmRemove
                    == initialTimeout)
        if (phase == 'body'):
            harness.excInfo.return_value = (RuntimeError, primary, None)
            with pytest.raises(RuntimeError) as caught:
                generator.throw(primary)
            assert (caught.value is primary)
        elif cleanupFails:
            with pytest.raises(RuntimeError) as caught:
                next(generator)
            assert (caught.value is secondary)
        else:
            with pytest.raises(StopIteration):
                next(generator)

    assert (target.call_count == (2 if (phase == 'later') else 1))
    MUT.VM.CleanUpVMs.assert_called_once_with(
        [wrapper],
        [owned],
        optsCleanUpRes={'optsDelRes': {'waitTime': initialTimeout}},
    )


def test_FxtVM_VMcreateAllowsCLIOverrides(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = vmTestContext
    initialConfig = ctx.config(count=0, namePfx='initial')
    harness = PrepareFxtVM(ctx, monkeypatch, initialConfig)

    defaultOC = mock.Mock(name='defaultOC')
    defaultVirtCtl = mock.Mock(name='defaultVirtCtl')
    selectedOC = mock.Mock(name='selectedOC')
    selectedVirtCtl = mock.Mock(name='selectedVirtCtl')
    wrapper = mock.Mock(name='reused-wrapper')
    selectedResult = [wrapper]

    def Create(request: Any, fPars: Any, **kwargs: Any) -> list[Any]:
        if (kwargs['ocCLI'] is defaultOC): return []
        fPars.vmList.append(wrapper)
        return selectedResult

    harness.vmCreate.side_effect = Create
    generator = MUT.FxtVM.__wrapped__(
        harness.request, defaultOC, defaultVirtCtl,
    )
    bundle = next(generator)
    cliCfg = SimpleNamespace(
        ocCfg={'context': 'selected-context'},
        vcCfg={'kubeconfig': '/selected-config'},
    )

    created = bundle.vmCreate(
        ocCLI=selectedOC,
        virtCtlCLI=selectedVirtCtl,
        ns='selected-ns',
        cliCfg=cliCfg,
        fxtInf={'vmCfg': {'count': 1}},
    )

    fPars = harness.vmCreate.call_args_list[0].args[1]
    assert (created is selectedResult)
    assert (bundle.vmList is fPars.vmList)
    assert (bundle.vmList == [wrapper])
    assert (harness.vmCreate.call_args_list == [
        mock.call(
            harness.request, fPars, ocCLI=defaultOC, virtCtlCLI=defaultVirtCtl,
        ),
        mock.call(
            harness.request, fPars, ocCLI=selectedOC,
            virtCtlCLI=selectedVirtCtl, ns='selected-ns', cliCfg=cliCfg,
            fxtInf={'vmCfg': {'count': 1}},
        ),
    ])
    harness.resolver.assert_called_once_with(initialConfig, fPars.sshKeyDirs)
    defaultOC.assert_not_called()
    defaultVirtCtl.assert_not_called()
    selectedOC.assert_not_called()
    selectedVirtCtl.assert_not_called()
    assert (fPars.ownResTrk.VMs == [])

    exitError = GeneratorExit()
    harness.excInfo.return_value = (GeneratorExit, exitError, None)
    generator.close()
    MUT.VM.CleanUpVMs.assert_called_once_with(
        bundle.vmList,
        fPars.ownResTrk.VMs,
        optsCleanUpRes={'optsDelRes': {'waitTime': '3m'}},
    )


@pytest.mark.parametrize('argument', ['request', 'fPars'])
def test_FxtVM_VMcreateRejectsInternalKeywordReplacementBeforeOperations(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    argument: str,
) -> None:
    ctx = vmTestContext
    initialConfig = ctx.config(count=0)
    harness = PrepareFxtVM(ctx, monkeypatch, initialConfig)
    generator = MUT.FxtVM.__wrapped__(
        harness.request, ctx.ocFactory, ctx.virtCtlFactory,
    )
    bundle = next(generator)
    fPars = harness.vmCreate.call_args_list[0].args[1]
    vmList = fPars.vmList
    ownedVMs = fPars.ownResTrk.VMs

    with pytest.raises(TypeError, match='multiple values'):
        bundle.vmCreate(**{argument: object()})

    harness.vmCreate.assert_called_once_with(
        harness.request, fPars, ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory,
    )
    assert (bundle.vmList is vmList is fPars.vmList)
    assert (fPars.ownResTrk.VMs is ownedVMs)
    assert (vmList == ownedVMs == [])
    ctx.ocFactory.assert_not_called()
    ctx.virtCtlFactory.assert_not_called()
    MUT.VM.CleanUpVMs.assert_not_called()

    exitError = GeneratorExit()
    harness.excInfo.return_value = (GeneratorExit, exitError, None)
    generator.close()


def test_FxtVM_PreyieldPartialFailureCleansCreatedVMs(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = vmTestContext
    initialConfig = ctx.config(count=0, namePfx='preyield')
    harness = PrepareFxtVM(ctx, monkeypatch, initialConfig)
    primary = RuntimeError('pre-yield VM batch failure')
    wrapper = mock.Mock(name='preyieldVM')
    client = mock.Mock(name='preyieldOC')
    owned = (client, 'VirtualMachine/preyield-0', 'uid-zero')

    def FailCreate(
        request: Any,
        fPars: MUT.FuncParsVMcreate,
        *,
        ocCLI: Any,
        virtCtlCLI: Any,
    ) -> list[Any]:
        fPars.vmList.append(wrapper)
        fPars.ownResTrk.VMs.append(owned)
        raise primary

    target = harness.vmCreate
    target.side_effect = FailCreate
    harness.excInfo.return_value = (RuntimeError, primary, None)
    generator = MUT.FxtVM.__wrapped__(
        harness.request, ctx.ocFactory, ctx.virtCtlFactory,
    )

    with pytest.raises(RuntimeError) as caught:
        next(generator)

    assert (caught.value is primary)
    fPars = target.call_args_list[0].args[1]
    assert (fPars.vmList == [wrapper])
    assert (fPars.ownResTrk.VMs == [owned])
    target.assert_called_once_with(
        harness.request,
        fPars,
        ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory,
    )
    MUT.VM.CleanUpVMs.assert_called_once_with(
        [wrapper], [owned],
        optsCleanUpRes={'optsDelRes': {'waitTime': '3m'}},
    )


def test_FxtVM_PostyieldPartialFailureCleansCreatedVMsOnClose(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = vmTestContext
    initialConfig = ctx.config(count=0)
    harness = PrepareFxtVM(ctx, monkeypatch, initialConfig)
    primary = RuntimeError('post-yield VM batch failure')
    wrapper = mock.Mock(name='postyieldVM')
    client = mock.Mock(name='postyieldOC')
    owned = (client, 'VirtualMachine/postyield-0', 'uid-zero')
    targetCalls = []

    def Create(
        request: Any,
        fPars: MUT.FuncParsVMcreate,
        *,
        ocCLI: Any,
        virtCtlCLI: Any,
        **overrides: Any,
    ) -> list[Any]:
        targetCalls.append((request, fPars, overrides))
        if (len(targetCalls) == 1): return []
        fPars.vmList.append(wrapper)
        fPars.ownResTrk.VMs.append(owned)
        raise primary

    target = harness.vmCreate
    target.side_effect = Create
    generator = MUT.FxtVM.__wrapped__(
        harness.request, ctx.ocFactory, ctx.virtCtlFactory,
    )
    bundle = next(generator)
    fPars = target.call_args_list[0].args[1]

    with pytest.raises(RuntimeError) as caught:
        bundle.vmCreate(fxtInf={'vmCfg': {'count': 2}})

    assert (caught.value is primary)
    assert (bundle.vmList is fPars.vmList)
    assert (bundle.vmList == [wrapper])
    assert (fPars.ownResTrk.VMs == [owned])
    assert (target.call_count == 2)
    MUT.VM.CleanUpVMs.assert_not_called()

    exitError = GeneratorExit()
    harness.excInfo.return_value = (GeneratorExit, exitError, None)
    generator.close()

    MUT.VM.CleanUpVMs.assert_called_once_with(
        [wrapper], [owned],
        optsCleanUpRes={'optsDelRes': {'waitTime': '3m'}},
    )


def test_FxtVM_PreyieldCleanupFailurePreservesOriginalExceptionIdentity(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = vmTestContext
    initialConfig = ctx.config(count=0)
    harness = PrepareFxtVM(ctx, monkeypatch, initialConfig)
    primary = RuntimeError('primary creation failure')
    secondary = RuntimeError('secondary cleanup failure')
    client = mock.Mock(name='partial-oc')
    owned = (client, 'VirtualMachine/partial', 'partial-uid')

    def FailCreate(
        request: Any,
        fPars: MUT.FuncParsVMcreate,
        *,
        ocCLI: Any,
        virtCtlCLI: Any,
    ) -> list[Any]:
        fPars.ownResTrk.VMs.append(owned)
        raise primary

    target = harness.vmCreate
    target.side_effect = FailCreate
    MUT.VM.CleanUpVMs.side_effect = secondary
    harness.excInfo.return_value = (RuntimeError, primary, None)
    generator = MUT.FxtVM.__wrapped__(
        harness.request, ctx.ocFactory, ctx.virtCtlFactory,
    )

    with pytest.raises(RuntimeError) as caught:
        next(generator)

    assert (caught.value is primary)
    MUT.VM.CleanUpVMs.assert_called_once_with(
        [], [owned],
        optsCleanUpRes={'optsDelRes': {'waitTime': '3m'}},
    )
    MUT.logs.error.assert_called_once_with(
        'Could not clean up fixture VMs: %s', secondary,
    )
    assert (ctx.ocFactory.CleanUpRes.call_args_list == [
        mock.call([]), mock.call([]),
    ])


@pytest.mark.parametrize('teardown', ['close', 'resume', 'primary'])
@pytest.mark.parametrize('case', ['empty', 'present', 'failed', 'exception'])
def test_FxtVM_RetainsOnlyNamespacesWithoutConfirmedVMAbsence(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    teardown: str,
    case: str,
) -> None:
    ctx = vmTestContext
    initialConfig = ctx.config(count=0)
    harness = PrepareFxtVM(ctx, monkeypatch, initialConfig)
    clients = [
        mock.Mock(name='retained-client'),
        mock.Mock(name='empty-client'),
    ]
    for client in clients:
        client.Run.return_value = CommandResult(True)
    clients[0].Run.return_value = CommandResult(
        (case != 'failed'),
        'remaining-vm-uid' if (case == 'present') else '',
    )
    if (case == 'exception'):
        clients[0].Run.side_effect = RuntimeError('synthetic lookup failure')
    records = [
        (clients[0], 'Namespace/retained-ns', 'first-uid'),
        (clients[1], 'Namespace/empty-ns', 'second-uid'),
    ]

    def Create(
        request: Any,
        fPars: MUT.FuncParsVMcreate,
        **kwargs: Any,
    ) -> list[Any]:
        fPars.ownResTrk.NSs.extend(records)
        return []

    target = harness.vmCreate
    target.side_effect = Create
    generator = MUT.FxtVM.__wrapped__(
        harness.request, ctx.ocFactory, ctx.virtCtlFactory,
    )
    next(generator)
    primary = RuntimeError('synthetic primary')
    if (teardown == 'close'):
        exitError = GeneratorExit()
        harness.excInfo.return_value = (GeneratorExit, exitError, None)
        generator.close()
    elif (teardown == 'resume'):
        harness.excInfo.return_value = (None, None, None)
        with pytest.raises(StopIteration):
            next(generator)
    else:
        harness.excInfo.return_value = (RuntimeError, primary, None)
        with pytest.raises(RuntimeError) as caught:
            generator.throw(primary)
        assert (caught.value is primary)

    expected = records if (case == 'empty') else records[1:]
    assert (ctx.ocFactory.CleanUpRes.call_args_list == [
        mock.call([]), mock.call(expected),
    ])
    assert not MUT.VM.CleanUpVMs.called
    for (client, resRef, ownUID) in records:
        client.Run.assert_called_once_with(
            'get',
            'VirtualMachines',
            n=resRef.split('/', 1)[1],
            namespace=None,
            o='jsonpath={.items[*].metadata.uid}',
        )
        client.DelRes.assert_not_called()


@pytest.mark.parametrize('failure', ['result', 'exception'])
@pytest.mark.parametrize('cleanupFails', [False, True])
def test_FxtVM_ImportFailureCleansOwnedResourcesAndPreservesError(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    cleanupFails: bool,
) -> None:
    ctx = vmTestContext
    initialConfig = ctx.config(count=0)
    harness = PrepareFxtVM(ctx, monkeypatch, initialConfig)
    primary = (
        RuntimeError('DataVolume `boot-0` import failed')
        if (failure == 'result')
        else OSError('synthetic import transport failure')
    )
    secondary = RuntimeError('synthetic VM cleanup failure')
    clients = [
        mock.Mock(name='import-client-0'),
        mock.Mock(name='import-client-1'),
    ]
    for client in clients:
        client.Run.return_value = CommandResult(True)
    wrappers = [mock.Mock(name='importVM0'), mock.Mock(name='importVM1')]
    vmRecords = [
        (clients[0], 'VirtualMachine/import-0', 'uid-0'),
        (clients[1], 'VirtualMachine/import-1', 'uid-1'),
    ]
    secretRecord = (clients[0], 'Secret/import-secret', 'secret-uid')
    namespaceRecord = (clients[0], 'Namespace/import-ns', 'namespace-uid')

    def FailImport(
        request: Any,
        fPars: MUT.FuncParsVMcreate,
        *,
        ocCLI: Any,
        virtCtlCLI: Any,
    ) -> list[Any]:
        fPars.vmList.extend(wrappers)
        fPars.ownResTrk.VMs.extend(vmRecords)
        fPars.ownResTrk.Secrets.append(secretRecord)
        fPars.ownResTrk.NSs.append(namespaceRecord)
        raise primary

    target = harness.vmCreate
    target.side_effect = FailImport
    if cleanupFails: MUT.VM.CleanUpVMs.side_effect = secondary
    harness.excInfo.return_value = (type(primary), primary, None)
    ordered = mock.Mock()
    ordered.attach_mock(MUT.VM.CleanUpVMs, 'vms')
    ordered.attach_mock(ctx.ocFactory.CleanUpRes, 'resources')
    generator = MUT.FxtVM.__wrapped__(
        harness.request, ctx.ocFactory, ctx.virtCtlFactory,
    )

    with pytest.raises(type(primary)) as caught:
        next(generator)

    assert (caught.value is primary)
    fPars = target.call_args_list[0].args[1]
    assert (fPars.vmList == wrappers)
    assert (fPars.ownResTrk.VMs == vmRecords)
    assert (fPars.ownResTrk.Secrets == [secretRecord])
    assert (fPars.ownResTrk.NSs == [namespaceRecord])
    MUT.VM.CleanUpVMs.assert_called_once_with(
        wrappers,
        vmRecords,
        optsCleanUpRes={'optsDelRes': {'waitTime': '3m'}},
    )
    assert (ctx.ocFactory.CleanUpRes.call_args_list == [
        mock.call([secretRecord]), mock.call([namespaceRecord]),
    ])
    assert ([call[0] for call in ordered.mock_calls] == [
        'vms', 'resources', 'resources',
    ])
    clients[0].Run.assert_called_once_with(
        'get',
        'VirtualMachines',
        n='import-ns',
        namespace=None,
        o='jsonpath={.items[*].metadata.uid}',
    )
    if cleanupFails:
        MUT.logs.error.assert_called_once_with(
            'Could not clean up fixture VMs: %s', secondary,
        )
    else: MUT.logs.error.assert_not_called()


@pytest.mark.parametrize('outcome', ['ready', 'false', 'exception'])
@pytest.mark.parametrize('customTimeout', [None, '7m'])
def test_FxtVM_StartFailureCleansAllResolvedVMsAndPreservesPrimary(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    customTimeout: str | None,
) -> None:
    ctx = vmTestContext
    removeTimeout = '11m' if customTimeout else '3m'
    initialConfig = ctx.config(
        count=2, startVM=True, namePfx='startup', ctxData={},
    )
    initialConfig.fxtInf.vmCfg.timeout.vmRemove = removeTimeout
    harness = PrepareFxtVM(
        ctx, monkeypatch, initialConfig, {'ns': 'startup-ns'},
    )
    wrappers = [mock.Mock(name='startupVM0'), mock.Mock(name='startupVM1')]
    records = [
        (mock.sentinel.startupOC0, 'VirtualMachine/startup-0', 'uid-0'),
        (mock.sentinel.startupOC1, 'VirtualMachine/startup-1', 'uid-1'),
    ]
    primary = {
        'ready': None,
        'false': RuntimeError('VM `startup-ns/startup-0` failed to start.'),
        'exception': RuntimeError('synthetic start exception'),
    }[outcome]

    def Create(request: Any, fPars: Any, **kwargs: Any) -> list[Any]:
        fPars.vmList.extend(wrappers)
        fPars.ownResTrk.VMs.extend(records)
        if (primary is not None): raise primary
        return wrappers

    harness.vmCreate.side_effect = Create
    generator = MUT.FxtVM.__wrapped__(
        harness.request, ctx.ocFactory, ctx.virtCtlFactory,
    )

    if (outcome == 'ready'):
        harness.excInfo.return_value = (None, None, None)
        bundle = next(generator)
        assert (bundle.vmList == wrappers)
        with pytest.raises(StopIteration):
            next(generator)
    else:
        harness.excInfo.return_value = (RuntimeError, primary, None)
        with pytest.raises(RuntimeError) as caught:
            next(generator)
        assert (caught.value is primary)

    fPars = harness.vmCreate.call_args_list[0].args[1]
    harness.vmCreate.assert_called_once()
    assert (fPars.vmList == wrappers)
    assert (fPars.ownResTrk.VMs == records)
    MUT.VM.CleanUpVMs.assert_called_once_with(
        fPars.vmList,
        fPars.ownResTrk.VMs,
        optsCleanUpRes={'optsDelRes': {'waitTime': removeTimeout}},
    )


@pytest.mark.parametrize('failure', [
    'mismatch', 'secret', 'wrapper', 'cleanup',
])
def test_FxtVM_ResourceHandlersKeepFailureCleanupAndPrimaryError(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    ctx = vmTestContext
    initialConfig = ctx.config(count=1, namePfx='resource')
    harness = PrepareFxtVM(ctx, monkeypatch, initialConfig)
    client = mock.Mock(name='resourceOC')
    wrapper = mock.Mock(name='resourceVM')
    namespaceRecord = (client, 'Namespace/resource-ns', 'namespace-uid')
    secretRecord = (client, 'Secret/resource-secret', 'secret-uid')
    vmRecord = (client, 'VirtualMachine/resource-0', 'vm-uid')
    primary = RuntimeError('primary fixture failure')
    secondary = RuntimeError('secondary cleanup failure')
    stateByFailure = {
        'mismatch': ([], [], [], [namespaceRecord]),
        'secret': ([], [], [secretRecord], [namespaceRecord]),
        'wrapper': ([], [vmRecord], [secretRecord], [namespaceRecord]),
        'cleanup': (
            [wrapper], [vmRecord], [secretRecord], [namespaceRecord],
        ),
    }
    (wrappers, vmRecords, secretRecords, namespaceRecords) = (
        stateByFailure[failure]
    )
    cleanupOrder = []
    resourceCalls = []
    retain = mock.Mock(return_value=False)
    monkeypatch.setattr(MUT, '_RetainNS', retain)

    def CleanUpVMs(*args: Any, **kwargs: Any) -> None:
        cleanupOrder.append('vm')
        if (failure == 'cleanup'): raise primary
        raise secondary

    def CleanUpRes(records: list[Any], **kwargs: Any) -> None:
        resourceKind = 'secret' if (len(resourceCalls) == 0) else 'namespace'
        resourceCalls.append((resourceKind, records, kwargs))
        cleanupOrder.append(resourceKind)
        if records: raise secondary

    MUT.VM.CleanUpVMs.side_effect = CleanUpVMs
    ctx.ocFactory.CleanUpRes.side_effect = CleanUpRes

    def Create(
        request: Any,
        fPars: MUT.FuncParsVMcreate,
        *,
        ocCLI: Any,
        virtCtlCLI: Any,
        **overrides: Any,
    ) -> list[Any]:
        fPars.vmList.extend(wrappers)
        fPars.ownResTrk.VMs.extend(vmRecords)
        fPars.ownResTrk.Secrets.extend(secretRecords)
        fPars.ownResTrk.NSs.extend(namespaceRecords)
        if (failure != 'cleanup'): raise primary
        return wrappers

    target = harness.vmCreate
    target.side_effect = Create
    generator = MUT.FxtVM.__wrapped__(
        harness.request, ctx.ocFactory, ctx.virtCtlFactory,
    )

    if (failure == 'cleanup'):
        harness.excInfo.return_value = (None, None, None)
        bundle = next(generator)
        fPars = target.call_args_list[0].args[1]
        assert (bundle.vmList is fPars.vmList)
        assert (bundle.vmList == wrappers)
        with pytest.raises(RuntimeError) as caught:
            next(generator)
    else:
        harness.excInfo.return_value = (RuntimeError, primary, None)
        with pytest.raises(RuntimeError) as caught:
            next(generator)
        fPars = target.call_args_list[0].args[1]

    # Without a primary error, teardown re-raises its last cleanup
    #   failure (`VM.py` `clnExc`).
    expectedError = secondary if (failure == 'cleanup') else primary
    assert (caught.value is expectedError)
    if (failure == 'cleanup'):
        assert (MUT.logs.error.call_args_list == [
            mock.call('Could not clean up fixture VMs: %s', primary),
            mock.call('Could not clean up fixture resources: %s', secondary),
            mock.call('Could not clean up fixture resources: %s', secondary),
        ])
    assert (fPars.vmList == wrappers)
    assert (fPars.ownResTrk.VMs == vmRecords)
    assert (fPars.ownResTrk.Secrets == secretRecords)
    assert (fPars.ownResTrk.NSs == namespaceRecords)
    target.assert_called_once_with(
        harness.request,
        fPars,
        ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory,
    )
    assert (len(resourceCalls) == 2)
    assert (resourceCalls[0][0:2] == ('secret', secretRecords))
    assert (resourceCalls[1][0:2] == ('namespace', namespaceRecords))
    assert (resourceCalls[1][2] == {})
    assert (retain.call_args_list == [
        mock.call(client, 'Namespace/resource-ns'),
    ])
    expectedOrder = (
        (['vm'] if (wrappers or vmRecords) else [])
        + ['secret', 'namespace']
    )
    assert (cleanupOrder == expectedOrder)
    if (vmRecords or wrappers):
        MUT.VM.CleanUpVMs.assert_called_once_with(
            wrappers,
            vmRecords,
            optsCleanUpRes={'optsDelRes': {'waitTime': '3m'}},
        )
    else: MUT.VM.CleanUpVMs.assert_not_called()


@pytest.mark.parametrize('case', [
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
])
def test_FxtVM_OwnedResourceLifecycleBoundaryConsumer(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    ctx = vmTestContext
    initialConfig = ctx.config(count=1, namePfx='boundary')
    harness = PrepareFxtVM(ctx, monkeypatch, initialConfig)
    client = mock.Mock(name='boundaryOC')
    wrapper = mock.Mock(name='boundaryVM')
    namespaceRecord = (client, 'Namespace/boundary-ns', 'namespace-uid')
    changedNamespaceRecord = (
        client, 'Namespace/boundary-ns', 'canned-replacement-namespace-uid',
    )
    secretRecord = (client, 'Secret/boundary-secret', 'secret-uid')
    changedSecretRecord = (
        client, 'Secret/boundary-secret', 'canned-replacement-secret-uid',
    )
    vmRecord = (client, 'VirtualMachine/boundary-0', 'vm-uid')
    changedVMRecord = (
        client, 'VirtualMachine/boundary-0', 'canned-replacement-vm-uid',
    )
    primary = RuntimeError('canned VM-create failure')
    secondary = RuntimeError('canned resource-cleanup failure')
    outcomes = {
        'owned': (
            [wrapper], [vmRecord], [secretRecord], [namespaceRecord], None,
        ),
        'existing': ([wrapper], [vmRecord], [], [], None),
        'different': ([], [], [], [], primary),
        'render-failure': (
            [], [], [secretRecord], [namespaceRecord], primary,
        ),
        'uid-changed': (
            [wrapper], [vmRecord], [changedSecretRecord],
            [namespaceRecord], None,
        ),
        'vm-wait-failure': (
            [wrapper], [vmRecord], [secretRecord], [namespaceRecord],
            primary,
        ),
        'secret-delete-failed': (
            [wrapper], [vmRecord], [secretRecord], [namespaceRecord], None,
        ),
        'namespace-delete-failed': (
            [wrapper], [vmRecord], [secretRecord], [namespaceRecord], None,
        ),
        'existing-uid-changed': (
            [wrapper], [vmRecord], [], [changedNamespaceRecord], None,
        ),
        'vm-uid-changed': (
            [wrapper], [changedVMRecord], [secretRecord],
            [namespaceRecord], None,
        ),
        'namespace-uid-changed': (
            [wrapper], [vmRecord], [secretRecord],
            [changedNamespaceRecord], None,
        ),
    }
    (wrappers, vmRecords, secretRecords, namespaceRecords, createError) = (
        outcomes[case]
    )
    cleanupOrder = []
    resourceCalls = []
    retain = mock.Mock(return_value=False)
    monkeypatch.setattr(MUT, '_RetainNS', retain)

    def CleanUpVMs(*args: Any, **kwargs: Any) -> None:
        cleanupOrder.append('vm')

    def CleanUpRes(records: list[Any], **kwargs: Any) -> None:
        resourceKind = 'secret' if (len(resourceCalls) == 0) else 'namespace'
        resourceCalls.append((resourceKind, records, kwargs))
        cleanupOrder.append(resourceKind)
        if (
            records and (
                (
                    (case == 'secret-delete-failed') and
                    (resourceKind == 'secret')
                )
                or (
                    (case == 'namespace-delete-failed') and
                    (resourceKind == 'namespace')
                )
            )
        ): raise secondary

    MUT.VM.CleanUpVMs.side_effect = CleanUpVMs
    ctx.ocFactory.CleanUpRes.side_effect = CleanUpRes

    def Create(
        request: Any,
        fPars: MUT.FuncParsVMcreate,
        *,
        ocCLI: Any,
        virtCtlCLI: Any,
        **overrides: Any,
    ) -> list[Any]:
        fPars.vmList.extend(wrappers)
        fPars.ownResTrk.VMs.extend(vmRecords)
        fPars.ownResTrk.Secrets.extend(secretRecords)
        fPars.ownResTrk.NSs.extend(namespaceRecords)
        if (createError is not None): raise createError
        return wrappers

    target = harness.vmCreate
    target.side_effect = Create
    generator = MUT.FxtVM.__wrapped__(
        harness.request, ctx.ocFactory, ctx.virtCtlFactory,
    )

    if (createError is not None):
        harness.excInfo.return_value = (RuntimeError, primary, None)
        with pytest.raises(RuntimeError) as caught:
            next(generator)
        assert (caught.value is primary)
    else:
        bundle = next(generator)
        fPars = target.call_args_list[0].args[1]
        assert (bundle.vmList is fPars.vmList)
        assert (bundle.vmList == wrappers)
        harness.excInfo.return_value = (None, None, None)
        if (case in ('secret-delete-failed', 'namespace-delete-failed')):
            with pytest.raises(RuntimeError) as caught:
                next(generator)
            assert (caught.value is secondary)
        else:
            with pytest.raises(StopIteration):
                next(generator)

    fPars = target.call_args_list[0].args[1]
    assert (fPars.vmList == wrappers)
    assert (fPars.ownResTrk.VMs == vmRecords)
    assert (fPars.ownResTrk.Secrets == secretRecords)
    assert (fPars.ownResTrk.NSs == namespaceRecords)
    target.assert_called_once_with(
        harness.request,
        fPars,
        ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory,
    )
    assert (len(resourceCalls) == 2)
    assert (resourceCalls[0][0:2] == ('secret', secretRecords))
    assert (resourceCalls[1][0:2] == ('namespace', namespaceRecords))
    if namespaceRecords:
        assert (resourceCalls[1][2] == {})
        assert (retain.call_args_list == [mock.call(*namespaceRecords[0][:2])])
    else: retain.assert_not_called()
    expectedOrder = (
        (['vm'] if (wrappers or vmRecords) else [])
        + ['secret', 'namespace']
    )
    assert (cleanupOrder == expectedOrder)
    if (vmRecords or wrappers):
        MUT.VM.CleanUpVMs.assert_called_once_with(
            wrappers,
            vmRecords,
            optsCleanUpRes={'optsDelRes': {'waitTime': '3m'}},
        )
    else: MUT.VM.CleanUpVMs.assert_not_called()


def VMKeyContext(ctx: SimpleNamespace) -> SimpleNamespace:
    '''Wrap only the test config factory with canned user credentials.'''
    configFactory = ctx.config

    cannedCredentials = {'sshUsr': 'canned-user', 'sshKey': 'canned-token'}

    class UserCredentials(SimpleNamespace):
        Add = mock.Mock(return_value=cannedCredentials)

        def __add__(self, other: Any) -> Any:
            return type(self).Add(self, other)

    def Config(*args: Any, **kwargs: Any) -> Any:
        cfg = configFactory(*args, **kwargs)
        source = cfg.fxtInf.gstCfg.usrCrd
        usrCrd = UserCredentials(sshUsr=source.sshUsr, sshKey=source.sshKey)
        assert (vars(usrCrd) == {
            'sshUsr': source.sshUsr, 'sshKey': source.sshKey,
        })
        cfg.fxtInf.gstCfg.usrCrd = usrCrd
        return cfg

    ctx.config = Config
    ctx.UserCredentials = UserCredentials
    ctx.userCredentialsAdd = UserCredentials.Add
    for (path, value) in (
        ('/synthetic/access', 'synthetic-access-value'),
        ('/synthetic/secret', 'synthetic-secret-value'),
        ('access', 'synthetic-access-value'),
        ('relative-secret', 'synthetic-secret-value'),
    ):
        credentialPath = ctx.paths.get(path)
        if (credentialPath is None): credentialPath = ctx.addPath(path)
        credentialPath.read_text.return_value = value
    return ctx


def AssertVMUserCredentialAdds(ctx: SimpleNamespace, *configs: Any) -> None:
    '''Verify credential identity and each literal override argument.'''
    calls = ctx.userCredentialsAdd.call_args_list[-len(configs):]
    assert (len(calls) == len(configs))
    for (captured, cfg) in zip(calls, configs):
        credentials = cfg.fxtInf.gstCfg.usrCrd
        assert (captured.args[0] is credentials)
        assert (captured.args[1] == {'sshKey': credentials.sshKey.prv})
        assert (captured.kwargs == {})


# Key lifetime, per-call identity, retry, and cleanup boundaries.
def QueueVMConfigMerges(root: Any, *results: Any) -> None:
    '''
    Return only explicitly queued case configurations from the merge mock.
    '''
    root._merge.side_effect = list(results)


def AddVMKeyDirectory(
    ctx: SimpleNamespace,
    name: str,
    privateName: str = 'id_ed25519',
) -> tuple[Any, Any, Any, Any]:
    '''
    Register lexical key paths and a directory mock; no filesystem access.
    '''
    directory = mock.Mock(name='temporaryDirectory')
    directory.name = name
    tempPath = ctx.addPath(name)
    privatePath = ctx.addPath(f'{name}/{privateName}', name=privateName)
    publicPath = ctx.addPath(
        f'{name}/{privateName}.pub', name=f'{privateName}.pub',
    )
    tempPath.__truediv__.return_value = privatePath
    privatePath.with_suffix.return_value = publicPath
    return directory, tempPath, privatePath, publicPath


def SetVMPathText(path: Any, value: str) -> Any:
    '''Return literal text from the mocked path object's mocked open stream.'''
    manager = mock.MagicMock(name='openedTextFile')
    stream = mock.MagicMock(name='textStream')
    manager.__enter__.return_value = stream
    manager.__exit__.return_value = False
    stream.read.return_value = value
    path.open.return_value = manager
    return stream


def StartFxtVM(
    ctx: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    selected: Any,
    resolved: Any,
    param: dict[str, Any] | None = None,
    keyDirs: tuple[Any, ...] = (),
) -> Any:
    '''Start FxtVM with mocked merges, key resolver and VMcreate.'''
    defaultCfg = ctx.config()
    QueueVMConfigMerges(defaultCfg, selected)
    monkeypatch.setattr(MUT, '_DEF__FXT_CFG', defaultCfg)
    request = VMRequest(ctx)
    request.param = {} if (param is None) else param

    def ResolveSSHkeys(fxtCfg: Any, sshKeyDirs: list[Any]) -> Any:
        sshKeyDirs.extend(keyDirs)
        return resolved

    ctx.resolve = mock.create_autospec(
        MUT._ResolveSSHkeys, side_effect=ResolveSSHkeys,
    )
    monkeypatch.setattr(MUT, '_ResolveSSHkeys', ctx.resolve)
    ctx.vmRequest = request
    ctx.vmCreate = mock.create_autospec(MUT.VMcreate, return_value=[])
    monkeypatch.setattr(MUT, 'VMcreate', ctx.vmCreate)
    return MUT.FxtVM.__wrapped__(request, ctx.ocFactory, ctx.virtCtlFactory)


def BoundFPars(ctx: SimpleNamespace) -> Any:
    '''Assert initial FxtVM delegation and return its VMcreate fPars.'''
    initCall = ctx.vmCreate.call_args_list[0]
    assert (initCall == mock.call(
        ctx.vmRequest, initCall.args[1], ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory,
    ))
    return initCall.args[1]


def PerCallVMcreate(ctx: SimpleNamespace, **overrides: Any) -> Any:
    '''Expected VMcreate call for one per-call fixture invocation.'''
    return mock.call(
        ctx.vmRequest, BoundFPars(ctx), ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory, **overrides,
    )


def VMKeyModeInputs(
    ctx: SimpleNamespace,
    mode: str,
    label: str,
) -> tuple[dict[str, Any], str, str, Any, bool]:
    '''Build one explicit key-mode case from registered lexical paths.'''
    if (mode in ('generate', 'public-only')):
        (directory, tempPath, privatePath, publicPath) = AddVMKeyDirectory(
            ctx, f'/synthetic/{label}-keys',
        )
        return (
            {
                'prv': None,
                'pub': (
                    None if (mode == 'generate')
                    else f'/synthetic/{label}-provided.pub'
                ),
            },
            str(privatePath), str(publicPath), directory, True,
        )
    if (mode == 'derive'):
        (directory, tempPath, unusedPrivate, publicPath) = AddVMKeyDirectory(
            ctx, f'/synthetic/{label}-keys', 'alternate.pem',
        )
        privatePath = ctx.addPath(
            f'/synthetic/{label}-source/alternate.pem', name='alternate.pem',
        )
        tempPath.__truediv__.return_value = publicPath
        return (
            {'prv': str(privatePath), 'pub': None},
            str(privatePath), str(publicPath), directory, True,
        )
    privatePath = ctx.addPath(f'/synthetic/{label}-private', name='private')
    publicPath = ctx.addPath(f'/synthetic/{label}-public', name='public')
    return (
        {'prv': str(privatePath), 'pub': str(publicPath)},
        str(privatePath), str(publicPath), None, False,
    )


@pytest.mark.parametrize('fld', ['prv', 'pub'])
def test_FxtVM_SuppliedPathObjectsFailNativeMergeBeforeResourceEffects(
    vmTestContext: SimpleNamespace,
    fld: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    cfg = ctx.config(count=1)
    paths = {
        'prv': ctx.addPath('/synthetic/private-key', name='private-key'),
        'pub': ctx.addPath('/synthetic/public-key', name='public-key'),
    }
    keyArgs = {
        'prv': '/synthetic/private-key', 'pub': '/synthetic/public-key',
    }
    keyArgs[fld] = paths[fld]
    overrides = {'fxtInf': {'gstCfg': {'usrCrd': {'sshKey': keyArgs}}}}
    ownResTrk = SimpleNamespace(NSs=[], Secrets=[], VMs=[])
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=cfg, vmList=[], ownResTrk=ownResTrk, sshKeyDirs=[],
    )
    cfg._merge.side_effect = TypeError('canned path merge failure')

    with pytest.raises(TypeError, match='canned path merge failure'):
        MUT.VMcreate(
            VMRequest(ctx), fPars, ocCLI=ctx.ocFactory,
            virtCtlCLI=ctx.virtCtlFactory, **overrides,
        )

    ctx.namedDict.assert_called_once_with(overrides)
    cfg._merge.assert_called_once_with(ctx.namedDictMade[-1])
    ctx.ocFactory.assert_not_called()
    ctx.virtCtlFactory.assert_not_called()
    MUT.Path.assert_not_called()
    MUT.TemporaryDirectory.assert_not_called()
    ctx.cmdExec.assert_not_called()
    MUT.Namespace.Create.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    MUT.YAMLcfg.Render.assert_not_called()


@pytest.mark.parametrize(
    'case', ['missing', 'directory', 'invalid', 'encrypted'],
)
def test_FxtVM_PrivateKeyFailureIsNoninteractiveAndSanitized(
    vmTestContext: SimpleNamespace,
    case: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    privateValue = '/synthetic/source/private-key'
    privatePath = ctx.addPath(privateValue, name='private-key')
    (directory, tempPath, unusedPrivate, publicPath) = AddVMKeyDirectory(
        ctx, f'/synthetic/{case}-derive', 'private-key',
    )
    tempPath.__truediv__.return_value = publicPath
    MUT.TemporaryDirectory.return_value = directory
    output = f'{case}-private-output'
    error = f'{case}-private-stderr'
    ctx.cmdExec.return_value.Run.return_value = CommandResult(
        False, output, error,
    )
    cfg = ctx.config(private=privateValue, public=None)
    before = ConfigSnapshot(cfg)
    sshKeyDirs = []

    with pytest.raises(ValueError) as caught:
        MUT._ResolveSSHkeys(cfg, sshKeyDirs)

    assert (str(caught.value) == (
        '`fxtInf.gstCfg.usrCrd.sshKey`: Public Key resolution failed. '
        'Use readable Public or unencrypted Private SSH Key files and/or '
        'install `ssh-keygen`.'
    ))
    assert (output not in str(caught.value))
    assert (error not in str(caught.value))
    assert caught.value.__suppress_context__
    assert (output not in str(MUT.logs.mock_calls))
    assert (error not in str(MUT.logs.mock_calls))
    assert (sshKeyDirs == [directory])
    assert (ConfigSnapshot(cfg) == before)
    assert (ctx.cmdExec.return_value.Run.call_args == mock.call(
        ['ssh-keygen', '-y', '-P', '', '-f', privateValue],
        input='', isSecret=True, spPars={'timeout': 30},
    ))
    publicPath.write_text.assert_not_called()
    cfg._merge.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    MUT.Namespace.Create.assert_not_called()


@pytest.mark.parametrize('case', ['key-timeout', 'key-empty'])
def test_FxtVM_KeyTimeoutOrEmptyOutputFailsBeforeEffects(
    vmTestContext: SimpleNamespace,
    case: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    privateValue = '/synthetic/source/private-key'
    ctx.addPath(privateValue, name='private-key')
    (directory, tempPath, unusedPrivate, publicPath) = AddVMKeyDirectory(
        ctx, '/synthetic/key-resolution', 'private-key',
    )
    tempPath.__truediv__.return_value = publicPath
    MUT.TemporaryDirectory.return_value = directory
    if (case == 'key-timeout'):
        ctx.cmdExec.return_value.Run.side_effect = TimeoutError(
            'synthetic-private-timeout',
        )
    else: ctx.cmdExec.return_value.Run.return_value = CommandResult(True, '')
    cfg = ctx.config(private=privateValue, public=None)
    before = ConfigSnapshot(cfg)
    sshKeyDirs = []

    with pytest.raises(ValueError) as caught:
        MUT._ResolveSSHkeys(cfg, sshKeyDirs)

    assert ('synthetic-private-timeout' not in str(caught.value))
    assert caught.value.__suppress_context__
    assert (sshKeyDirs == [directory])
    assert (ConfigSnapshot(cfg) == before)
    assert (ctx.cmdExec.return_value.Run.call_args.kwargs == {
        'input': '', 'isSecret': True, 'spPars': {'timeout': 30},
    })
    publicPath.write_text.assert_not_called()
    cfg._merge.assert_not_called()
    MUT.TemporaryDirectory.assert_called_once_with(prefix='fxtVM--ssh--')
    MUT.VirtualMachine.Create.assert_not_called()
    MUT.Namespace.Create.assert_not_called()


def test_FxtVM_PartialNamedDictOverridesPreserveInputsAndDefaults(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    privateValue = '/synthetic/base-private'
    publicValue = '/synthetic/base-public'
    ctx.addPath(privateValue, name='base-private')
    publicPath = ctx.addPath(publicValue, name='base-public')
    SetVMPathText(publicPath, 'ssh-ed25519 base-public\n')
    selected = ctx.config(private=privateValue, public=publicValue, count=0)
    resolved = ctx.config(private=privateValue, public=publicValue, count=0)
    param = {'fxtInf': {'vmCfg': {'count': 0, 'ctxData': {'owner': 'case'}}}}
    generator = StartFxtVM(ctx, monkeypatch, selected, resolved, param)
    beforeParam = json.dumps(param)
    defaultsBefore = ConfigSnapshot(MUT._DEF__FXT_CFG)
    bundle = next(generator)
    overrides = {
        'fxtInf': {'vmCfg': {'count': 0, 'ctxData': {'owner': 'override'}}},
    }
    overridesBefore = json.dumps(overrides)

    assert (bundle.vmCreate(**overrides) == [])

    assert (ctx.namedDict.call_args_list[0] == mock.call(**param))
    assert (json.dumps(param) == beforeParam)
    assert (json.dumps(overrides) == overridesBefore)
    assert (resolved is BoundFPars(ctx).fxtCfgObj)
    assert (ctx.vmCreate.call_args_list[1:] == [
        PerCallVMcreate(ctx, **overrides),
    ])
    assert (ConfigSnapshot(MUT._DEF__FXT_CFG) == defaultsBefore)
    generator.close()


def test_FxtVM_NoOverrideSkipsResolutionAndSharesOwnedDirectories(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    privateValue = '/synthetic/base-private'
    publicValue = '/synthetic/base-public'
    publicPath = ctx.addPath(publicValue, name='base-public')
    SetVMPathText(publicPath, 'ssh-ed25519 base-public\n')
    selected = ctx.config(private=privateValue, public=publicValue, count=0)
    resolved = ctx.config(private=privateValue, public=publicValue, count=0)
    generator = StartFxtVM(ctx, monkeypatch, selected, resolved)
    bundle = next(generator)
    sshKeyDirs = BoundFPars(ctx).sshKeyDirs
    ctx.resolve.assert_called_once_with(selected, sshKeyDirs)
    assert (ctx.resolve.call_args.args[0] is selected)
    assert (ctx.resolve.call_args.args[1] is sshKeyDirs)

    assert (bundle.vmCreate() == [])

    ctx.resolve.assert_called_once()
    assert (ctx.vmCreate.call_args_list[1:] == [PerCallVMcreate(ctx)])
    assert (BoundFPars(ctx).sshKeyDirs is sshKeyDirs)
    assert (sshKeyDirs == [])
    generator.close()


def test_FxtVM_PerCallAlternatePEMPrivateDerivesOwnedPublic(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    privateValue = '/synthetic/source/alternate.pem'
    privatePath = ctx.addPath(privateValue, name='alternate.pem')
    (directory, tempPath, unusedPrivate, publicPath) = AddVMKeyDirectory(
        ctx, '/synthetic/alternate-derive', 'alternate.pem',
    )
    tempPath.__truediv__.return_value = publicPath
    MUT.TemporaryDirectory.return_value = directory
    ctx.cmdExec.return_value.Run.return_value = CommandResult(
        True, 'ssh-rsa derived-public comment\n',
    )
    cfg = ctx.config(private=privateValue, public=None)
    peer = ctx.config(
        private=privateValue,
        public='/synthetic/alternate-derive/alternate.pem.pub',
    )
    QueueVMConfigMerges(cfg, peer)
    before = ConfigSnapshot(cfg)
    sshKeyDirs = []

    resolved = MUT._ResolveSSHkeys(cfg, sshKeyDirs)

    assert (resolved is peer)
    assert (resolved.fxtInf.gstCfg.usrCrd.sshKey.prv == privateValue)
    assert (resolved.fxtInf.gstCfg.usrCrd.sshKey.pub == str(publicPath))
    assert (sshKeyDirs == [directory])
    assert (ConfigSnapshot(cfg) == before)
    assert (publicPath.name == 'alternate.pem.pub')
    publicPath.write_text.assert_called_once_with(
        'ssh-rsa derived-public comment\n', encoding='utf-8',
    )
    assert (ctx.cmdExec.return_value.Run.call_args == mock.call(
        ['ssh-keygen', '-y', '-P', '', '-f', privateValue],
        input='', isSecret=True, spPars={'timeout': 30},
    ))


def test_FxtVM_KeyExecutorExceptionIsSanitizedBeforeEffects(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    privateValue = '/synthetic/source/private-key'
    ctx.addPath(privateValue, name='private-key')
    (directory, tempPath, unusedPrivate, publicPath) = AddVMKeyDirectory(
        ctx, '/synthetic/key-executor-error', 'private-key',
    )
    tempPath.__truediv__.return_value = publicPath
    MUT.TemporaryDirectory.return_value = directory
    secretError = 'synthetic-sensitive-executor-exception'
    ctx.cmdExec.return_value.Run.side_effect = RuntimeError(secretError)
    cfg = ctx.config(private=privateValue, public=None)
    before = ConfigSnapshot(cfg)

    with pytest.raises(ValueError) as caught:
        MUT._ResolveSSHkeys(cfg, [])

    assert ('Public Key resolution failed' in str(caught.value))
    assert caught.value.__suppress_context__
    assert (secretError not in str(caught.value))
    assert (secretError not in str(MUT.logs.mock_calls))
    publicPath.write_text.assert_not_called()
    cfg._merge.assert_not_called()
    MUT.VirtualMachine.Create.assert_not_called()
    MUT.Namespace.Create.assert_not_called()
    assert (ConfigSnapshot(cfg) == before)


@pytest.mark.parametrize('keyMode', ['generate', 'read'])
def test_FxtVM_KeyPairLifetimeTracksResolvedDirectoriesUntilClose(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    keyMode: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    (keyInput, privateValue, publicValue, directory, unusedCommand) = (
        VMKeyModeInputs(ctx, keyMode, 'lifetime')
    )
    selected = ctx.config(
        private=keyInput['prv'], public=keyInput['pub'], count=1,
        credType='None',
    )
    resolved = ctx.config(
        private=privateValue, public=publicValue, count=1, credType='None',
    )
    publicPath = ctx.paths[publicValue]
    SetVMPathText(publicPath, 'ssh-ed25519 literal-public\r\n')
    keyDirs = () if (directory is None) else (directory,)
    generator = StartFxtVM(
        ctx, monkeypatch, selected, resolved, keyDirs=keyDirs,
    )
    vm = mock.Mock(name='lifetime-vm')

    def Create(request: Any, fPars: Any, **kwargs: Any) -> list[Any]:
        fPars.vmList.append(vm)
        return [vm]

    ctx.vmCreate.side_effect = Create
    MUT.sys.exc_info.return_value = (None, None, None)
    bundle = next(generator)

    assert (bundle.vmList == [vm])
    ctx.resolve.assert_called_once_with(selected, BoundFPars(ctx).sshKeyDirs)
    assert (BoundFPars(ctx).fxtCfgObj is resolved)
    publicPath.open.assert_called_once_with(encoding='utf-8', newline='')
    assert (BoundFPars(ctx).sshKeyDirs == list(keyDirs))
    if (directory is not None): directory.cleanup.assert_not_called()
    MUT.sys.exc_info.return_value = (GeneratorExit, GeneratorExit(), None)
    generator.close()
    MUT.VM.CleanUpVMs.assert_called_once()
    if (directory is not None): directory.cleanup.assert_called_once_with()


@pytest.mark.parametrize(
    'failure', ['generate', 'create', 'postyield', 'cleanup'],
)
def test_FxtVM_OwnedKeyFilesRemovedAfterFailure(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    (directory, tempPath, privatePath, publicPath) = AddVMKeyDirectory(
        ctx, f'/synthetic/owned-{failure}',
    )
    selected = ctx.config(private=None, public=None, count=0, credType='None')
    resolved = ctx.config(
        private=str(privatePath), public=str(publicPath), count=0,
        credType='None',
    )
    SetVMPathText(publicPath, 'ssh-ed25519 owned-public\n')
    generator = StartFxtVM(
        ctx, monkeypatch, selected, resolved, keyDirs=(directory,),
    )
    if (failure == 'generate'):
        primary = ValueError('canned key generation failure')

        def FailResolve(fxtCfg: Any, sshKeyDirs: list[Any]) -> Any:
            sshKeyDirs.append(directory)
            raise primary

        ctx.resolve.side_effect = FailResolve
        MUT.sys.exc_info.return_value = (ValueError, primary, None)
    elif (failure == 'create'):
        primary = RuntimeError('synthetic-resource-primary')
        ctx.vmCreate.side_effect = primary
        MUT.sys.exc_info.return_value = (RuntimeError, primary, None)

    if (failure in ('generate', 'create')):
        with pytest.raises((ValueError, RuntimeError)) as caught:
            next(generator)
        assert (caught.value is primary)
        assert (str(directory.name) not in str(caught.value))
        if (failure == 'generate'): ctx.vmCreate.assert_not_called()
    elif (failure == 'postyield'):
        next(generator)
        MUT.sys.exc_info.return_value = (GeneratorExit, GeneratorExit(), None)
        generator.close()
    else:
        next(generator)
        directory.cleanup.side_effect = OSError('synthetic-cleanup-error')
        MUT.sys.exc_info.return_value = (None, None, None)
        with pytest.raises(RuntimeError, match='Could not clean up') as caught:
            next(generator)
        assert caught.value.__suppress_context__
        assert ('synthetic-cleanup-error' not in str(MUT.logs.mock_calls))

    directory.cleanup.assert_called_once_with()


@pytest.mark.parametrize('publicFile', ['missing', 'empty', 'invalid-utf8'])
def test_FxtVM_PublicFileFailureIsSanitizedAndPreservesPrivateFile(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    publicFile: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    privateValue = '/synthetic/private-key'
    publicValue = '/synthetic/unreadable-public'
    privatePath = ctx.addPath(privateValue, name='private-key')
    publicPath = ctx.addPath(publicValue, name='public-key')
    selected = ctx.config(private=privateValue, public=publicValue, count=0)
    resolved = ctx.config(private=privateValue, public=publicValue, count=0)
    if (publicFile == 'missing'):
        publicPath.open.side_effect = FileNotFoundError(
            'synthetic-sensitive-missing-path',
        )
    else:
        stream = SetVMPathText(
            publicPath, ' ' if (publicFile == 'empty') else '',
        )
        if (publicFile == 'invalid-utf8'):
            stream.read.side_effect = UnicodeError(
                'synthetic-sensitive-invalid-public-bytes',
            )
    generator = StartFxtVM(ctx, monkeypatch, selected, resolved)
    primary = ValueError('canned public-file primary')
    MUT.sys.exc_info.return_value = (ValueError, primary, None)

    with pytest.raises(
        ValueError, match='Public Key resolution failed',
    ) as caught:
        next(generator)

    text = str(caught.value) + str(MUT.logs.mock_calls)
    assert caught.value.__suppress_context__
    assert ('synthetic-sensitive' not in text)
    assert (privatePath.write_text.call_count == 0)
    assert (privatePath.read_text.call_count == 0)
    ctx.resolve.assert_called_once_with(selected, [])
    ctx.vmCreate.assert_not_called()
    publicPath.open.assert_called_once_with(encoding='utf-8', newline='')


@pytest.mark.parametrize('failure', ['public', 'derive'])
def test_FxtVM_FailedResolutionLeavesInputsRetryableWithoutPartialPaths(
    vmTestContext: SimpleNamespace,
    failure: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    privateValue = '/synthetic/source/private-key'
    privatePath = ctx.addPath(privateValue, name='private-key')
    if (failure == 'public'):
        publicValue = '/synthetic/source/public-key'
        ctx.addPath(publicValue, name='public-key')
        cfg = ctx.config(private=privateValue, public=publicValue)
        peer = ctx.config(private=privateValue, public=publicValue)
        QueueVMConfigMerges(cfg, peer)
        before = ConfigSnapshot(cfg)

        def ExpandPath(value: str) -> str:
            if (value == publicValue):
                raise OSError('synthetic-public-path-resolution')
            return value

        MUT.os.path.expandvars.side_effect = ExpandPath
        with pytest.raises(OSError, match='synthetic-public-path-resolution'):
            MUT._ResolveSSHkeys(cfg, [])
        MUT.os.path.expandvars.side_effect = lambda value: (
            ctx.expansions.get(value, value)
        )
        resolved = MUT._ResolveSSHkeys(cfg, [])
        assert (resolved is peer)
        assert (ConfigSnapshot(cfg) == before)
        MUT.TemporaryDirectory.assert_not_called()
    else:
        (directory0, tempPath0, unused0, publicPath0) = AddVMKeyDirectory(
            ctx, '/synthetic/derive-failure-0', 'private-key',
        )
        (directory1, tempPath1, unused1, publicPath1) = AddVMKeyDirectory(
            ctx, '/synthetic/derive-failure-1', 'private-key',
        )
        tempPath0.__truediv__.return_value = publicPath0
        tempPath1.__truediv__.return_value = publicPath1
        MUT.TemporaryDirectory.side_effect = [directory0, directory1]
        ctx.cmdExec.return_value.Run.side_effect = [
            RuntimeError('synthetic-derive-failure'),
            CommandResult(True, 'ssh-ed25519 corrected-public\n'),
        ]
        cfg = ctx.config(private=privateValue, public=None)
        peer = ctx.config(private=privateValue, public=str(publicPath1))
        QueueVMConfigMerges(cfg, peer)
        before = ConfigSnapshot(cfg)
        sshKeyDirs = []
        with pytest.raises(ValueError) as caught:
            MUT._ResolveSSHkeys(cfg, sshKeyDirs)
        assert caught.value.__suppress_context__
        assert (ConfigSnapshot(cfg) == before)
        resolved = MUT._ResolveSSHkeys(cfg, sshKeyDirs)
        assert (resolved is peer)
        assert (sshKeyDirs == [directory0, directory1])
        assert (ConfigSnapshot(cfg) == before)


def test_FxtVM_FailedBatchRetainsPreparedIdentityForCorrectedRetry(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    (directory, tempPath, privatePath, publicPath) = AddVMKeyDirectory(
        ctx, '/synthetic/prepared-identity',
    )
    SetVMPathText(publicPath, 'ssh-ed25519 prepared-public\n')
    selected = ctx.config(private=None, public=None, count=0, credType='None')
    resolved = ctx.config(
        private=str(privatePath), public=str(publicPath), count=0,
        credType='None',
    )
    generator = StartFxtVM(
        ctx, monkeypatch, selected, resolved, keyDirs=(directory,),
    )
    bundle = next(generator)
    primary = RuntimeError('synthetic-first-render-failure')
    retryVM = mock.Mock(name='retry-vm')
    retryResult = [retryVM]
    outcomes = [primary, retryResult]

    def Create(request: Any, fPars: Any, **kwargs: Any) -> list[Any]:
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException): raise outcome
        fPars.vmList.extend(outcome)
        return outcome

    ctx.vmCreate.side_effect = Create
    overrides = {'fxtInf': {'vmCfg': {'count': 1, 'namePfx': 'retry'}}}

    with pytest.raises(RuntimeError) as caught:
        bundle.vmCreate(**overrides)
    assert (caught.value is primary)
    assert (BoundFPars(ctx).sshKeyDirs == [directory])
    assert (BoundFPars(ctx).fxtCfgObj is resolved)
    assert (directory.cleanup.call_count == 0)
    result = bundle.vmCreate(**overrides)

    assert (result is retryResult)
    assert (bundle.vmList == [retryVM])
    assert (BoundFPars(ctx).sshKeyDirs == [directory])
    assert (ctx.vmCreate.call_args_list[1:] == [
        PerCallVMcreate(ctx, **overrides), PerCallVMcreate(ctx, **overrides),
    ])
    ctx.resolve.assert_called_once()
    MUT.sys.exc_info.return_value = (GeneratorExit, GeneratorExit(), None)
    generator.close()
    directory.cleanup.assert_called_once_with()


@pytest.mark.parametrize('partialFailure', [False, True])
def test_FxtVM_PytestLIFOClosesSSHBeforeOwnedFiles(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    partialFailure: bool,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    (directory, tempPath, privatePath, publicPath) = AddVMKeyDirectory(
        ctx, '/synthetic/lifo-identity',
    )
    SetVMPathText(publicPath, 'ssh-ed25519 lifo-public\n')
    selected = ctx.config(private=None, public=None, count=0)
    resolved = ctx.config(
        private=str(privatePath), public=str(publicPath), count=0,
    )
    generator = StartFxtVM(
        ctx, monkeypatch, selected, resolved, keyDirs=(directory,),
    )
    bundle = next(generator)
    assert (BoundFPars(ctx).vmList is bundle.vmList)
    vm = mock.Mock(name='owned-vm', sshKey=str(privatePath))
    bundle.vmList.append(vm)
    events = []

    def CloseVMs(vmList: list[Any], *args: Any, **kwargs: Any) -> None:
        events.extend(('ssh-close', item.sshKey) for item in vmList)
        if (getattr(ctx, 'cleanupPrimary', None) is not None):
            raise ctx.cleanupPrimary

    def CleanDirectory() -> None:
        events.append(('keydir-cleanup', directory))

    MUT.VM.CleanUpVMs.side_effect = CloseVMs
    directory.cleanup.side_effect = CleanDirectory
    if partialFailure:
        primary = RuntimeError('synthetic-body-primary')
        MUT.sys.exc_info.return_value = (RuntimeError, primary, None)
        with pytest.raises(RuntimeError) as caught:
            generator.throw(primary)
        assert (caught.value is primary)
    else:
        MUT.sys.exc_info.return_value = (GeneratorExit, GeneratorExit(), None)
        generator.close()

    assert (events == [
        ('ssh-close', str(privatePath)), ('keydir-cleanup', directory),
    ])
    MUT.VM.CleanUpVMs.assert_called_once()
    directory.cleanup.assert_called_once_with()


@pytest.mark.parametrize('primaryPhase', ['preparation', 'cleanup', 'none'])
def test_FxtVM_DirectoryCleanupFailurePreservesPrimaryAndSanitizesLogs(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    primaryPhase: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    (directory, tempPath, privatePath, publicPath) = AddVMKeyDirectory(
        ctx, '/synthetic/directory-cleanup',
    )
    selected = ctx.config(private=None, public=None, count=0)
    resolved = ctx.config(
        private=str(privatePath), public=str(publicPath), count=0,
    )
    secondary = OSError('synthetic-sensitive-directory-cleanup')
    directory.cleanup.side_effect = secondary
    generator = StartFxtVM(
        ctx, monkeypatch, selected, resolved, keyDirs=(directory,),
    )
    if (primaryPhase == 'preparation'):
        primary = ValueError('canned preparation failure')

        def FailResolve(fxtCfg: Any, sshKeyDirs: list[Any]) -> Any:
            sshKeyDirs.append(directory)
            raise primary

        ctx.resolve.side_effect = FailResolve
        MUT.sys.exc_info.return_value = (ValueError, primary, None)
        with pytest.raises(ValueError) as caught:
            next(generator)
        assert (caught.value is primary)
        ctx.vmCreate.assert_not_called()
    else:
        SetVMPathText(publicPath, 'ssh-ed25519 cleanup-public\n')
        bundle = next(generator)
        assert (BoundFPars(ctx).vmList is bundle.vmList)
        bundle.vmList.append(mock.Mock(name='cleanup-vm'))
        MUT.sys.exc_info.return_value = (None, None, None)
        if (primaryPhase == 'cleanup'):
            primary = RuntimeError('synthetic-primary-cleanup')
            MUT.VM.CleanUpVMs.side_effect = primary
            with pytest.raises(RuntimeError) as caught:
                next(generator)
            assert (caught.value is primary)
        else:
            with pytest.raises(RuntimeError) as caught:
                next(generator)
            assert (str(caught.value) == (
                'Could not clean up fixture resources or SSH key files.'
            ))
    directory.cleanup.assert_called_once_with()
    text = str(MUT.logs.mock_calls)
    assert ('synthetic-sensitive-directory-cleanup' not in text)
    if (primaryPhase == 'none'): assert caught.value.__suppress_context__
    if (primaryPhase == 'cleanup'):
        assert ('synthetic-primary-cleanup' in text)


@pytest.mark.parametrize('failure', ['generate-result', 'generate-timeout'])
def test__ResolveSSHkeys_KeyGenerationFailureSanitizesPartialFilesAndOutput(
    vmTestContext: SimpleNamespace,
    failure: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    (directory, unusedTemp, privatePath, publicPath) = AddVMKeyDirectory(
        ctx, f'/synthetic/keygen-failure-{failure}',
    )
    MUT.TemporaryDirectory.return_value = directory
    secret = 'PRIVATE KEY synthetic-secret-output'
    if (failure == 'generate-result'):
        ctx.cmdExec.return_value.Run.return_value = CommandResult(
            False, secret, 'synthetic-keygen-stderr',
        )
    else: ctx.cmdExec.return_value.Run.side_effect = TimeoutError(secret)
    cfg = ctx.config(private=None, public=None, count=0)
    before = ConfigSnapshot(cfg)
    sshKeyDirs = []

    with pytest.raises(ValueError, match='Key generation failed') as caught:
        MUT._ResolveSSHkeys(cfg, sshKeyDirs)

    assert (secret not in str(caught.value))
    assert caught.value.__suppress_context__
    assert (secret not in str(MUT.logs.mock_calls))
    assert (sshKeyDirs == [directory])
    assert (ConfigSnapshot(cfg) == before)
    assert (privatePath.chmod.call_count == 0)
    publicPath.write_text.assert_not_called()
    directory.cleanup.assert_not_called()
    cfg._merge.assert_not_called()


_REGISTERED_CFG = {
    'ns': 'context-ns',
    'fxtInf': {
        'vmCfg': {'namePfx': 'registered-vm'},
        'gstCfg': {
            'imgSrc': {
                'CDI': {
                    'credInfo': {
                        'vaultInfo': {
                            'file--AccKeyId': 'access',
                            'file--AccKeySecret': 'relative-secret',
                        },
                    },
                },
            },
            'usrCrd': {
                'sshKey': {'prv': '$VMCTX__KEY_DIR/private-key', 'pub': None},
            },
        },
    },
}


@pytest.mark.parametrize('registeredParam', [_REGISTERED_CFG])
def test_FxtVM_RegisteredFixtureSupportsPartialNestedNamedDictBatches(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    registeredParam: dict[str, Any],
) -> None:
    ctx = VMKeyContext(vmTestContext)
    paramBefore = json.dumps(registeredParam)
    privateInput = (
        registeredParam['fxtInf']['gstCfg']['usrCrd']['sshKey']['prv']
    )
    privateValue = '/synthetic/registered-private'
    (directory, unusedTemp, unusedPrivate, publicPath) = AddVMKeyDirectory(
        ctx, '/synthetic/registered-owned', 'private-key',
    )
    SetVMPathText(publicPath, 'ssh-ed25519 registered-public\n')
    selected = ctx.config(
        private=privateInput, public=None, count=1,
        credType='K8s', namePfx='registered-vm',
    )
    resolved = ctx.config(
        private=privateValue, public=str(publicPath), count=1,
        credType='K8s', namePfx='registered-vm',
    )
    for cfg in (selected, resolved):
        cfg.ns = registeredParam['ns']
    initialVM = mock.Mock(name='initial-vm')
    batchVM0 = mock.Mock(name='batch-vm-0')
    batchVM1 = mock.Mock(name='batch-vm-1')
    batchResult = [batchVM0, batchVM1]
    results = [[initialVM], batchResult]

    def Create(request: Any, fPars: Any, **kwargs: Any) -> list[Any]:
        result = results.pop(0)
        fPars.vmList.extend(result)
        return result

    generator = StartFxtVM(
        ctx, monkeypatch, selected, resolved, registeredParam,
        keyDirs=(directory,),
    )
    ctx.vmCreate.side_effect = Create
    selectedBefore = ConfigSnapshot(selected)
    defaultsBefore = ConfigSnapshot(MUT._DEF__FXT_CFG)
    bundle = next(generator)
    batchArgs = {'fxtInf': {'vmCfg': {
        'namePfx': 'registered-batch', 'count': 2,
    }}}
    batchBefore = json.dumps(batchArgs)

    created = bundle.vmCreate(**batchArgs)

    assert (created is batchResult)
    assert (bundle.vmList == [initialVM, batchVM0, batchVM1])
    assert (ctx.namedDict.call_args_list[0] == mock.call(**registeredParam))
    ctx.resolve.assert_called_once_with(selected, BoundFPars(ctx).sshKeyDirs)
    assert (BoundFPars(ctx).fxtCfgObj is resolved)
    assert (ctx.vmCreate.call_args_list[1:] == [
        PerCallVMcreate(ctx, **batchArgs),
    ])
    assert (json.dumps(registeredParam) == paramBefore)
    assert (json.dumps(batchArgs) == batchBefore)
    assert (ConfigSnapshot(selected) == selectedBefore)
    assert (ConfigSnapshot(MUT._DEF__FXT_CFG) == defaultsBefore)
    assert (BoundFPars(ctx).vmList is bundle.vmList)
    MUT.sys.exc_info.return_value = (GeneratorExit, GeneratorExit(), None)
    generator.close()
    MUT.VM.CleanUpVMs.assert_called_once()
    directory.cleanup.assert_called_once_with()


@pytest.mark.parametrize(
    'cfgMode', ['implicit', 'partial', 'complete', 'custom-user'],
)
def test_FxtVM_ImplicitDefaultPathsAreDetachedBeforeFactoryBinding(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    cfgMode: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    (directory, tempPath, privatePath, publicPath) = AddVMKeyDirectory(
        ctx, f'/synthetic/default-paths-{cfgMode}',
    )
    selected = ctx.config(private=None, public=None, count=0)
    if (cfgMode == 'custom-user'):
        selected.fxtInf.gstCfg.usrCrd.sshUsr = 'custom-user'
    param = {}
    if (cfgMode == 'partial'):
        param = {'fxtInf': {'gstCfg': {'usrCrd': {'sshKey': {'prv': None}}}}}
    elif (cfgMode == 'complete'):
        param = {'fxtInf': {'gstCfg': {'usrCrd': {
            'sshKey': {'prv': None, 'pub': None},
        }}}}
    elif (cfgMode == 'custom-user'):
        param = {'fxtInf': {'gstCfg': {'usrCrd': {
            'sshUsr': 'custom-user',
            'sshKey': {'prv': None, 'pub': None},
        }}}}
    resolved = ctx.config(
        private=str(privatePath), public=str(publicPath), count=0,
    )
    SetVMPathText(publicPath, 'ssh-ed25519 detached-public\n')
    generator = StartFxtVM(
        ctx, monkeypatch, selected, resolved, param, keyDirs=(directory,),
    )
    selectedBefore = ConfigSnapshot(selected)
    defaultsBefore = ConfigSnapshot(MUT._DEF__FXT_CFG)
    bundle = next(generator)

    assert (ctx.namedDict.call_args_list[0] == mock.call(**param))
    ctx.resolve.assert_called_once_with(selected, BoundFPars(ctx).sshKeyDirs)
    assert (ConfigSnapshot(selected) == selectedBefore)
    assert (ConfigSnapshot(MUT._DEF__FXT_CFG) == defaultsBefore)
    assert (BoundFPars(ctx).fxtCfgObj is resolved)
    assert (bundle.vmCreate() == [])
    MUT.sys.exc_info.return_value = (GeneratorExit, GeneratorExit(), None)
    generator.close()
    directory.cleanup.assert_called_once_with()


@pytest.mark.parametrize(
    ('publicText', 'encodedPublic'), [
        ('untyped-content', 'dW50eXBlZC1jb250ZW50'),
        ('arbitrary\nnonempty\n', 'YXJiaXRyYXJ5Cm5vbmVtcHR5Cg=='),
        ('  nonempty text \n\n', 'ICBub25lbXB0eSB0ZXh0IAoK'),
        ('arbitrary\r\nnonempty\r\n', 'YXJiaXRyYXJ5DQpub25lbXB0eQ0K'),
        ('arbitrary\rnonempty\r', 'YXJiaXRyYXJ5DW5vbmVtcHR5DQ=='),
    ],
)
@pytest.mark.parametrize('pathForm', ['literal', 'env', 'home', 'env-home'])
def test_FxtVM_SuppliedPublicTextAndPathsPassThroughUnchanged(
    vmTestContext: SimpleNamespace,
    publicText: str,
    encodedPublic: str,
    pathForm: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    privateValue = '/synthetic/supplied-private'
    expandedPublic = '/synthetic/supplied-public'
    if (pathForm == 'literal'): publicValue = expandedPublic
    elif (pathForm == 'env'):
        publicValue = '${VMCTX__KEY_DIR}/provided-public'
        ctx.expansions[publicValue] = expandedPublic
    elif (pathForm == 'home'):
        publicValue = '~/provided-public'
        homePath = ctx.addPath(publicValue, name='provided-public')
        homePath.expanduser.return_value = ctx.addPath(
            expandedPublic, name='provided-public',
        )
    else:
        publicValue = '$VMCTX__PUBLIC_HOME'
        ctx.expansions[publicValue] = '~/provided-public'
        homePath = ctx.addPath('~/provided-public', name='provided-public')
        homePath.expanduser.return_value = ctx.addPath(
            expandedPublic, name='provided-public',
        )
    privatePath = ctx.addPath(privateValue, name='supplied-private')
    publicPath = ctx.addPath(expandedPublic, name='supplied-public')
    cfg = ctx.config(private=privateValue, public=publicValue, count=1)
    resolved = ctx.config(private=privateValue, public=expandedPublic, count=1)
    QueueVMConfigMerges(cfg, resolved)
    sshKeyDirs = []
    before = ConfigSnapshot(cfg)
    normalized = MUT._ResolveSSHkeys(cfg, sshKeyDirs)
    QueueVMConfigMerges(normalized, normalized)
    SetVMPathText(publicPath, publicText)
    MUT.base64.b64encode.return_value = encodedPublic.encode('ascii')
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=normalized, vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=sshKeyDirs,
    )

    MUT.VMcreate(
        VMRequest(ctx), fPars, ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory,
    )

    assert (ConfigSnapshot(cfg) == before)
    assert (sshKeyDirs == [])
    assert (normalized.fxtInf.gstCfg.usrCrd.sshKey.pub == expandedPublic)
    assert (MUT.base64.b64encode.call_args == mock.call(
        publicText.encode('utf-8'),
    ))
    AssertVMUserCredentialAdds(ctx, normalized)
    assert (MUT.YAMLcfg.Render.call_args.args[1]['sshPublicKeyB64'] == (
        encodedPublic
    ))
    assert (privatePath.expanduser.call_count == 1)
    assert (publicPath.open.call_count == 1)


def test_FxtVM_DerivedRSAPublicKeyUsesSuppliedPEMBasename(
    vmTestContext: SimpleNamespace,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    privateValue = '/synthetic/source/openshift-qe.pem'
    privatePath = ctx.addPath(privateValue, name='openshift-qe.pem')
    (directory, tempPath, unusedPrivate, publicPath) = AddVMKeyDirectory(
        ctx, '/synthetic/rsa-derived', 'openshift-qe.pem',
    )
    tempPath.__truediv__.return_value = publicPath
    MUT.TemporaryDirectory.return_value = directory
    ctx.cmdExec.return_value.Run.return_value = CommandResult(
        True, 'ssh-rsa supplied-pem-public\n',
    )
    cfg = ctx.config(private=privateValue, public=None)
    resolvedCfg = ctx.config(
        private=privateValue,
        public='/synthetic/rsa-derived/openshift-qe.pem.pub',
    )
    QueueVMConfigMerges(cfg, resolvedCfg)
    sshKeyDirs = []

    resolved = MUT._ResolveSSHkeys(cfg, sshKeyDirs)

    assert (resolved is resolvedCfg)
    assert (resolved.fxtInf.gstCfg.usrCrd.sshKey.prv == privateValue)
    assert (resolved.fxtInf.gstCfg.usrCrd.sshKey.pub == str(publicPath))
    assert (publicPath.name == 'openshift-qe.pem.pub')
    assert (sshKeyDirs == [directory])
    publicPath.write_text.assert_called_once_with(
        'ssh-rsa supplied-pem-public\n', encoding='utf-8',
    )
    assert (privatePath.expanduser.call_count == 1)


@pytest.mark.parametrize(
    'setupMode', ['generate', 'public-only', 'derive', 'read'],
)
@pytest.mark.parametrize(
    'keyMode', ['generate', 'public-only', 'derive', 'read'],
)
def test_FxtVM_PerCallKeyModesPreserveBaseAndEarlierIdentities(
    vmTestContext: SimpleNamespace,
    setupMode: str,
    keyMode: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    (setupInput, basePrivate, basePublic, setupDir, setupCmd) = (
        VMKeyModeInputs(ctx, setupMode, 'setup')
    )
    setupCfg = ctx.config(
        private=setupInput['prv'], public=setupInput['pub'], count=0,
    )
    base = ctx.config(private=basePrivate, public=basePublic, count=0)
    QueueVMConfigMerges(setupCfg, base)
    (firstInput, firstPrivate, firstPublic, firstDir, firstCmd) = (
        VMKeyModeInputs(ctx, keyMode, 'first-call')
    )
    firstCfg = ctx.config(
        private=firstInput['prv'], public=firstInput['pub'], count=0,
    )
    firstResolved = ctx.config(
        private=firstPrivate, public=firstPublic, count=0,
    )
    laterPrivate = '/synthetic/later-private'
    laterPublic = '/synthetic/later-public'
    ctx.addPath(laterPrivate, name='later-private')
    ctx.addPath(laterPublic, name='later-public')
    laterCfg = ctx.config(private=laterPrivate, public=laterPublic, count=0)
    laterResolved = ctx.config(
        private=laterPrivate, public=laterPublic, count=0,
    )
    directories = [
        directory for directory in (setupDir, firstDir)
        if (directory is not None)
    ]
    if directories: MUT.TemporaryDirectory.side_effect = directories
    commandCount = int(setupCmd) + int(firstCmd)
    if commandCount:
        ctx.cmdExec.return_value.Run.side_effect = [
            CommandResult(True, 'ssh-ed25519 canned-derived-public\n')
            for _ in range(commandCount)
        ]
    sshKeyDirs = []
    base = MUT._ResolveSSHkeys(setupCfg, sshKeyDirs)
    baseBefore = ConfigSnapshot(base)
    QueueVMConfigMerges(base, firstCfg, laterCfg)
    QueueVMConfigMerges(firstCfg, firstResolved)
    QueueVMConfigMerges(laterCfg, laterResolved)
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base, vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=sshKeyDirs,
    )
    request = VMRequest(ctx)
    firstArgs = {'fxtInf': {'vmCfg': {'count': 0}, 'gstCfg': {
        'usrCrd': {'sshKey': firstInput},
    }}}
    firstArgsBefore = json.dumps(firstArgs)
    laterArgs = {'fxtInf': {'vmCfg': {'count': 0}, 'gstCfg': {
        'usrCrd': {'sshKey': {'prv': laterPrivate, 'pub': laterPublic}},
    }}}
    firstKeyBefore = ConfigSnapshot(firstResolved)

    assert (MUT.VMcreate(
        request, fPars, ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory, **firstArgs,
    ) == [])
    assert (firstResolved.fxtInf.gstCfg.usrCrd.sshKey.prv == firstPrivate)
    assert (firstResolved.fxtInf.gstCfg.usrCrd.sshKey.pub == firstPublic)
    assert (MUT.VMcreate(
        request, fPars, ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory, **laterArgs,
    ) == [])

    assert (ConfigSnapshot(base) == baseBefore)
    assert (ConfigSnapshot(firstResolved) == firstKeyBefore)
    assert (laterResolved.fxtInf.gstCfg.usrCrd.sshKey.prv == laterPrivate)
    assert (laterResolved.fxtInf.gstCfg.usrCrd.sshKey.pub == laterPublic)
    assert (json.dumps(firstArgs) == firstArgsBefore)
    assert (len(sshKeyDirs) == len(directories))
    assert (ctx.cmdExec.return_value.Run.call_count == commandCount)
    assert (ctx.ocFactory.call_count == 0)


@pytest.mark.parametrize('pathForm', ['env', 'home', 'env-home'])
@pytest.mark.parametrize(
    'publicText', ['ssh-ed25519 opaque\r\n', ' opaque\r'],
)
def test_FxtVM_PerCallExpandedPathsPreserveLiteralPublicText(
    vmTestContext: SimpleNamespace,
    pathForm: str,
    publicText: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    privateValue = {
        'env': '${VMCTX__KEY_DIR}/private-key',
        'home': '~/private-key',
        'env-home': '$VMCTX__KEY_HOME/private-key',
    }[pathForm]
    publicValue = {
        'env': '${VMCTX__KEY_DIR}/public-key',
        'home': '~/public-key',
        'env-home': '$VMCTX__PUBLIC_HOME',
    }[pathForm]
    privateExpanded = '/synthetic/expanded/private-key'
    publicExpanded = '/synthetic/expanded/public-key'
    if (pathForm == 'env'):
        ctx.expansions[privateValue] = privateExpanded
        ctx.expansions[publicValue] = publicExpanded
        ctx.addPath(privateExpanded, name='private-key')
        ctx.addPath(publicExpanded, name='public-key')
    elif (pathForm == 'home'):
        privatePath = ctx.addPath(privateValue, name='private-key')
        publicPath = ctx.addPath(publicValue, name='public-key')
        privatePath.expanduser.return_value = ctx.addPath(
            privateExpanded, name='private-key',
        )
        publicPath.expanduser.return_value = ctx.addPath(
            publicExpanded, name='public-key',
        )
    else:
        ctx.expansions[privateValue] = '~/private-key'
        ctx.expansions[publicValue] = '~/public-key'
        privatePath = ctx.addPath('~/private-key', name='private-key')
        publicPath = ctx.addPath('~/public-key', name='public-key')
        privatePath.expanduser.return_value = ctx.addPath(
            privateExpanded, name='private-key',
        )
        publicPath.expanduser.return_value = ctx.addPath(
            publicExpanded, name='public-key',
        )
    basePrivate = '/synthetic/base-private'
    basePublic = '/synthetic/base-public'
    base = ctx.config(private=basePrivate, public=basePublic, count=0)
    callCfg = ctx.config(private=privateValue, public=publicValue, count=1)
    resolved = ctx.config(
        private=privateExpanded, public=publicExpanded, count=1,
    )
    QueueVMConfigMerges(base, callCfg)
    QueueVMConfigMerges(callCfg, resolved)
    publicPath = ctx.paths[publicExpanded]
    SetVMPathText(publicPath, publicText)
    fPars = MUT.FuncParsVMcreate(
        fxtCfgObj=base, vmList=[],
        ownResTrk=SimpleNamespace(NSs=[], Secrets=[], VMs=[]),
        sshKeyDirs=[],
    )
    overrides = {'fxtInf': {'vmCfg': {'count': 1}, 'gstCfg': {
        'usrCrd': {'sshKey': {'prv': privateValue, 'pub': publicValue}},
    }}}
    baseBefore = ConfigSnapshot(base)

    MUT.VMcreate(
        VMRequest(ctx), fPars, ocCLI=ctx.ocFactory,
        virtCtlCLI=ctx.virtCtlFactory, **overrides,
    )

    assert (ConfigSnapshot(base) == baseBefore)
    assert (resolved.fxtInf.gstCfg.usrCrd.sshKey.prv == privateExpanded)
    assert (resolved.fxtInf.gstCfg.usrCrd.sshKey.pub == publicExpanded)
    assert (MUT.base64.b64encode.call_args == mock.call(
        publicText.encode('utf-8'),
    ))
    AssertVMUserCredentialAdds(ctx, resolved)
    assert (ctx.vmFactory.call_args.kwargs['sshKey'] == (
        ctx.UserCredentials.Add.return_value['sshKey']
    ))
    assert (MUT.TemporaryDirectory.call_count == 0)


def test_FxtVM_PerCallListOverridesAreAppliedExactlyOnce(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    privateValue = '/synthetic/list-private'
    publicValue = '/synthetic/list-public'
    ctx.addPath(privateValue, name='list-private')
    publicPath = ctx.addPath(publicValue, name='list-public')
    SetVMPathText(publicPath, 'ssh-ed25519 list-public\n')
    selected = ctx.config(private=privateValue, public=publicValue, count=0)
    resolved = ctx.config(private=privateValue, public=publicValue, count=0)
    selected.tags = ['setup']
    resolved.tags = ['setup']
    generator = StartFxtVM(ctx, monkeypatch, selected, resolved)
    bundle = next(generator)
    baseTags = resolved.tags
    overrideTags = ['override']
    override = {'tags': overrideTags, 'fxtInf': {'vmCfg': {'count': 0}}}
    overrideBefore = json.dumps(override)

    assert (bundle.vmCreate(**override) == [])

    assert (ctx.vmCreate.call_args_list[1:] == [
        PerCallVMcreate(ctx, **override),
    ])
    assert (ctx.vmCreate.call_args.kwargs['tags'] is overrideTags)
    assert ((resolved.tags is baseTags) and (baseTags == ['setup']))
    assert (overrideTags == ['override'])
    assert (json.dumps(override) == overrideBefore)
    assert (BoundFPars(ctx).fxtCfgObj is resolved)
    generator.close()


def test_FxtVM_PerCallBlankPrivateFailsBeforeEffectsAndRetries(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    privateValue = '/synthetic/base-private'
    publicValue = '/synthetic/base-public'
    ctx.addPath(privateValue, name='base-private')
    publicPath = ctx.addPath(publicValue, name='base-public')
    SetVMPathText(publicPath, 'ssh-ed25519 base-public\n')
    selected = ctx.config(private=privateValue, public=publicValue, count=0)
    resolved = ctx.config(private=privateValue, public=publicValue, count=0)
    generator = StartFxtVM(ctx, monkeypatch, selected, resolved)
    bundle = next(generator)
    failure = ValueError(
        'The `fxtInf.gstCfg.usrCrd.sshKey.prv` can not be empty string.'
    )
    ctx.vmCreate.side_effect = [failure, []]
    overrides = {'fxtInf': {'vmCfg': {'count': 0}, 'gstCfg': {
        'usrCrd': {'sshKey': {'prv': ' '}},
    }}}
    overrideBefore = json.dumps(overrides)

    with pytest.raises(ValueError) as caught:
        bundle.vmCreate(**overrides)

    assert (caught.value is failure)
    assert (json.dumps(overrides) == overrideBefore)
    assert (bundle.vmList == BoundFPars(ctx).ownResTrk.VMs == [])
    MUT.VM.CleanUpVMs.assert_not_called()
    retry = {'fxtInf': {'vmCfg': {'count': 0}, 'gstCfg': {
        'usrCrd': {'sshKey': {
            'prv': '/synthetic/retry-private',
            'pub': '/synthetic/retry-public',
        }},
    }}}

    assert (bundle.vmCreate(**retry) == [])
    assert (ctx.vmCreate.call_args_list[1:] == [
        PerCallVMcreate(ctx, **overrides), PerCallVMcreate(ctx, **retry),
    ])
    assert (BoundFPars(ctx).fxtCfgObj is resolved)
    generator.close()


@pytest.mark.parametrize('prv', ['', '.'])
def test_FxtVM_DotPrivateOverrideIsForwardedLiterally(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    prv: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    basePrivate = '/synthetic/base-private'
    basePublic = '/synthetic/base-public'
    ctx.addPath(basePrivate, name='base-private')
    basePublicPath = ctx.addPath(basePublic, name='base-public')
    SetVMPathText(basePublicPath, 'ssh-ed25519 base-public\n')
    selected = ctx.config(private=basePrivate, public=basePublic, count=0)
    resolved = ctx.config(private=basePrivate, public=basePublic, count=0)
    generator = StartFxtVM(ctx, monkeypatch, selected, resolved)
    bundle = next(generator)
    assert (BoundFPars(ctx).vmList is bundle.vmList)
    overrides = {'fxtInf': {'vmCfg': {'count': 1}, 'gstCfg': {
        'usrCrd': {'sshKey': {'prv': prv, 'pub': 'literal.pub'}},
    }}}
    vm = mock.Mock(name='dot-private-vm')

    if not prv:
        failure = ValueError(
            'The `fxtInf.gstCfg.usrCrd.sshKey.prv` can not be empty string.'
        )
        ctx.vmCreate.side_effect = failure
        with pytest.raises(ValueError) as caught:
            bundle.vmCreate(**overrides)
        assert (caught.value is failure)
    else:
        ctx.vmCreate.return_value = [vm]
        assert (bundle.vmCreate(**overrides) == [vm])
    assert (ctx.vmCreate.call_args_list[1:] == [
        PerCallVMcreate(ctx, **overrides),
    ])
    assert (ctx.vmCreate.call_args.kwargs['fxtInf'] is overrides['fxtInf'])
    MUT.sys.exc_info.return_value = (GeneratorExit, GeneratorExit(), None)
    generator.close()


def test_FxtVM_PerCallResolutionFailureIsPrivateOwnedAndRetryable(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    basePrivate = '/synthetic/base-private'
    basePublic = '/synthetic/base-public'
    ctx.addPath(basePrivate, name='base-private')
    basePublicPath = ctx.addPath(basePublic, name='base-public')
    SetVMPathText(basePublicPath, 'ssh-ed25519 base-public\n')
    selected = ctx.config(private=basePrivate, public=basePublic, count=0)
    resolved = ctx.config(private=basePrivate, public=basePublic, count=0)
    generator = StartFxtVM(ctx, monkeypatch, selected, resolved)
    bundle = next(generator)
    (directory, unusedTemp, privatePath, unusedPublic) = AddVMKeyDirectory(
        ctx, '/synthetic/per-call-failure',
    )
    failure = ValueError(
        '`fxtInf.gstCfg.usrCrd.sshKey`: Key generation failed.'
    )

    def FailResolution(request: Any, fPars: Any, **kwargs: Any) -> list[Any]:
        fPars.sshKeyDirs.append(directory)
        raise failure

    ctx.vmCreate.side_effect = FailResolution
    overrides = {'fxtInf': {'vmCfg': {'count': 0}, 'gstCfg': {
        'usrCrd': {'sshKey': {'prv': None, 'pub': None}},
    }}}

    with pytest.raises(ValueError) as caught:
        bundle.vmCreate(**overrides)

    assert (caught.value is failure)
    assert (BoundFPars(ctx).sshKeyDirs == [directory])
    assert (BoundFPars(ctx).fxtCfgObj is resolved)
    assert (directory.cleanup.call_count == 0)
    ctx.vmCreate.side_effect = None
    retryPrivate = '/synthetic/retry-private'
    retryPublic = '/synthetic/retry-public'
    retry = {'fxtInf': {'vmCfg': {'count': 0}, 'gstCfg': {
        'usrCrd': {'sshKey': {'prv': retryPrivate, 'pub': retryPublic}},
    }}}

    assert (bundle.vmCreate(**retry) == [])
    assert (ctx.vmCreate.call_args_list[1:] == [
        PerCallVMcreate(ctx, **overrides), PerCallVMcreate(ctx, **retry),
    ])
    assert (BoundFPars(ctx).sshKeyDirs == [directory])
    MUT.sys.exc_info.return_value = (GeneratorExit, GeneratorExit(), None)
    generator.close()
    directory.cleanup.assert_called_once_with()


@pytest.mark.parametrize('primaryPhase', ['body', 'cleanup', 'none'])
def test_FxtVM_MultipleOwnedDirectoriesFollowAllSSHCleanupAndPreservePrimary(
    vmTestContext: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    primaryPhase: str,
) -> None:
    ctx = VMKeyContext(vmTestContext)
    basePrivate = '/synthetic/base-private'
    basePublic = '/synthetic/base-public'
    ctx.addPath(basePrivate, name='base-private')
    basePublicPath = ctx.addPath(basePublic, name='base-public')
    SetVMPathText(basePublicPath, 'ssh-ed25519 base-public\n')
    selected = ctx.config(private=basePrivate, public=basePublic, count=0)
    resolved = ctx.config(private=basePrivate, public=basePublic, count=0)
    generator = StartFxtVM(ctx, monkeypatch, selected, resolved)
    bundle = next(generator)
    assert (BoundFPars(ctx).vmList is bundle.vmList)
    (firstDir, firstTemp, firstPrivate, unusedFirstPublic) = AddVMKeyDirectory(
        ctx, '/synthetic/multi-generated',
    )
    (secondDir, unusedTemp, unusedPrivate, unusedPublic) = AddVMKeyDirectory(
        ctx, '/synthetic/multi-derived', 'alternate.pem',
    )
    secondPrivate = ctx.addPath(
        '/synthetic/multi-source/alternate.pem', name='alternate.pem',
    )
    batchDirs = [firstDir, secondDir]

    def Create(request: Any, fPars: Any, **kwargs: Any) -> list[Any]:
        fPars.sshKeyDirs.append(batchDirs.pop(0))
        return []

    ctx.vmCreate.side_effect = Create
    firstArgs = {'fxtInf': {'vmCfg': {'count': 0}, 'gstCfg': {
        'usrCrd': {'sshKey': {'prv': None, 'pub': None}},
    }}}
    secondArgs = {'fxtInf': {'vmCfg': {'count': 0}, 'gstCfg': {
        'usrCrd': {'sshKey': {'prv': str(secondPrivate), 'pub': None}},
    }}}
    assert (bundle.vmCreate(**firstArgs) == [])
    assert (bundle.vmCreate(**secondArgs) == [])
    assert (BoundFPars(ctx).sshKeyDirs == [firstDir, secondDir])
    vm0 = mock.Mock(name='multi-vm-0', sshKey=str(firstPrivate))
    vm1 = mock.Mock(name='multi-vm-1', sshKey=str(secondPrivate))
    bundle.vmList.extend([vm0, vm1])
    events = []

    def CloseVMs(vmList: list[Any], *args: Any, **kwargs: Any) -> None:
        events.extend(('ssh-close', item.sshKey) for item in vmList)
        if (getattr(ctx, 'cleanupPrimary', None) is not None):
            raise ctx.cleanupPrimary

    def CleanupDirectory(directory: Any, fail: bool) -> Any:
        def Cleanup() -> None:
            events.append(('keydir-cleanup', directory))
            if fail: raise OSError('synthetic-sensitive-cleanup-error')
        return Cleanup

    MUT.VM.CleanUpVMs.side_effect = CloseVMs
    firstDir.cleanup.side_effect = CleanupDirectory(firstDir, True)
    secondDir.cleanup.side_effect = CleanupDirectory(secondDir, False)
    if (primaryPhase == 'body'):
        primary = RuntimeError('synthetic-body-primary')
        MUT.sys.exc_info.return_value = (RuntimeError, primary, None)
        with pytest.raises(RuntimeError) as caught:
            generator.throw(primary)
        assert (caught.value is primary)
    elif (primaryPhase == 'cleanup'):
        primary = RuntimeError('synthetic-cleanup-primary')
        ctx.cleanupPrimary = primary
        MUT.sys.exc_info.return_value = (None, None, None)
        with pytest.raises(RuntimeError) as caught:
            next(generator)
        assert (caught.value is primary)
    else:
        MUT.sys.exc_info.return_value = (None, None, None)
        with pytest.raises(RuntimeError, match='Could not clean up') as caught:
            next(generator)

    assert (events[:2] == [
        ('ssh-close', str(firstPrivate)),
        ('ssh-close', str(secondPrivate)),
    ])
    assert (events[2:] == [
        ('keydir-cleanup', firstDir), ('keydir-cleanup', secondDir),
    ])
    firstDir.cleanup.assert_called_once_with()
    secondDir.cleanup.assert_called_once_with()
    assert ('synthetic-sensitive-cleanup-error' not in (
        str(caught.value) + str(MUT.logs.mock_calls)
    ))
    if (primaryPhase == 'none'): assert caught.value.__suppress_context__


def test_FuncParsVMcreate_OwnershipTypesUseConcreteCLI() -> None:
    annotation = MUT.FuncParsVMcreate.__annotations__['ownResTrk']
    (keyType, recordList) = get_args(annotation)
    (recordType,) = get_args(recordList)
    (clientType, refType, uidType) = get_args(recordType)
    assert (get_origin(annotation).__name__ == 'NamedDict')
    assert (get_origin(recordList) is list)
    assert (get_origin(recordType) is tuple)
    assert (keyType is refType is uidType is str)
    assert (clientType.__name__ == 'OCcli')
    assert (clientType.__module__ == 'libs.OpenShift.OCP.CLI.OC')
