'''Guarded local inputs for VM fixture consumer tests.'''
import base64
import csv
import json
from collections.abc import Callable, Generator
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
import yaml

import fixtures.VM as MUT
from fixtures.VM import FxtVM as FxtVM
from libs.OpenShift.LP.Virt.CLI.VirtCtl import VirtCtlCLI
from libs.OpenShift.OCP.CLI.OC import OCcli
import Utils
from libs.Utils.CmdExec import CmdRes


@pytest.fixture(name='localPreparation')
def LocalPreparation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        MUT, '_DEF__FXT_CFG',
        MUT._DEF__FXT_CFG | MUT.NamedDict(
            fxtInf=MUT.NamedDict(
                gstCfg=MUT.NamedDict(
                    usrCrd=MUT.NamedDict(
                        sshKey=MUT.NamedDict(
                            prv='/synthetic/private-key',
                            pub='/synthetic/public-key',
                        ),
                    ),
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        Path, 'read_text', lambda *args, **kw: 'ssh-ed25519 synthetic',
    )
    monkeypatch.setattr(
        Path, 'open', mock.mock_open(read_data='ssh-ed25519 synthetic'),
    )
    Utils.AutoPatch(monkeypatch, MUT, 'CmdExec')
    MUT.CmdExec.return_value.Run.return_value = CmdRes(
        'ssh-ed25519 synthetic', '', 0, True,
    )


@pytest.fixture(name='localContext')
def LocalContext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    sshKeyDirs: list[TemporaryDirectory],
) -> SimpleNamespace:
    root = Path(MUT.__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    keyPath = tmp_path / 'private-key'
    monkeypatch.setenv('VMCTX__KEY_DIR', str(tmp_path))
    keyPath.write_text('unit-private-ed25519')
    keyPath.chmod(0o600)
    publicKey = 'ssh-ed25519 unit-public-private-key\n'
    access = tmp_path / 'access'
    secret = tmp_path / 'relative-secret'
    access.write_text('synthetic-access-value\n')
    secret.write_text('synthetic-secret-value\n')
    cfg = json.loads(json.dumps(MUT._DEF__FXT_CFG))
    cfg['testPars'] = MUT.NamedDict(cfg['testPars'])
    cfg['fxtInf']['vmCfg'] = dict(
        cfg['fxtInf']['vmCfg'],
        ctxData=dict(cfg['fxtInf']['vmCfg']['ctxData']),
        timeout=MUT.NamedDict(cfg['fxtInf']['vmCfg']['timeout']),
    )
    cfg['ns'] = 'context-ns'
    cfg['fxtInf']['vmCfg']['namePfx'] = 'context-vm'
    cfg['cliCfg'] = MUT.NamedDict(
        ocCfg={'context': 'fixture-context', 'kubeconfig': '/mock/config'},
        vcCfg={'context': 'fixture-context'},
    )
    cfg['fxtInf']['gstCfg']['usrCrd']['sshKey'] = {
        'prv': str(keyPath), 'pub': None,
    }
    cfg['fxtInf']['gstCfg']['imgSrc']['CDI']['credInfo']['vaultInfo'] = {
        'file--AccKeyId': str(access),
        'file--AccKeySecret': 'relative-secret',
    }
    def KeyRun(command: list[str], **kwargs: Any) -> Any:
        '''Model key-file command results without running OpenSSH.'''
        assert (command[0] == 'ssh-keygen')
        assert kwargs['capture_output']
        assert (kwargs['timeout'] == 30)
        path = Path(command[command.index('-f') + 1])
        if ('-q' in command):
            algorithm = command[command.index('-t') + 1]
            assert (algorithm in ('ed25519', 'rsa'))
            assert (command[command.index('-N') + 1] == '')
            path.write_text(f'unit-private-{algorithm}')
            path.chmod(0o600)
            path.with_suffix(path.suffix + '.pub').write_text(
                f'ssh-{algorithm} unit-public-{path.name}\n',
            )
            output = ''
        elif ('-p' in command):
            assert (command[command.index('-P') + 1] == '')
            assert (command[command.index('-N') + 1] == 'synthetic-passphrase')
            path.write_text('unit-encrypted-private')
            output = ''
        else:
            assert (command[:5] == ['ssh-keygen', '-y', '-P', '', '-f'])
            content = ''
            if path.is_file():
                with open(path, encoding='utf-8') as stream:
                    content = stream.read()
            if (content not in ('unit-private-ed25519', 'unit-private-rsa')):
                return subprocess.CompletedProcess(command, 1, '', 'invalid')
            algorithm = content.removeprefix('unit-private-')
            output = f'ssh-{algorithm} unit-public-{path.name}\n'
        if not kwargs.get('text'): output = output.encode()
        return subprocess.CompletedProcess(command, 0, output, '')

    state = SimpleNamespace(
        cfg=cfg,
        keyPath=keyPath,
        publicKey=publicKey,
        resources=[],
        commands=[],
        present={},
        statuses={},
        fail='',
        secrets={},
        namespaces={},
        clients=[],
        keyCalls=0,
        genCalls=0,
        generated=[],
        secretTimeouts=[],
        sshKeyDirs=sshKeyDirs,
        request=SimpleNamespace(
            config=SimpleNamespace(rootpath=root),
            addfinalizer=request.addfinalizer,
        ),
        recorder=Utils.RecordingExec(),
        nativeRun=KeyRun,
    )

    def ClusterRun(command: list[str], **kwargs: Any) -> Any:
        if (command[0] == 'ssh-keygen'):
            assert (kwargs['input'] == '')
            assert (kwargs['timeout'] == 30)
            if ('-t' in command):
                assert (command[:6] == [
                    'ssh-keygen', '-q', '-t', 'ed25519', '-N', '',
                ])
                assert (Path(command[-1]).name == 'id_ed25519')
                assert Path(command[-1]).parent.name.startswith('fxtVM--ssh--')
                state.genCalls += 1
                state.generated.append(Path(command[-1]))
                if (state.fail == 'generate'):
                    raise RuntimeError('synthetic-private-error')
                if (state.fail in ('generate-result', 'generate-timeout')):
                    Path(command[-1]).write_text('synthetic-partial-private')
                    if (state.fail == 'generate-timeout'):
                        raise subprocess.TimeoutExpired(
                            command, 30, output='synthetic-sensitive-stdout',
                            stderr='synthetic-sensitive-stderr',
                        )
                    return subprocess.CompletedProcess(
                        command, 1, 'synthetic-sensitive-stdout',
                        'synthetic-sensitive-stderr',
                    )
                return KeyRun(command, **kwargs)
            assert (command[:5] == ['ssh-keygen', '-y', '-P', '', '-f'])
            assert (
                Path(command[5]).absolute().is_relative_to(tmp_path) or
                (Path(command[5]) in state.generated)
            )
            state.keyCalls += 1
            if (state.fail == 'key-timeout'):
                raise subprocess.TimeoutExpired(
                    command, 30, output='PRIVATE KEY',
                    stderr='synthetic-secret-value',
                )
            if (state.fail == 'key-empty'):
                return subprocess.CompletedProcess(
                    command, 0, '  \n', '',
                )
            return KeyRun(command, **kwargs)
        assert (command[0] in ('oc-unit', 'virtctl-unit', 'virtctl'))
        command = [
            part for arg in command
            for part in (
                arg.split('=', 1) if (
                    arg.startswith('-') and
                    not arg.startswith('--from-file=')
                ) else [arg]
            )
        ]
        state.commands.append(command)
        stdout = ''
        if ('options' in command):
            stdout = (
                '  -n, --namespace=\n'
                '      --context=\n'
                '      --kubeconfig=\n'
            )
        elif ('get' in command):
            ref = command[-1]
            if ('-f' in command):
                manifest = yaml.safe_load(kwargs['input'])
                ref = (
                    manifest['kind']
                    + '/'
                    + manifest['metadata']['name']
                )
            name = ref.split('/')[-1]
            ns = command[(command.index('-n') + 1)]
            if ref.startswith('Namespace/'):
                resource = state.namespaces.get(
                    name,
                    {
                        'metadata': {'name': name, 'uid': 'existing-ns'},
                        'status': {'phase': 'Active'},
                    },
                )
            elif ref.startswith('Secret/'):
                resource = state.secrets.get((ns, name))
            else:
                resource = None
            if ref.startswith(('Namespace/', 'Secret/')):
                if resource:
                    stdout = (
                        resource['metadata']['uid']
                        if ('jsonpath={.metadata.uid}' in command)
                        else json.dumps(resource)
                    )
            elif ('jsonpath={.metadata.namespace}' in command):
                stdout = ns
            elif any(
                (part.replace("'", '') == 'jsonpath={.status.printableStatus}')
                for part in command
            ):
                assert (name in state.present)
                stdout = state.statuses.get(name, 'Stopped')
            else:
                stdout = state.present.get(name, '')
        elif ('start' in command):
            assert (command[0] in ('virtctl-unit', 'virtctl'))
            assert (command[-2] == 'start')
            name = command[-1]
            assert (name in state.present)
            assert command[command.index('-n') + 1]
            if (state.fail == 'start-exception'):
                raise RuntimeError('synthetic start failure')
            if (state.fail == 'start-false'):
                return subprocess.CompletedProcess(command, 1, '', 'start')
            state.statuses[name] = 'Running'
        elif ('wait' in command):
            if ('condition=Ready' in command):
                assert command[-1].startswith('VirtualMachine/')
                assert (command[-1].split('/')[-1] in state.present)
                assert command[command.index('--timeout') + 1]
                if (state.fail == 'ready-false'):
                    return subprocess.CompletedProcess(command, 1, '', 'ready')
        elif ('delete' in command):
            assert all(path.exists() for path in state.generated)
            ref = command[-1]
            if ('-f' in command):
                manifest = yaml.safe_load(kwargs['input'])
                ref = f"{manifest['kind']}/{manifest['metadata']['name']}"
            name = ref.split('/')[-1]
            if ref.startswith('Secret/'):
                ns = command[(command.index('-n') + 1)]
                state.secrets.pop((ns, name), None)
            elif ref.startswith('VirtualMachine/'):
                state.present.pop(name, None)
            else:
                state.namespaces.pop(name, None)
                for key in list(state.secrets):
                    if (key[0] == name): del state.secrets[key]
                for resource in state.resources:
                    if (
                        (resource['kind'] == 'VirtualMachine')
                        and (resource['metadata']['namespace'] == name)
                    ):
                        state.present.pop(resource['metadata']['name'], None)
            stdout = ref
        elif (('create' in command) or ('apply' in command)):
            if (('namespace' in command) and ('create' in command)):
                manifest = {
                    'kind': 'Namespace',
                    'metadata': {'name': command[-1]},
                }
            elif (('generic' in command) and ('create' in command)):
                files = [
                    part
                    for part in command
                    if part.startswith('--from-file=')
                ]
                data = {}
                for source in (
                    next(csv.reader([files[0].split('=', 1)[1]])) if files
                    else []
                ):
                    (key, path) = source.split('=', 1)
                    file = Path(path)
                    assert ((file.stat().st_mode & 0o777) == 0o600)
                    assert ((file.parent.stat().st_mode & 0o777) == 0o700)
                    data[key] = base64.b64encode(file.read_bytes()).decode()
                manifest = {
                    'kind': 'Secret', 'type': 'Opaque', 'data': data,
                    'metadata': {
                        'name': command[-1],
                        'namespace': command[(command.index('-n') + 1)],
                    },
                }
            else:
                manifest = yaml.safe_load(kwargs['input'])
            if ((manifest['kind'] == 'Secret') and state.fail):
                if (state.fail == 'secret-timeout'):
                    timeout = subprocess.TimeoutExpired(
                        command, 30,
                        output='synthetic-secret-value',
                        stderr='PRIVATE KEY',
                    )
                    state.secretTimeouts.append(timeout)
                    raise timeout
                if (state.fail == 'os-error'):
                    raise OSError('synthetic-secret-value')
                if (state.fail == 'exception'):
                    raise RuntimeError('synthetic-secret-value')
                if (state.fail in command):
                    return subprocess.CompletedProcess(
                        command, 1, 'synthetic-secret-value',
                        'synthetic-access-value PRIVATE KEY synthetic',
                    )
            if ('create' in command):
                stdout = yaml.safe_dump(manifest)
            else:
                if (manifest['kind'] in ('Secret', 'Namespace')):
                    manifest['metadata']['uid'] = 'owned-' + manifest['kind']
                state.resources.append(manifest)
                metadata = manifest['metadata']
                if (manifest['kind'] == 'Secret'):
                    state.secrets[(
                        metadata['namespace'], metadata['name'],
                    )] = manifest
                if (manifest['kind'] == 'Namespace'):
                    manifest['status'] = {'phase': 'Active'}
                    state.namespaces[metadata['name']] = manifest
                if (manifest['kind'] == 'VirtualMachine'):
                    manifest['metadata']['uid'] = 'owned-uid'
                    state.present[metadata['name']] = 'owned-uid'
                stdout = json.dumps(manifest)
        else:
            assert ('wait' in command)
        return subprocess.CompletedProcess(command, 0, stdout, '')

    monkeypatch.setattr(subprocess, 'run', ClusterRun)

    def OCfactory(ns: str, **opts: Any) -> OCcli:
        client = OCcli('oc-unit', cmdExec=state.recorder, n=ns, **opts)
        state.clients.append(client)
        return client

    state.ocFactory = OCfactory
    return state


@pytest.fixture(name='fxtOCcli')
def LocalOCcli(localContext: SimpleNamespace) -> Callable[..., OCcli]:
    return localContext.ocFactory


@pytest.fixture(name='fxtVirtCtlCLI')
def LocalVirtCtlCLI(
    localContext: SimpleNamespace,
) -> Callable[..., VirtCtlCLI]:
    def Create(ns: str, **opts: Any) -> VirtCtlCLI:
        return VirtCtlCLI(
            'virtctl-unit', cmdExec=localContext.recorder, n=ns, **opts,
        )
    return Create


@pytest.fixture(name='sshKeyDirs')
def SSHkeyDirs() -> Generator[list[TemporaryDirectory], None, None]:
    dirs: list[TemporaryDirectory] = []
    try:
        yield dirs
    finally:
        for dir in dirs: dir.cleanup()
