from collections.abc import Iterator
from multiprocessing.connection import Connection
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import libs.Utils.LibInVenv_Worker as MUT


@pytest.fixture(name='workerPaths')
def WorkerPaths(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    paths = {
        'stdlib': '/unit/venv/lib',
        'platstdlib': '/unit/venv/platform',
        'purelib': '/unit/venv/site-packages',
        'platlib': '/unit/venv/site-packages',
    }
    monkeypatch.setattr(MUT, 'sys', SimpleNamespace(
        implementation=SimpleNamespace(name='cpython'),
        version_info=(3, 11, 8, 'final', 0),
        prefix='/unit/venv', base_prefix='/unit/base',
        path=['/parent/packages'],
    ))
    workerEnv = {}
    monkeypatch.setattr(MUT, 'os', SimpleNamespace(
        environ=workerEnv, getenv=workerEnv.get, pathsep=os.pathsep,
    ))
    for name in ('sysconfig', 'site', 'importlib', 'traceback'):
        monkeypatch.setattr(MUT, name, mock.Mock())
    MUT.sysconfig.get_paths.return_value = paths
    MUT.sysconfig.get_config_var.return_value = None
    MUT.traceback.format_exc.return_value = 'RuntimeError: worker traceback'
    with mock.patch.object(
        Path, 'resolve', autospec=True, side_effect=lambda path: path,
    ):
        yield paths


@pytest.fixture(name='sndPipe')
def SndPipe() -> mock.Mock:
    return mock.Mock(spec=Connection)


@pytest.mark.parametrize('pythonPath', [None, '', '/extra:/first:/last'])
@pytest.mark.parametrize('sharedPath', [None, '/native-extensions'])
def test__CallInVenv_RebuildsOrderedPathsAndReturnsTaggedResult(
    workerPaths: dict[str, str],
    pythonPath: str | None,
    sharedPath: str | None,
) -> None:
    if (pythonPath is not None):
        MUT.os.environ['PYTHONPATH'] = pythonPath.replace(':', os.pathsep)
    MUT.sysconfig.get_config_var.return_value = sharedPath
    module = MUT.importlib.import_module.return_value
    module.Call.return_value = ('result', 42)
    sndPipe = mock.Mock(spec=Connection)
    beforePath = MUT.sys.path
    stdlib = Path(workerPaths['stdlib'])
    zipName = 'python{}{}.zip'.format(*MUT.sys.version_info[:2])
    expectedPaths = [
        '/first', '/extra', *(['/last'] if pythonPath else []),
        str((stdlib.parent / zipName)), workerPaths['stdlib'],
        workerPaths['platstdlib'], (sharedPath or str((stdlib / 'DLLs'))),
    ]

    with mock.patch.object(MUT.sys, 'path', list(beforePath)):
        MUT._CallInVenv(
            sndPipe, 'target', 'Call', (7,), {'option': 'value'},
            ('/first', '/extra', '/first'), MUT.sys.prefix,
            tuple(MUT.sys.version_info),
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


@pytest.mark.parametrize('invalid', [
    'implementation', 'version', 'major', 'minor', 'micro', 'release-level',
    'serial', 'root', 'not-venv',
])
def test__CallInVenv_RejectsInvalidRuntimeWithNotedException(
    workerPaths: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    sndPipe: mock.Mock,
    invalid: str,
) -> None:
    pyVer = tuple(MUT.sys.version_info)
    if (invalid == 'implementation'):
        monkeypatch.setattr(MUT.sys.implementation, 'name', 'pypy')
    elif (invalid == 'version'): pyVer = (0, 0)
    elif (invalid in ('major', 'minor', 'micro', 'release-level', 'serial')):
        pyVer = list(pyVer)
        index = ('major', 'minor', 'micro', 'release-level', 'serial').index(
            invalid
        )
        pyVer[index] = 'candidate' if (index == 3) else (pyVer[index] + 1)
        pyVer = tuple(pyVer)
    elif (invalid == 'not-venv'):
        monkeypatch.setattr(MUT.sys, 'prefix', MUT.sys.base_prefix)
    venvRoot = MUT.sys.prefix + ('/missing' if (invalid == 'root') else '')

    MUT._CallInVenv(sndPipe, 'target', 'Call', (), {}, (), venvRoot, pyVer)
    (exitType, error) = sndPipe.send.call_args.args[0]

    assert (exitType is MUT.ExitType.EXCEPTION)
    assert (type(error) is RuntimeError)
    assert ('same exact Version' in str(error))
    assert ('RuntimeError' in error.__notes__[0])
    MUT.sysconfig.get_paths.assert_not_called()
    MUT.importlib.import_module.assert_not_called()
    sndPipe.close.assert_called_once_with()


@pytest.mark.parametrize(
    'outcome',
    ['return', 'exception', 'bad-result', 'bad-exception', 'closed-pipe'],
)
def test__CallInVenv_TransportsResultsAndExceptionsOrExits32(
    workerPaths: dict[str, str],
    sndPipe: mock.Mock,
    outcome: str,
) -> None:
    target = MUT.importlib.import_module.return_value.Call
    target.return_value = None
    original = ValueError('original target failure', 7)
    original.detail = 'retained attribute'
    if (outcome in ('exception', 'bad-exception')):
        target.side_effect = original
    if (outcome == 'exception'):
        MUT.traceback.format_exc.return_value = 'original target failure'
    if (outcome == 'bad-exception'):
        original.callback = lambda: None
        sndPipe.send.side_effect = [AttributeError('<lambda>'), None]
        MUT.traceback.format_exc.side_effect = [
            'original target failure', '<lambda> transport failure',
        ]
    if (outcome == 'bad-result'):
        target.return_value = lambda: None
        sndPipe.send.side_effect = [AttributeError('<lambda>'), None]
        MUT.traceback.format_exc.return_value = 'AttributeError: <lambda>'
    if (outcome == 'closed-pipe'):
        sndPipe.send.side_effect = OSError('closed pipe')

    with mock.patch.object(MUT.sys, 'path', list(MUT.sys.path)):
        if (outcome == 'closed-pipe'):
            with pytest.raises(SystemExit) as raised:
                MUT._CallInVenv(
                    sndPipe, 'target', 'Call', (), {}, (), MUT.sys.prefix,
                    tuple(MUT.sys.version_info),
                )
            assert (raised.value.code == 32)
        else:
            MUT._CallInVenv(
                sndPipe, 'target', 'Call', (), {}, (), MUT.sys.prefix,
                tuple(MUT.sys.version_info),
            )

    target.assert_called_once_with()
    sndPipe.close.assert_called_once_with()
    if (outcome == 'closed-pipe'):
        assert (sndPipe.send.call_count == 3)
        return
    (exitType, result) = sndPipe.send.call_args.args[0]
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
