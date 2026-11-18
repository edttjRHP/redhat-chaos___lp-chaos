import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
import yaml

from libs.OpenShift.OCP.Resources import SpecMismatch
import libs.OpenShift.OCP.Resources.Secret as MUT


def NativeMeta(
    name: str = 'image-access',
    namespace: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(name=name, get=mock.Mock(return_value=namespace))


def SecretConfig(
    spec: object,
    *,
    namespace: str | None = None,
    yamlText: str | None = None,
    native: bool = True,
) -> SimpleNamespace:
    meta = NativeMeta(namespace=namespace) if native else None
    return SimpleNamespace(meta=meta, spec=spec, yaml=yamlText)


@pytest.fixture(name='secretContext')
def SecretContext(
    resourceCLI: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    '''Provide mocked helpers, parser, codecs, and file collaborators.'''
    validate = mock.Mock(return_value=None)
    create = mock.Mock()
    parser = mock.Mock(side_effect=AssertionError('Unexpected YAML parse'))
    yamlModule = SimpleNamespace(YAMLError=yaml.YAMLError, safe_load=parser)
    directoryName = 'tmp-secret,csv"quoted'
    directory = mock.MagicMock()
    directory.__enter__.return_value = directoryName
    directory.__exit__.return_value = False
    temporaryDirectory = mock.Mock(return_value=directory)
    streams = []
    for _ in range(3):
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.__exit__.return_value = False
        streams.append(stream)
    fromFiles = mock.MagicMock()
    fromFiles.__enter__.return_value = fromFiles
    fromFiles.__exit__.return_value = False
    fromFiles.getvalue.return_value = 'canned-from-file-list\n'
    stringIO = mock.Mock(return_value=fromFiles)
    writer = mock.Mock()
    csvWriter = mock.Mock(return_value=writer)
    csvModule = SimpleNamespace(writer=csvWriter)
    decode = mock.Mock(side_effect=AssertionError('Unexpected base64 decode'))
    base64Module = SimpleNamespace(b64decode=decode)
    osAPI = mock.Mock()
    osAPI.O_WRONLY = os.O_WRONLY
    osAPI.O_CREAT = os.O_CREAT
    osAPI.O_EXCL = os.O_EXCL
    osAPI.open.side_effect = [101, 102, 103]
    osAPI.fdopen.side_effect = streams

    monkeypatch.setattr(MUT, '_ValResCfg', validate)
    monkeypatch.setattr(MUT, '_ResCreate', create)
    monkeypatch.setattr(MUT, 'yaml', yamlModule)
    monkeypatch.setattr(MUT, 'base64', base64Module)
    monkeypatch.setattr(MUT, 'csv', csvModule)
    monkeypatch.setattr(MUT, 'StringIO', stringIO)
    monkeypatch.setattr(MUT, 'TemporaryDirectory', temporaryDirectory)
    monkeypatch.setattr(MUT, 'os', osAPI)
    return SimpleNamespace(
        cli=resourceCLI, validate=validate, create=create, parser=parser,
        decode=decode, directoryName=directoryName, directory=directory,
        temporaryDirectory=temporaryDirectory, streams=streams, os=osAPI,
        fromFiles=fromFiles, stringIO=stringIO, csvWriter=csvWriter,
        writer=writer,
    )


def test_Create_InitialParserFailureIsPrivate(
    secretContext: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = SecretConfig(
        object(), yamlText='kind: Secret\ndata: [private', native=False,
    )
    failure = yaml.YAMLError('dummy-private-yaml')
    secretContext.validate.side_effect = failure

    with pytest.raises(RuntimeError) as caught:
        MUT.Create(secretContext.cli, cfg)

    assert (
        str(caught.value) ==
        'Invalid Secret conf. (details are suppressed).'
    )
    assert caught.value.__suppress_context__
    assert ('dummy-private-yaml' not in (str(caught.value) + caplog.text))
    assert ('[private' not in (str(caught.value) + caplog.text))
    secretContext.validate.assert_called_once_with(
        cfg, {'namespace', 'name'}, {'type', 'data', 'stringData'}, 'Secret',
    )
    secretContext.create.assert_not_called()
    secretContext.parser.assert_not_called()
    secretContext.decode.assert_not_called()
    secretContext.temporaryDirectory.assert_not_called()
    secretContext.os.open.assert_not_called()


@pytest.mark.parametrize('authoritative', [False, True])
@pytest.mark.parametrize('data', ['!!!!', 'YQ=', 'YQ==\n', 'é'])
def test_Create_InvalidDesiredBase64IsSanitized(
    secretContext: SimpleNamespace,
    authoritative: bool,
    data: str,
) -> None:
    text = 'kind: Secret\nmetadata:\n  name: image-access\n'
    cfg = (
        SecretConfig(object(), yamlText=text, native=False)
        if authoritative else SecretConfig({'data': {'key': data}})
    )
    if authoritative:
        secretContext.parser.side_effect = None
        secretContext.parser.return_value = {'data': {'key': data}}
    secretContext.decode.side_effect = ValueError('invalid desired data')

    with pytest.raises(RuntimeError) as caught:
        MUT.Create(secretContext.cli, cfg)

    assert (
        str(caught.value) ==
        'Could not prepare Secret (private details are suppressed).'
    )
    assert caught.value.__suppress_context__
    secretContext.decode.assert_called_once_with(data, validate=True)
    secretContext.validate.assert_called_once_with(
        cfg, {'namespace', 'name'}, {'type', 'data', 'stringData'}, 'Secret',
    )
    secretContext.create.assert_not_called()
    secretContext.temporaryDirectory.assert_not_called()
    secretContext.os.open.assert_not_called()
    secretContext.stringIO.assert_not_called()
    secretContext.csvWriter.assert_not_called()
    if authoritative: secretContext.parser.assert_called_once_with(text)
    else: secretContext.parser.assert_not_called()


@pytest.mark.parametrize('authoritative', [False, True])
@pytest.mark.parametrize('data,expectedBytes', [('', b''), ('YQ==', b'a')])
def test_Create_ValidDesiredBase64ReturnsMockedResource(
    secretContext: SimpleNamespace,
    authoritative: bool,
    data: str,
    expectedBytes: bytes,
) -> None:
    text = 'kind: Secret\nmetadata:\n  name: image-access\n'
    cfg = (
        SecretConfig(object(), yamlText=text, native=False)
        if authoritative else SecretConfig({'data': {'key': data}})
    )
    resource = {'type': 'Opaque', 'data': {'key': data}}
    secretContext.create.return_value = resource
    secretContext.decode.side_effect = [expectedBytes, expectedBytes]
    if authoritative:
        secretContext.parser.side_effect = None
        secretContext.parser.return_value = {'data': {'key': data}}
    else:
        secretContext.fromFiles.getvalue.return_value = (
            'canned-from-file-list\n'
        )

    assert (MUT.Create(secretContext.cli, cfg) is resource)
    secretContext.validate.assert_called_once_with(
        cfg, {'namespace', 'name'}, {'type', 'data', 'stringData'}, 'Secret',
    )
    assert (secretContext.decode.call_args_list == [
        mock.call(data, validate=True), mock.call(data),
    ])
    if authoritative:
        secretContext.parser.assert_called_once_with(text)
        secretContext.create.assert_called_once_with(
            secretContext.cli, text, None, True,
        )
        secretContext.temporaryDirectory.assert_not_called()
        secretContext.stringIO.assert_not_called()
        secretContext.csvWriter.assert_not_called()
        secretContext.os.open.assert_not_called()
    else:
        producer = {
            'subCmd': 'secret generic', 'args': ['image-access'],
            'opts': {
                'from-file': 'canned-from-file-list', 'type': 'Opaque',
            },
        }
        fileSources = [f'key={Path(secretContext.directoryName, "0")}']
        secretContext.parser.assert_not_called()
        secretContext.create.assert_called_once_with(
            secretContext.cli, producer, None, True,
        )
        secretContext.stringIO.assert_called_once_with()
        secretContext.csvWriter.assert_called_once_with(
            secretContext.fromFiles, lineterminator='\n',
        )
        secretContext.writer.writerow.assert_called_once_with(fileSources)
        secretContext.fromFiles.getvalue.assert_called_once_with()
        secretContext.temporaryDirectory.assert_called_once_with(
            prefix='oc--secret--',
        )
        secretContext.directory.__enter__.assert_called_once_with()
        secretContext.directory.__exit__.assert_called_once_with(
            None, None, None,
        )
        secretContext.os.chmod.assert_called_once_with(
            secretContext.directoryName, 0o0700,
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        secretContext.os.open.assert_called_once_with(
            Path(secretContext.directoryName, '0'), flags, 0o0600,
        )
        secretContext.os.fdopen.assert_called_once_with(101, 'wb')
        secretContext.streams[0].write.assert_called_once_with(expectedBytes)
        secretContext.streams[0].__exit__.assert_called_once_with(
            None, None, None,
        )


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('namespace,expectedNamespace', [
    (None, None), ('', None), ('workload-ns', 'workload-ns'),
])
def test_Create_NativeBinaryUTF8UsesPrivateFileInputs(
    secretContext: SimpleNamespace,
    tracked: bool,
    namespace: str | None,
    expectedNamespace: str | None,
) -> None:
    cfg = SecretConfig(
        {
            'type': 'custom-type',
            'data': {
                'binary': 'binary-input', 'overlap': 'overlap-input',
            },
            'stringData': {
                'overlap': 'dummy-new-☃', 'plain': 'dummy-utf8',
            },
        },
        namespace=namespace,
    )
    resource = {
        'type': 'custom-type',
        'data': {
            'binary': 'binary-returned',
            'overlap': 'overlap-returned',
            'plain': 'plain-returned',
        },
    }
    secretContext.create.return_value = resource
    secretContext.decode.side_effect = [
        b'\xff\x00dummy-binary', b'dummy-old',
        b'\xff\x00dummy-binary', b'dummy-new-\xe2\x98\x83',
        b'dummy-utf8',
    ]
    ownInf = object() if tracked else None

    assert (MUT.Create(secretContext.cli, cfg, ownInf) is resource)
    secretContext.validate.assert_called_once_with(
        cfg, {'namespace', 'name'}, {'type', 'data', 'stringData'}, 'Secret',
    )
    secretContext.parser.assert_not_called()
    assert (secretContext.decode.call_args_list == [
        mock.call('binary-input', validate=True),
        mock.call('overlap-input', validate=True),
        mock.call('binary-returned'),
        mock.call('overlap-returned'),
        mock.call('plain-returned'),
    ])
    fileSources = [
        f'binary={Path(secretContext.directoryName, "0")}',
        f'overlap={Path(secretContext.directoryName, "1")}',
        f'plain={Path(secretContext.directoryName, "2")}',
    ]
    options = {'from-file': 'canned-from-file-list', 'type': 'custom-type'}
    if (expectedNamespace is not None): options['n'] = expectedNamespace
    producer = {
        'subCmd': 'secret generic', 'args': ['image-access'], 'opts': options,
    }
    secretContext.create.assert_called_once_with(
        secretContext.cli, producer, ownInf, True,
    )
    secretContext.stringIO.assert_called_once_with()
    secretContext.csvWriter.assert_called_once_with(
        secretContext.fromFiles, lineterminator='\n',
    )
    secretContext.writer.writerow.assert_called_once_with(fileSources)
    secretContext.fromFiles.getvalue.assert_called_once_with()
    secretContext.temporaryDirectory.assert_called_once_with(
        prefix='oc--secret--',
    )
    secretContext.directory.__enter__.assert_called_once_with()
    secretContext.directory.__exit__.assert_called_once_with(None, None, None)
    secretContext.os.chmod.assert_called_once_with(
        secretContext.directoryName, 0o0700,
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    secretContext.os.open.assert_has_calls([
        mock.call(Path(secretContext.directoryName, '0'), flags, 0o0600),
        mock.call(Path(secretContext.directoryName, '1'), flags, 0o0600),
        mock.call(Path(secretContext.directoryName, '2'), flags, 0o0600),
    ])
    secretContext.os.fdopen.assert_has_calls([
        mock.call(101, 'wb'), mock.call(102, 'wb'), mock.call(103, 'wb'),
    ])
    secretContext.streams[0].write.assert_called_once_with(
        b'\xff\x00dummy-binary',
    )
    secretContext.streams[1].write.assert_called_once_with(
        b'dummy-new-\xe2\x98\x83',
    )
    secretContext.streams[2].write.assert_called_once_with(b'dummy-utf8')
    for stream in secretContext.streams:
        stream.__exit__.assert_called_once_with(None, None, None)
    cfg.meta.get.assert_called_once_with('namespace')
    secretContext.cli.NewRes.assert_not_called()
    secretContext.cli.EnsureRes.assert_not_called()


def test_Create_EmptyDataUsesDefaultTypeWithoutFromFile(
    secretContext: SimpleNamespace,
) -> None:
    cfg = SecretConfig({})
    resource = {'type': 'Opaque', 'data': {}}
    secretContext.create.return_value = resource
    secretContext.fromFiles.getvalue.return_value = '\n'

    assert (MUT.Create(secretContext.cli, cfg) is resource)
    secretContext.decode.assert_not_called()
    secretContext.create.assert_called_once_with(
        secretContext.cli,
        {
            'subCmd': 'secret generic', 'args': ['image-access'],
            'opts': {'from-file': None, 'type': 'Opaque'},
        },
        None, True,
    )
    secretContext.csvWriter.assert_called_once_with(
        secretContext.fromFiles, lineterminator='\n',
    )
    secretContext.writer.writerow.assert_called_once_with([])
    secretContext.fromFiles.getvalue.assert_called_once_with()
    secretContext.os.open.assert_not_called()
    secretContext.os.fdopen.assert_not_called()
    secretContext.directory.__exit__.assert_called_once_with(None, None, None)
    secretContext.cli.NewRes.assert_not_called()
    secretContext.cli.EnsureRes.assert_not_called()


@pytest.mark.parametrize('tracked', [False, True])
def test_Create_AuthoritativeYAMLUsesParsedSpecAndRawInput(
    secretContext: SimpleNamespace,
    tracked: bool,
) -> None:
    text = 'kind: Secret\nmetadata:\n  name: yaml-secret\n'
    parsed = {
        'type': 'yaml-type',
        'data': {'one': 'encoded-old'},
        'stringData': {'one': 'dummy-new-☃'},
    }
    resource = {'type': 'yaml-type', 'data': {'one': 'encoded-new'}}
    cfg = SecretConfig(object(), yamlText=text, native=False)
    secretContext.parser.side_effect = None
    secretContext.parser.return_value = parsed
    secretContext.create.return_value = resource
    secretContext.decode.side_effect = [
        b'dummy-old', b'dummy-new-\xe2\x98\x83',
    ]
    ownInf = object() if tracked else None

    assert (MUT.Create(secretContext.cli, cfg, ownInf) is resource)
    secretContext.validate.assert_called_once_with(
        cfg, {'namespace', 'name'}, {'type', 'data', 'stringData'}, 'Secret',
    )
    secretContext.parser.assert_called_once_with(text)
    assert (secretContext.decode.call_args_list == [
        mock.call('encoded-old', validate=True),
        mock.call('encoded-new'),
    ])
    secretContext.create.assert_called_once_with(
        secretContext.cli, text, ownInf, True,
    )
    secretContext.temporaryDirectory.assert_not_called()
    secretContext.stringIO.assert_not_called()
    secretContext.csvWriter.assert_not_called()
    secretContext.os.open.assert_not_called()
    secretContext.cli.NewRes.assert_not_called()
    secretContext.cli.EnsureRes.assert_not_called()


@pytest.mark.parametrize('spec', [
    {'data': {'key': 'a'}},
    {'stringData': {'../bad': 'dummy-sensitive'}},
    {'stringData': {'key': b'dummy-sensitive'}},
    {'data': []},
])
def test_Create_PreparationFailureIsSanitized(
    secretContext: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
    spec: dict[str, object],
) -> None:
    cfg = SecretConfig(spec)
    if (spec == {'data': {'key': 'a'}}):
        secretContext.decode.side_effect = ValueError('invalid desired data')

    with pytest.raises(RuntimeError) as caught:
        MUT.Create(secretContext.cli, cfg)

    assert (
        str(caught.value) ==
        'Could not prepare Secret (private details are suppressed).'
    )
    assert caught.value.__suppress_context__
    assert ('dummy-sensitive' not in (str(caught.value) + caplog.text))
    if (spec == {'data': {'key': 'a'}}):
        secretContext.decode.assert_called_once_with('a', validate=True)
    else: secretContext.decode.assert_not_called()
    secretContext.validate.assert_called_once_with(
        cfg, {'namespace', 'name'}, {'type', 'data', 'stringData'}, 'Secret',
    )
    secretContext.create.assert_not_called()
    secretContext.parser.assert_not_called()
    secretContext.temporaryDirectory.assert_not_called()
    secretContext.stringIO.assert_not_called()
    secretContext.csvWriter.assert_not_called()
    secretContext.os.open.assert_not_called()


@pytest.mark.parametrize('phase', ['lifecycle', 'write'])
def test_Create_FailureExitsTemporaryDirectoryAndSanitizes(
    secretContext: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
    phase: str,
) -> None:
    failure = RuntimeError('dummy-sensitive-failure')
    cfg = SecretConfig({'stringData': {'key': 'dummy-sensitive-value'}})
    if (phase == 'lifecycle'): secretContext.create.side_effect = failure
    else: secretContext.os.open.side_effect = failure

    with pytest.raises(RuntimeError) as caught:
        MUT.Create(secretContext.cli, cfg)

    assert (
        str(caught.value) ==
        'Could not create Secret (private details are suppressed).'
    )
    assert caught.value.__suppress_context__
    assert ('dummy-sensitive' not in (str(caught.value) + caplog.text))
    secretContext.temporaryDirectory.assert_called_once_with(
        prefix='oc--secret--',
    )
    secretContext.directory.__exit__.assert_called_once()
    assert (
        secretContext.directory.__exit__.call_args.args[0] is RuntimeError
    )
    assert (secretContext.directory.__exit__.call_args.args[1] is failure)
    secretContext.fromFiles.__exit__.assert_called_once()
    assert (secretContext.fromFiles.__exit__.call_args.args[1] is failure)
    secretContext.decode.assert_not_called()
    if (phase == 'lifecycle'): secretContext.create.assert_called_once()
    else: secretContext.create.assert_not_called()
    secretContext.cli.NewRes.assert_not_called()
    secretContext.cli.EnsureRes.assert_not_called()


def test_Create_MismatchRaisesSpecMismatchForWrongType(
    secretContext: SimpleNamespace,
) -> None:
    cfg = SecretConfig({'data': {'key': 'desired-data'}})
    resource = {'type': 'wrong', 'data': {'key': 'desired-data'}}
    secretContext.create.return_value = resource
    secretContext.decode.side_effect = [b'desired', b'desired']

    with pytest.raises(SpecMismatch) as caught:
        MUT.Create(secretContext.cli, cfg)

    assert (type(caught.value) is MUT.SpecMismatch is SpecMismatch)
    assert (str(caught.value) == 'Resolved Secret has different type or data.')
    assert (secretContext.decode.call_args_list == [
        mock.call('desired-data', validate=True), mock.call('desired-data'),
    ])
    secretContext.create.assert_called_once_with(
        secretContext.cli,
        {
            'subCmd': 'secret generic', 'args': ['image-access'],
            'opts': {
                'from-file': 'canned-from-file-list', 'type': 'Opaque',
            },
        },
        None, True,
    )
    secretContext.directory.__exit__.assert_called_once_with(None, None, None)


def test_Create_MismatchRaisesSpecMismatchForWrongBytes(
    secretContext: SimpleNamespace,
) -> None:
    cfg = SecretConfig({'data': {'key': 'desired-data'}})
    resource = {'type': 'Opaque', 'data': {'key': 'actual-data'}}
    secretContext.create.return_value = resource
    secretContext.decode.side_effect = [b'desired', b'actual']

    with pytest.raises(SpecMismatch) as caught:
        MUT.Create(secretContext.cli, cfg)

    assert (type(caught.value) is MUT.SpecMismatch is SpecMismatch)
    assert (str(caught.value) == 'Resolved Secret has different type or data.')
    assert (secretContext.decode.call_args_list == [
        mock.call('desired-data', validate=True), mock.call('actual-data'),
    ])
    secretContext.directory.__exit__.assert_called_once_with(None, None, None)


def test_Create_MismatchRaisesSpecMismatchForUnexpectedKey(
    secretContext: SimpleNamespace,
) -> None:
    cfg = SecretConfig({})
    resource = {'type': 'Opaque', 'data': {'different': 'ZHVtbXk='}}
    secretContext.create.return_value = resource
    secretContext.decode.side_effect = [b'dummy']
    secretContext.fromFiles.getvalue.return_value = '\n'

    with pytest.raises(SpecMismatch) as caught:
        MUT.Create(secretContext.cli, cfg)

    assert (type(caught.value) is MUT.SpecMismatch is SpecMismatch)
    assert (str(caught.value) == 'Resolved Secret has different type or data.')
    secretContext.decode.assert_called_once_with('ZHVtbXk=')
    secretContext.writer.writerow.assert_called_once_with([])
    secretContext.directory.__exit__.assert_called_once_with(None, None, None)


def test_Create_MismatchRaisesSpecMismatchForMissingActualData(
    secretContext: SimpleNamespace,
) -> None:
    cfg = SecretConfig({'data': {'key': 'desired-data'}})
    resource = {'type': 'Opaque'}
    secretContext.create.return_value = resource
    secretContext.decode.side_effect = [b'desired']

    with pytest.raises(SpecMismatch) as caught:
        MUT.Create(secretContext.cli, cfg)

    assert (type(caught.value) is MUT.SpecMismatch is SpecMismatch)
    assert (str(caught.value) == 'Resolved Secret has different type or data.')
    secretContext.decode.assert_called_once_with('desired-data', validate=True)
    secretContext.directory.__exit__.assert_called_once_with(None, None, None)


def test_Create_InvalidReturnedBase64IsSanitized(
    secretContext: SimpleNamespace,
) -> None:
    cfg = SecretConfig({})
    resource = {'data': {'key': 'invalid-actual-data'}}
    secretContext.create.return_value = resource
    secretContext.fromFiles.getvalue.return_value = '\n'
    secretContext.decode.side_effect = ValueError('invalid returned data')

    with pytest.raises(RuntimeError) as caught:
        MUT.Create(secretContext.cli, cfg)

    assert (
        str(caught.value) ==
        'Could not create Secret (private details are suppressed).'
    )
    assert caught.value.__suppress_context__
    secretContext.decode.assert_called_once_with('invalid-actual-data')
    secretContext.create.assert_called_once_with(
        secretContext.cli,
        {
            'subCmd': 'secret generic', 'args': ['image-access'],
            'opts': {'from-file': None, 'type': 'Opaque'},
        },
        None, True,
    )
    secretContext.csvWriter.assert_called_once_with(
        secretContext.fromFiles, lineterminator='\n',
    )
    secretContext.writer.writerow.assert_called_once_with([])
    secretContext.fromFiles.getvalue.assert_called_once_with()
    secretContext.directory.__exit__.assert_called_once_with(None, None, None)
    secretContext.cli.NewRes.assert_not_called()
    secretContext.cli.EnsureRes.assert_not_called()


@pytest.mark.parametrize('cfg', [
    SimpleNamespace(meta={'name': 'one', 'labels': {}}, spec={}, yaml=None),
    SimpleNamespace(meta={}, spec={'immutable': False}, yaml=None),
    SimpleNamespace(meta={}, spec={}, yaml='kind: Namespace'),
])
def test_Create_ValidationFailurePrecedesPrivateFiles(
    secretContext: SimpleNamespace,
    cfg: SimpleNamespace,
) -> None:
    failure = ValueError('invalid Secret configuration')
    secretContext.validate.side_effect = failure

    with pytest.raises(ValueError) as caught:
        MUT.Create(secretContext.cli, cfg)

    assert (caught.value is failure)
    secretContext.validate.assert_called_once_with(
        cfg, {'namespace', 'name'}, {'type', 'data', 'stringData'}, 'Secret',
    )
    secretContext.create.assert_not_called()
    secretContext.parser.assert_not_called()
    secretContext.decode.assert_not_called()
    secretContext.temporaryDirectory.assert_not_called()
    secretContext.stringIO.assert_not_called()
    secretContext.csvWriter.assert_not_called()
    secretContext.os.open.assert_not_called()
