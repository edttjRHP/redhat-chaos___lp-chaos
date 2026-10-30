import base64
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
import yaml

from libs.CustomTypes import NamedDict
from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.OpenShift.OCP.Resources import OwnInf, ResCfg, SpecMismatch
import libs.OpenShift.OCP.Resources.Secret as MUT
from libs.Utils.CmdExec import CmdRes


@pytest.fixture(name='secretContext')
def SecretContext(
    resourceCLI: mock.MagicMock, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    root = tmp_path / 'csv,quoted"directory'
    root.mkdir()
    state = SimpleNamespace(
        cli=resourceCLI, files=[], producers=[], payloads=[], directories=[],
        failure=None, override=None, calls=[],
    )

    def Directory(**kwargs: Any) -> TemporaryDirectory:
        directory = TemporaryDirectory(dir=root, **kwargs)
        state.directories.append(Path(directory.name))
        return directory

    def Resolve(producer: Any, **kwargs: Any) -> dict[str, Any]:
        assert (kwargs['isSecret'] is True)
        state.calls.append(kwargs)
        state.producers.append(producer)
        if isinstance(producer, str):
            resource = yaml.safe_load(producer)
            data = dict(resource.get('data', {}))
            data.update({
                key: base64.b64encode(value.encode()).decode()
                for (key, value) in resource.get('stringData', {}).items()
            })
            resource['data'] = data
        else:
            assert (producer['subCmd'] == 'secret generic')
            assert (producer['args'] == ['image-access'])
            options = producer['opts']
            sources = options['from-file']
            data = {}
            for source in (next(csv.reader([sources]), []) if sources else []):
                (key, filename) = source.split('=', 1)
                path = Path(filename)
                assert (path.name.isdecimal())
                assert ((path.stat().st_mode & 0o777) == 0o600)
                assert ((path.parent.stat().st_mode & 0o777) == 0o700)
                state.files.append(path)
                value = path.read_bytes()
                state.payloads.append(value)
                assert (value.decode(errors='replace') not in str(producer))
                data[key] = base64.b64encode(value).decode()
            resource = {'type': options['type'], 'data': data}
        if state.failure: raise state.failure
        return (state.override if (state.override is not None) else resource)

    def New(producer: Any, **kwargs: Any) -> tuple[Any, CmdRes, dict]:
        return (
            OCcli.ResPhase.APPLIED, CmdRes('', '', 0, True),
            Resolve(producer, **kwargs),
        )

    def Ensure(producer: Any, owned: list, **kwargs: Any) -> dict:
        state.owned = owned
        return Resolve(producer, **kwargs)

    resourceCLI.NewRes.side_effect = New
    resourceCLI.EnsureRes.side_effect = Ensure
    monkeypatch.setattr(MUT, 'TemporaryDirectory', Directory)
    return state


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('namespace', [None, '', 'workload-ns'])
def test_Create_NativeBinaryUTF8OverrideAndPrivateCSVFiles(
    secretContext: SimpleNamespace, tracked: bool, namespace: str | None,
) -> None:
    state = secretContext
    cfg = ResCfg(
        meta=NamedDict(name='image-access', namespace=namespace),
        spec=NamedDict(
            type='custom-type', data={
                'binary': base64.b64encode(b'\xff\x00dummy-binary').decode(),
                'overlap': base64.b64encode(b'dummy-old').decode(),
            },
            stringData={'overlap': 'dummy-new-\u2603', 'plain': 'dummy-utf8'},
        ),
    )
    owned = [('caller', 'Secret/caller', 'caller-uid')]
    result = MUT.Create(state.cli, cfg, (OwnInf(owned) if tracked else None))
    assert (result['type'] == 'custom-type')
    assert ({
        key: base64.b64decode(value)
        for (key, value) in result['data'].items()
    } == {
        'binary': b'\xff\x00dummy-binary',
        'overlap': 'dummy-new-\u2603'.encode(),
        'plain': b'dummy-utf8',
    })
    options = state.producers[0]['opts']
    assert (('n' in options) == bool(namespace))
    if namespace: assert (options['n'] == namespace)
    assert ('-n' not in options)
    assert isinstance(options['from-file'], str)
    assert ('"' in options['from-file'])
    assert (len(state.files) == 3)
    assert all(
        not path.exists()
        for path in (state.files + state.directories)
    )
    if tracked:
        assert (state.owned is owned)
        assert (state.calls[0]['existResDelTO'] == '5m')
        state.cli.NewRes.assert_not_called()
    else:
        assert (state.calls == [{'isSecret': True}])
        state.cli.EnsureRes.assert_not_called()
    assert (owned == [('caller', 'Secret/caller', 'caller-uid')])


def test_Create_EmptyDataUsesDefaultTypeWithoutFromFile(
    secretContext: SimpleNamespace,
) -> None:
    state = secretContext
    assert (MUT.Create(state.cli, ResCfg(
        meta=NamedDict(name='image-access'),
    )) == {'type': 'Opaque', 'data': {}})
    assert (state.producers[0]['opts'] == {
        'type': 'Opaque', 'from-file': None,
    })
    assert (state.files == [])
    assert all(not path.exists() for path in state.directories)


@pytest.mark.parametrize('tracked', [False, True])
def test_Create_AuthoritativeYAMLDeterminesTypeAndData(
    secretContext: SimpleNamespace, tracked: bool,
) -> None:
    state = secretContext
    text = yaml.safe_dump({
        'kind': 'Secret', 'metadata': {'namespace': 'yaml-ns', 'name': 'yaml'},
        'type': 'yaml-type',
        'data': {'one': base64.b64encode(b'dummy-old').decode()},
        'stringData': {'one': 'dummy-new-\u2603'},
    })
    cfg = ResCfg(meta=None, spec=object(), yaml=text)
    result = MUT.Create(state.cli, cfg, (OwnInf([]) if tracked else None))
    assert (result['type'] == 'yaml-type')
    assert (
        base64.b64decode(result['data']['one'])
        == 'dummy-new-\u2603'.encode()
    )
    assert (state.producers == [text])
    assert (state.directories == [])


@pytest.mark.parametrize('spec', [
    NamedDict(data={'key': 'a'}),
    NamedDict(stringData={'../bad': 'dummy-sensitive'}),
    NamedDict(stringData={'key': b'dummy-sensitive'}),
    NamedDict(data=[]),
])
def test_Create_PreparationFailureIsSanitized(
    secretContext: SimpleNamespace, spec: NamedDict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = secretContext
    with pytest.raises(
        RuntimeError, match='Could not prepare Secret'
    ) as caught:
        MUT.Create(state.cli, ResCfg(
            meta=NamedDict(name='image-access'), spec=spec,
        ))
    assert caught.value.__suppress_context__
    assert ('dummy-sensitive' not in (str(caught.value) + caplog.text))
    state.cli.NewRes.assert_not_called()
    state.cli.EnsureRes.assert_not_called()


@pytest.mark.parametrize('phase', ['lifecycle', 'write'])
def test_Create_FailureCleansPrivateFilesAndSuppressesDetails(
    secretContext: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, phase: str,
) -> None:
    state = secretContext
    failure = RuntimeError('dummy-sensitive-failure')
    if (phase == 'lifecycle'): state.failure = failure
    else:
        nativeOpen = MUT.os.open

        def Open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            if (flags & MUT.os.O_EXCL): raise failure
            return nativeOpen(path, flags, *args, **kwargs)

        monkeypatch.setattr(MUT.os, 'open', Open)
    with pytest.raises(
        RuntimeError, match='Could not create Secret'
    ) as caught:
        MUT.Create(state.cli, ResCfg(
            meta=NamedDict(name='image-access'),
            spec=NamedDict(stringData={'key': 'dummy-sensitive-value'}),
        ))
    assert caught.value.__suppress_context__
    assert ('dummy-sensitive' not in (str(caught.value) + caplog.text))
    assert all(
        not path.exists()
        for path in (state.files + state.directories)
    )


@pytest.mark.parametrize('actual', [
    {'type': 'wrong', 'data': {}},
    {'type': 'Opaque', 'data': {'different': 'ZHVtbXk='}},
])
def test_Create_MismatchUsesSharedException(
    secretContext: SimpleNamespace, actual: dict,
) -> None:
    state = secretContext
    state.override = actual
    with pytest.raises(SpecMismatch) as caught:
        MUT.Create(state.cli, ResCfg(meta=NamedDict(name='image-access')))
    assert (type(caught.value) is MUT.SpecMismatch is SpecMismatch)


@pytest.mark.parametrize('actual', [{}, {'data': {'key': 'a'}}])
def test_Create_InvalidConsumerStateIsSanitized(
    secretContext: SimpleNamespace, actual: dict,
) -> None:
    secretContext.override = actual
    with pytest.raises(RuntimeError, match='Could not create Secret'):
        MUT.Create(secretContext.cli, ResCfg(
            meta=NamedDict(name='image-access'),
        ))


@pytest.mark.parametrize('cfg', [
    ResCfg(meta=NamedDict(name='one', labels={})),
    ResCfg(spec=NamedDict(immutable=False)),
    ResCfg(yaml='kind: Namespace'),
])
def test_Create_ValidationFailurePrecedesPrivateFiles(
    secretContext: SimpleNamespace, cfg: ResCfg,
) -> None:
    with pytest.raises(ValueError): MUT.Create(secretContext.cli, cfg)
    assert (secretContext.directories == [])
    secretContext.cli.NewRes.assert_not_called()
