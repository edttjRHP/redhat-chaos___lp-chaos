from collections.abc import Iterator
from importlib.machinery import ModuleSpec, SourceFileLoader
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from unittest import mock

import pytest

import libs.Utils.DynLoad as MUT


@pytest.fixture(autouse=True)
def ModuleRegistry() -> Iterator[None]:
    with mock.patch.dict(sys.modules):
        yield


def _ExpectedImportName(filename: str) -> str:
    '''Mirror ImportFromPath encoding for `/foo/bar/<filename>`.'''
    path = Path('/foo/bar') / filename
    namePath = path.with_suffix('') if (path.suffix == '.py') else path
    escapes = str.maketrans('-._', 'hdu')
    import re
    return '.'.join(
        re.sub(
            r'[-._]+',
            lambda match: f'_{match.group().translate(escapes)}_',
            component,
        )
        for component in namePath.parts[1:]
    )


@pytest.mark.parametrize('filename', [
    'abc.py',
    'abc.txt',
    '--.py',
    '_._.py',
    '-.py',
    '..py',
    '_.py',
    'a--b_._c.py',
    '_h_.py',
    '_hh_.py',
    '_udu_.py',
    'a b+é.py',
])
def test_ImportFromPath_UsesExactAbsoluteRunEncoding(
    monkeypatch: pytest.MonkeyPatch, filename: str,
) -> None:
    path = Path('/foo/bar') / filename
    monkeypatch.setattr(Path, 'resolve', lambda self: path)
    monkeypatch.setattr(Path, 'is_file', lambda self: True)
    specFactory = MUT.importlib.util.spec_from_file_location

    def CreateSpec(name: str, location: Path) -> ModuleSpec:
        spec = specFactory(
            name, location, loader=SourceFileLoader(name, str(location)),
        )
        spec.loader.exec_module = mock.Mock()
        return spec

    monkeypatch.setattr(MUT.importlib.util, 'spec_from_file_location',
                        CreateSpec)
    module = MUT.ImportFromPath('relative.py')
    assert (module.__name__ == _ExpectedImportName(filename))
    assert (module.__spec__.name == module.__name__)
    assert (sys.modules[module.__name__] is module)


