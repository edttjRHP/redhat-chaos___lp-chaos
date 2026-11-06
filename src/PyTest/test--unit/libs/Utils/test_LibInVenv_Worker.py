from collections.abc import Iterator
import multiprocessing as mp
from multiprocessing.connection import Connection
import os
from pathlib import Path
from unittest import mock

import pytest

import libs.Utils.LibInVenv_Worker as MUT
import Utils


@pytest.fixture(name='workerPaths')
def WorkerPaths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, str]:
    paths = {
        'stdlib': str(tmp_path / 'lib'),
        'platstdlib': str(tmp_path / 'platform'),
        'purelib': str(tmp_path / 'site-packages'),
        'platlib': str(tmp_path / 'site-packages'),
    }
    Utils.AutoPatch(monkeypatch, MUT.sysconfig, 'get_paths')
    Utils.AutoPatch(monkeypatch, MUT.sysconfig, 'get_config_var')
    Utils.AutoPatch(monkeypatch, MUT.site, 'addsitedir')
    Utils.AutoPatch(monkeypatch, MUT.importlib, 'invalidate_caches')
    Utils.AutoPatch(monkeypatch, MUT.importlib, 'import_module')
    MUT.sysconfig.get_paths.return_value = paths
    MUT.sysconfig.get_config_var.return_value = None
    monkeypatch.delenv('PYTHONPATH', raising=False)
    return paths


@pytest.fixture(name='pipeEnds')
def PipeEnds() -> Iterator[tuple[Connection, Connection]]:
    (rcvPipe, sndPipe) = mp.get_context('spawn').Pipe(duplex=False)
    try:
        yield (rcvPipe, sndPipe)
    finally:
        sndPipe.close()
        rcvPipe.close()


@pytest.mark.parametrize('pythonPath', [None, '', '/extra:/first:/last'])
@pytest.mark.parametrize('sharedPath', [None, '/native-extensions'])
def test__CallInVenv_RebuildsOrderedPathsAndReturnsTaggedResult(
    workerPaths: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    pythonPath: str | None,
    sharedPath: str | None,
) -> None:
    if (pythonPath is not None):
        monkeypatch.setenv('PYTHONPATH', pythonPath.replace(':', os.pathsep))
    MUT.sysconfig.get_config_var.return_value = sharedPath
    module = MUT.importlib.import_module.return_value
    module.Call.return_value = ('result', 42)
    sndPipe = mock.Mock(spec=Connection)
    beforePath = MUT.sys.path
    stdlib = Path(workerPaths['stdlib'])
    zipName = 'python{}{}.zip'.format(*MUT.sys.version_info[:2])
    expectedPaths = [
        '/first', '/extra', *(['/last'] if pythonPath else []),
        str(stdlib.parent / zipName), workerPaths['stdlib'],
        workerPaths['platstdlib'], (sharedPath or str(stdlib / 'DLLs')),
    ]

    with mock.patch.object(MUT.sys, 'path', list(beforePath)):
        MUT._CallInVenv(
            sndPipe, 'target', 'Call', (7,), {'option': 'value'},
            ('/first', '/extra', '/first'), MUT.sys.prefix,
            tuple(MUT.sys.version_info[:2]),
        )
        assert (MUT.sys.path == expectedPaths)

    assert (MUT.sys.path is beforePath)
    MUT.site.addsitedir.assert_called_once_with(workerPaths['purelib'])
    MUT.sysconfig.get_config_var.assert_called_once_with('DESTSHARED')
    MUT.importlib.invalidate_caches.assert_called_once_with()
    MUT.importlib.import_module.assert_called_once_with('target')
    module.Call.assert_called_once_with(7, option='value')
    sndPipe.send.assert_called_once_with((MUT.ExitType.RETURN, ('result', 42)))
    sndPipe.close.assert_called_once_with()


@pytest.mark.parametrize('invalid', ['implementation', 'version', 'root',
                                     'not-venv'])
def test__CallInVenv_RejectsInvalidRuntimeWithNotedException(
    workerPaths: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    pipeEnds: tuple[Connection, Connection],
    invalid: str,
) -> None:
    (rcvPipe, sndPipe) = pipeEnds
    pyVer = tuple(MUT.sys.version_info[:2])
    if (invalid == 'implementation'):
        monkeypatch.setattr(MUT.sys.implementation, 'name', 'pypy')
    elif (invalid == 'version'): pyVer = (0, 0)
    elif (invalid == 'not-venv'):
        monkeypatch.setattr(MUT.sys, 'prefix', MUT.sys.base_prefix)
    venvRoot = MUT.sys.prefix + ('/missing' if (invalid == 'root') else '')

    MUT._CallInVenv(sndPipe, 'target', 'Call', (), {}, (), venvRoot, pyVer)
    (exitType, error) = rcvPipe.recv()

    assert (exitType is MUT.ExitType.EXCEPTION)
    assert (type(error) is RuntimeError)
    assert ('same Major/Minor Version' in str(error))
    assert ('RuntimeError' in error.__notes__[0])
    MUT.sysconfig.get_paths.assert_not_called()
    MUT.importlib.import_module.assert_not_called()
    assert sndPipe.closed


@pytest.mark.parametrize('outcome', ['return', 'exception', 'bad-result',
                                     'bad-exception', 'closed-pipe'])
def test__CallInVenv_TransportsResultsAndExceptionsOrExits32(
    workerPaths: dict[str, str],
    pipeEnds: tuple[Connection, Connection],
    outcome: str,
) -> None:
    (rcvPipe, sndPipe) = pipeEnds
    target = MUT.importlib.import_module.return_value.Call
    target.return_value = None
    original = ValueError('original target failure', 7)
    original.detail = 'retained attribute'
    if (outcome in ('exception', 'bad-exception')):
        target.side_effect = original
    if (outcome == 'bad-exception'): original.callback = lambda: None
    if (outcome == 'bad-result'): target.return_value = lambda: None
    if (outcome == 'closed-pipe'): sndPipe.close()

    with mock.patch.object(MUT.sys, 'path', list(MUT.sys.path)):
        if (outcome == 'closed-pipe'):
            with pytest.raises(SystemExit) as raised:
                MUT._CallInVenv(
                    sndPipe, 'target', 'Call', (), {}, (), MUT.sys.prefix,
                    tuple(MUT.sys.version_info[:2]),
                )
            assert (raised.value.code == 32)
        else:
            MUT._CallInVenv(
                sndPipe, 'target', 'Call', (), {}, (), MUT.sys.prefix,
                tuple(MUT.sys.version_info[:2]),
            )

    target.assert_called_once_with()
    assert sndPipe.closed
    if (outcome == 'closed-pipe'):
        with pytest.raises(EOFError): rcvPipe.recv()
        return
    (exitType, result) = rcvPipe.recv()
    if (outcome == 'return'):
        assert (exitType is MUT.ExitType.RETURN)
        assert (result is None)
    else:
        assert (exitType is MUT.ExitType.EXCEPTION)
        if (outcome == 'exception'):
            assert (type(result) is ValueError)
            assert (result.args == original.args)
            assert (result.detail == original.detail)
            assert ('original target failure' in result.__notes__[0])
        elif (outcome == 'bad-result'):
            assert (type(result) is AttributeError)
            assert ('<lambda>' in str(result))
            assert ('AttributeError' in result.__notes__[0])
        else:
            assert (type(result) is RuntimeError)
            assert ('original target failure' in str(result))
            assert ('<lambda>' in str(result))
