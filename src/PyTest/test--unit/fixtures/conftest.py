'''VM-only canned collaborators for the `fixtures.VM` test subject.'''
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

import fixtures.VM as MUT


class RootConfig(SimpleNamespace):
    '''
    Test config forwarding value.

    Operators only forward to `_merge`.
    '''

    def __or__(self, other: Any) -> Any:
        return self._merge(other)

    def __ior__(self, other: Any) -> Any:
        return self._merge(other)


@pytest.fixture(name='vmTestContext')
def VMTestContext(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    '''Return plain config data, lexical path mocks, and MUT call controls.'''
    paths: dict[str, Any] = {}
    expansions: dict[str, str] = {}
    namedDictMade: list[SimpleNamespace] = []

    def AddPath(value: str, name: str = '') -> Any:
        '''Register a lexical path mock; this never accesses the filesystem.'''
        path = mock.MagicMock(name=f'Path[{value}]')
        path.__str__.return_value = value
        path.name = name
        path.expanduser.return_value = path
        path.resolve.return_value = path
        path.absolute.return_value = path
        path.exists.return_value = False
        path.is_dir.return_value = False
        path.iterdir.return_value = []
        path.read_text.return_value = ''
        path.read_bytes.return_value = b''
        path.stat.return_value = SimpleNamespace(st_mode=0)
        paths[value] = path
        return path

    def PathFor(value: Any) -> Any:
        '''Return only a path mock explicitly registered by this test.'''
        key = str(value)
        if (key not in paths):
            raise AssertionError(f'Unregistered lexical path: {key}')
        return paths[key]

    def Expand(value: str) -> str:
        '''Apply only explicit substitutions; otherwise keep the literal.'''
        return expansions.get(value, value)

    def MakeNamedDict(*args: Any, **kwargs: Any) -> SimpleNamespace:
        '''Make a shallow namespace token; do not merge or recurse.'''
        if kwargs: result = SimpleNamespace(**kwargs)
        elif args: result = SimpleNamespace(payload=args[0].copy())
        else: result = SimpleNamespace()
        namedDictMade.append(result)
        return result

    def Config(
        private: str = '/synthetic/private-key',
        public: str | None = '/synthetic/public-key',
        *,
        count: int = 1,
        startVM: bool = False,
        reuseExisting: bool = False,
        credType: str = 'None',
        url: str = 'https://synthetic.invalid/image.qcow2',
        cfgTmpl: str = 'unit-vm.yaml',
        namePfx: str = 'unit-vm',
        ctxData: dict[str, Any] | None = None,
    ) -> RootConfig:
        '''
        Build plain per-case test data.

        This helper never calls `MUT.NamedDict`.
        '''
        return RootConfig(
            _merge=mock.Mock(name='rootMerge'),
            kCfg='/synthetic/kubeconfig',
            cliCfg=SimpleNamespace(ocCfg={}, vcCfg={}),
            ns='unit-namespace',
            fxtInf=SimpleNamespace(
                vmCfg=SimpleNamespace(
                    cfgTmpl=cfgTmpl,
                    ctxData={} if (ctxData is None) else ctxData,
                    namePfx=namePfx,
                    count=count,
                    startVM=startVM,
                    reuseExisting=reuseExisting,
                    timeout=SimpleNamespace(
                        vmCreate='15m', dvImport='30m', vmStart='15m',
                        vmRemove='3m',
                    ),
                ),
                gstCfg=SimpleNamespace(
                    imgSrc=SimpleNamespace(
                        CDI=SimpleNamespace(
                            type='AWS-S3',
                            credType=credType,
                            credInfo=SimpleNamespace(
                                name='unit-image-secret',
                                vaultType='LocalFile',
                                vaultInfo={
                                    'file--AccKeyId': '/synthetic/access',
                                    'file--AccKeySecret': '/synthetic/secret',
                                },
                            ),
                            url=url,
                        ),
                    ),
                    usrCrd=SimpleNamespace(
                        sshUsr='unit-user',
                        sshKey=SimpleNamespace(prv=private, pub=public),
                    ),
                ),
            ),
            testPars=SimpleNamespace(),
        )

    namedDict = mock.Mock(side_effect=MakeNamedDict, name='NamedDict')
    tempDir = mock.Mock(name='TemporaryDirectory')
    tempDir.name = '/synthetic/temp'
    AddPath(tempDir.name)
    cmdExec = mock.Mock(name='CmdExec')
    cmdExec.return_value.Run.return_value = SimpleNamespace(
        success=True, stdout='ssh-ed25519 canned-public\n',
    )
    vmFactory = mock.Mock(name='VM')
    ocFactory = mock.Mock(name='OCcli')
    ocFactory.GetResUID.return_value = (True, '', '')
    ocFactory.CleanUpRes.return_value = None
    virtCtlFactory = mock.Mock(name='VirtCtlCLI')
    secretSpecMismatch = type('SecretSpecMismatch', (Exception,), {})

    monkeypatch.setattr(MUT, 'NamedDict', namedDict)
    monkeypatch.setattr(MUT, 'Path', mock.Mock(side_effect=PathFor))
    monkeypatch.setattr(
        MUT, 'TemporaryDirectory', mock.Mock(return_value=tempDir),
    )
    monkeypatch.setattr(MUT, 'CmdExec', cmdExec)
    monkeypatch.setattr(MUT, 'VM', vmFactory)
    monkeypatch.setattr(MUT, 'OCcli', ocFactory)
    monkeypatch.setattr(MUT, 'ResCfg', mock.Mock(name='ResCfg'))
    monkeypatch.setattr(MUT, 'OwnInf', mock.Mock(name='OwnInf'))
    monkeypatch.setattr(MUT, 'YAMLcfg', SimpleNamespace(
        Render=mock.Mock(return_value='canned-rendered-vm-yaml'),
    ))
    monkeypatch.setattr(MUT, 'VirtualMachine', SimpleNamespace(
        Create=mock.Mock(return_value={'metadata': {'uid': 'canned-vm-uid'}}),
    ))
    monkeypatch.setattr(MUT, 'Namespace', SimpleNamespace(Create=mock.Mock()))
    monkeypatch.setattr(
        MUT, 'Secret',
        SimpleNamespace(Create=mock.Mock(), SpecMismatch=secretSpecMismatch),
    )
    monkeypatch.setattr(
        MUT, 'base64',
        SimpleNamespace(b64encode=mock.Mock(return_value=b'canned-base64')),
    )
    urlParts = SimpleNamespace(scheme='https', hostname='synthetic.invalid')
    monkeypatch.setattr(MUT, 'urlsplit', mock.Mock(return_value=urlParts))
    monkeypatch.setattr(MUT, 'os', SimpleNamespace(path=SimpleNamespace(
        expandvars=mock.Mock(side_effect=Expand),
        expanduser=mock.Mock(side_effect=Expand),
    )))
    monkeypatch.setattr(
        MUT, 'sys',
        SimpleNamespace(exc_info=mock.Mock(return_value=(None, None, None))),
    )
    monkeypatch.setattr(
        MUT, 'functools', SimpleNamespace(partial=mock.Mock(name='partial')),
    )
    monkeypatch.setattr(MUT, 'logs', mock.Mock())

    return SimpleNamespace(
        config=Config,
        addPath=AddPath,
        paths=paths,
        expansions=expansions,
        namedDict=namedDict,
        namedDictMade=namedDictMade,
        tempDir=tempDir,
        cmdExec=cmdExec,
        vmFactory=vmFactory,
        ocFactory=ocFactory,
        virtCtlFactory=virtCtlFactory,
    )