def test_ImportFromPath_RegistersBeforeExecutionAndReturnsModule(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'registered.py'
    path.write_text(
        'import sys\n'
        'registered = sys.modules[__name__]\n'
        'value = 42\n'
    )
    module = MUT.ImportFromPath(path)
    assert (module.registered is module)
    assert (module.value == 42)
    assert (module.__spec__.name == module.__name__)
    assert (sys.modules[module.__name__] is module)
    assert (not module.__name__.startswith('.'))
    assert (module.__file__ == str(path.resolve()))


def test_ImportFromPath_DistinguishesDirectoriesAndEscapeLookalikes(
    tmp_path: Path,
) -> None:
    modules = []
    for directory in ('-', '_h_', '--', '_hh_', '_._', '_udu_'):
        folder = tmp_path / directory
        folder.mkdir()
        path = folder / 'prep.py'
        path.write_text(f'value = {directory!r}\n')
        module = MUT.ImportFromPath(path)
        assert (module.value == directory)
        assert (sys.modules[module.__name__] is module)
        modules.append(module)
    assert (len({module.__name__ for module in modules}) == 6)


def test_ImportFromPath_ResolvedAliasesReloadWithoutCreatingParents(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'fresh.py'
    path.write_text('value = object()\n')
    alias = tmp_path / 'alias.py'
    alias.symlink_to(path)
    beforePath = list(sys.path)
    first = MUT.ImportFromPath(str(path))
    second = MUT.ImportFromPath(alias)
    assert (first.__name__ == second.__name__)
    assert (first is not second)
    assert (first.value is not second.value)
    assert (sys.modules[first.__name__] is second)
    assert (sys.path == beforePath)
    parent = first.__name__.rsplit('.', 1)[0]
    assert (parent not in sys.modules)


@pytest.mark.parametrize('previousKind', ['missing', 'none', 'module'])
@pytest.mark.parametrize('errorType', [RuntimeError, SystemExit,
                                      KeyboardInterrupt])
def test_ImportFromPath_RestoresRegistryOnBaseException(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    previousKind: str, errorType: type[BaseException],
) -> None:
    path = tmp_path / 'rollback.py'
    path.write_text('value = 1\n')
    initial = MUT.ImportFromPath(path)
    name = initial.__name__
    if (previousKind == 'missing'): del sys.modules[name]
    elif (previousKind == 'none'): sys.modules[name] = None
    error = errorType('synthetic failure')
    monkeypatch.setattr(sys, '_dynload_test_error', error, raising=False)
    path.write_text('import sys\nraise sys._dynload_test_error\n')
    with pytest.raises(errorType) as caught:
        MUT.ImportFromPath(path)
    assert (caught.value is error)
    if (previousKind == 'missing'): assert (name not in sys.modules)
    elif (previousKind == 'none'): assert (sys.modules[name] is None)
    else: assert (sys.modules[name] is initial)


def test_ImportFromPath_RejectsMissingFile(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match='Target module'):
        MUT.ImportFromPath(tmp_path / 'absent.py')


def test_ImportFromPath_RejectsUnsupportedExtension(tmp_path: Path) -> None:
    path = tmp_path / 'present.txt'
    path.write_text('')
    with pytest.raises(ImportError, match='Could not build module spec'):
        MUT.ImportFromPath(path)


@pytest.mark.parametrize('noSpec', [True, False])
def test_ImportFromPath_RejectsMissingSpecOrLoader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, noSpec: bool,
) -> None:
    path = tmp_path / 'present.py'
    path.write_text('')
    monkeypatch.setattr(
        MUT.importlib.util, 'spec_from_file_location',
        lambda *args: None if noSpec else mock.Mock(loader=None),
    )
    with pytest.raises(ImportError, match='Could not build module spec'):
        MUT.ImportFromPath(path)


def test_ImportFromPath_AnnotatedDataclassPrepThroughKrknHub(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[4]
    observedPath = tmp_path / 'observed.json'
    venvBin = tmp_path / '.venv' / 'bin'
    venvBin.mkdir(parents=True)
    venvPython = venvBin / 'python'
    venvPython.write_text(
        '#!/bin/sh\n'
        f'exec {sys.executable!r} "$@"\n'
    )
    venvPython.chmod(0o755)
    (tmp_path / 'env.sh').write_text(
        'read -r value || :\n'
        'export SYNTHETIC_VALUE=sourced\n'
        'unset SYNTHETIC_REMOVE\n'
        'printf "synthetic output\\n"\n'
    )
    (tmp_path / 'prep.py').write_text(
        'from __future__ import annotations\n'
        'from dataclasses import dataclass\n'
        'import os, sys\n'
        '@dataclass\n'
        'class Config:\n'
        '    value: str\n'
        'def BuildArgs():\n'
        '    assert sys.modules[__name__].Config is Config\n'
        '    return [Config(os.environ["SYNTHETIC_VALUE"]).value]\n'
    )
    (tmp_path / 'entry.py').write_text(
        'import json, os, sys\n'
        f'path = {str(observedPath)!r}\n'
        'with open(path, "w", encoding="utf-8") as handle:\n'
        '    json.dump([sys.argv[1:], os.getenv("SYNTHETIC_VALUE"),\n'
        '               os.getenv("SYNTHETIC_REMOVE")], handle)\n'
    )
    code = (
        'import json, os, sys\n'
        f'sys.path.insert(0, {str(root)!r})\n'
        'from fixtures.Krkn import KrknHub, '
        'FuncParsKrknHub_Run_krknPrepMod\n'
        'before = dict(os.environ)\n'
        f'hub = KrknHub({str(tmp_path)!r}, '
        f'{str(tmp_path / "entry.py")!r}, "synthetic-config")\n'
        'hub.Run("env.sh", FuncParsKrknHub_Run_krknPrepMod("prep.py"))\n'
        'assert dict(os.environ) == before\n'
        f'print(open({str(observedPath)!r}, encoding="utf-8").read())\n'
    )
    process = subprocess.Popen(
        [sys.executable, '-c', code], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True,
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
             'HOME': str(tmp_path), 'SYNTHETIC_REMOVE': 'safe'},
    )
    try:
        stdout, stderr = process.communicate(timeout=5)
    finally:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait(timeout=5)
    assert (process.returncode == 0), stderr
    assert (json.loads(stdout) == [['sourced'], 'sourced', None])
    assert ('synthetic output' in stderr)


def test_RunFromPath_PreservesEnvironmentAndArguments(tmp_path: Path) -> None:
    path = tmp_path / 'entry.py'
    path.write_text(
        'import os, sys\n'
        'observed = (os.getenv("SYNTHETIC_REMOVE"),\n'
        '            os.getenv("SYNTHETIC_VALUE"), sys.argv[:], injected)\n'
    )
    beforeArgs = sys.argv
    with mock.patch.dict(os.environ, {'SYNTHETIC_REMOVE': 'before'}):
        beforeEnv = dict(os.environ)
        result = MUT.RunFromPath(
            path, [42], {'SYNTHETIC_REMOVE': None, 'SYNTHETIC_VALUE': 7},
            {'injected': 'safe-global'},
        )
        assert (result['observed'] == (
            None, '7', [str(path), '42'], 'safe-global',
        ))
        assert (dict(os.environ) == beforeEnv)
    assert (sys.argv is beforeArgs)


def test_RunFromPath_RejectsMissingFile(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match='Target script'):
        MUT.RunFromPath(tmp_path / 'absent.py')
