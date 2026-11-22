from unittest import mock

import pytest

import libs.Utils.DynLoad as MUT


@pytest.fixture(autouse=True, name='moduleState')
def ModuleState(monkeypatch: pytest.MonkeyPatch) -> mock.MagicMock:
    sysState = mock.MagicMock()
    sysState.modules = {}
    sysState.path = ['/synthetic/import-path']
    sysState.argv = ['original-script', 'original-argument']
    monkeypatch.setattr(MUT, 'sys', sysState)
    return sysState


def PreparePath(
    monkeypatch: pytest.MonkeyPatch,
    pathParts: tuple[str, ...],
    nameParts: tuple[str, ...] | None = None,
    fileExists: bool = True,
    suffix: str = '.py',
    pathText: str = '/synthetic/target',
) -> tuple[mock.Mock, ...]:
    path = mock.MagicMock()
    path.resolve.return_value = path
    path.is_file.return_value = fileExists
    path.parts = pathParts
    path.suffix = suffix
    path.__str__.return_value = pathText
    namePath = mock.MagicMock()
    namePath.parts = pathParts if (nameParts is None) else nameParts
    path.with_suffix.return_value = namePath
    pathFactory = mock.Mock(return_value=path)
    monkeypatch.setattr(MUT, 'Path', pathFactory)
    return (pathFactory, path, namePath)


def PrepareImport(
    monkeypatch: pytest.MonkeyPatch,
    pathParts: tuple[str, ...],
    nameParts: tuple[str, ...] | None = None,
    fileExists: bool = True,
    suffix: str = '.py',
) -> tuple[mock.Mock, ...]:
    (pathFactory, path, namePath) = PreparePath(
        monkeypatch, pathParts, nameParts, fileExists, suffix,
    )
    importer = mock.Mock()
    spec = mock.MagicMock()
    spec.loader = mock.MagicMock()
    module = mock.MagicMock()
    importer.util.spec_from_file_location = mock.Mock(return_value=spec)
    importer.util.module_from_spec = mock.Mock(return_value=module)
    monkeypatch.setattr(MUT, 'importlib', importer)
    return (pathFactory, path, namePath, importer, spec, module)


@pytest.mark.parametrize('filename,expected', [
    ('abc.py', 'abc'),
    ('abc.txt', 'abc_d_txt'),
    ('--.py', '_hh_'),
    ('_._.py', '_udu_'),
    ('-.py', '_h_'),
    ('..py', '_d_'),
    ('_.py', '_u_'),
    ('a--b_._c.py', 'a_hh_b_udu_c'),
    ('_h_.py', '_u_h_u_'),
    ('_hh_.py', '_u_hh_u_'),
    ('_udu_.py', '_u_udu_u_'),
    ('a b+é.py', 'a b+é'),
])
def test_ImportFromPath_UsesExactAbsoluteRunEncoding(
    monkeypatch: pytest.MonkeyPatch,
    moduleState: mock.MagicMock,
    filename: str,
    expected: str,
) -> None:
    suffix = '.py' if filename.endswith('.py') else '.txt'
    pathParts = ('/', 'foo', 'bar', filename)
    nameParts = (
        (pathParts[:-1] + (filename[:-3],))
        if (suffix == '.py') else pathParts
    )
    (pathFactory, path, namePath, importer, spec, module) = PrepareImport(
        monkeypatch, pathParts, nameParts, suffix=suffix,
    )
    moduleName = f'foo.bar.{expected}'
    module.__name__ = moduleName
    beforePath = list(moduleState.path)

    result = MUT.ImportFromPath('relative.py')

    assert (result is module)
    assert (module.__name__ == moduleName)
    assert (moduleState.modules[moduleName] is module)
    assert (moduleState.path == beforePath)
    pathFactory.assert_called_once_with('relative.py')
    path.resolve.assert_called_once_with()
    path.is_file.assert_called_once_with()
    if (suffix == '.py'): path.with_suffix.assert_called_once_with('')
    else: path.with_suffix.assert_not_called()
    importer.util.spec_from_file_location.assert_called_once_with(
        moduleName, path,
    )
    importer.util.module_from_spec.assert_called_once_with(spec)
    spec.loader.exec_module.assert_called_once_with(module)
    assert (namePath.parts == nameParts)


def test_ImportFromPath_RegistersBeforeExecutionAndReturnsModule(
    monkeypatch: pytest.MonkeyPatch,
    moduleState: mock.MagicMock,
) -> None:
    moduleName = 'synthetic.registered'
    (pathFactory, path, namePath, importer, spec, module) = PrepareImport(
        monkeypatch,
        ('/', 'synthetic', 'registered.py'),
        ('/', 'synthetic', 'registered'),
    )
    module.__name__ = moduleName

    def CheckRegistration(loadedModule: mock.MagicMock) -> None:
        assert (moduleState.modules[moduleName] is loadedModule)

    spec.loader.exec_module.side_effect = CheckRegistration
    result = MUT.ImportFromPath('registered.py')

    assert (result is module)
    assert (moduleState.modules[moduleName] is module)
    pathFactory.assert_called_once_with('registered.py')
    importer.util.module_from_spec.assert_called_once_with(spec)
    spec.loader.exec_module.assert_called_once_with(module)
    assert (namePath.parts == ('/', 'synthetic', 'registered'))


def test_ImportFromPath_DistinguishesDirectoriesAndEscapeLookalikes(
    monkeypatch: pytest.MonkeyPatch,
    moduleState: mock.MagicMock,
) -> None:
    encodedNames = []
    for (directory, encoded) in [
        ('-', '_h_'), ('_h_', '_u_h_u_'), ('--', '_hh_'),
        ('_hh_', '_u_hh_u_'), ('_._', '_udu_'), ('_udu_', '_u_udu_u_'),
    ]:
        (pathFactory, path, namePath, importer, spec, module) = PrepareImport(
            monkeypatch,
            ('/', directory, 'prep.py'),
            ('/', directory, 'prep'),
        )
        moduleName = f'{encoded}.prep'
        module.__name__ = moduleName
        result = MUT.ImportFromPath('synthetic/path')
        assert (result is module)
        assert (moduleState.modules[moduleName] is module)
        pathFactory.assert_called_once_with('synthetic/path')
        importer.util.module_from_spec.assert_called_once_with(spec)
        spec.loader.exec_module.assert_called_once_with(module)
        assert (namePath.parts == ('/', directory, 'prep'))
        encodedNames.append(moduleName)
    assert (len(set(encodedNames)) == 6)


def test_ImportFromPath_ResolvedAliasesReloadFreshModulesWithoutParents(
    monkeypatch: pytest.MonkeyPatch,
    moduleState: mock.MagicMock,
) -> None:
    (pathFactory, path, namePath, importer, firstSpec, first) = PrepareImport(
        monkeypatch,
        ('/', 'synthetic', 'fresh.py'),
        ('/', 'synthetic', 'fresh'),
    )
    originalPath = mock.MagicMock()
    aliasPath = mock.MagicMock()
    originalPath.resolve.return_value = path
    aliasPath.resolve.return_value = path
    pathFactory.side_effect = [originalPath, aliasPath]
    secondSpec = mock.MagicMock()
    secondSpec.loader = mock.MagicMock()
    second = mock.MagicMock()
    moduleName = 'synthetic.fresh'
    first.__name__ = moduleName
    second.__name__ = moduleName
    importer.util.spec_from_file_location.side_effect = [firstSpec, secondSpec]
    importer.util.module_from_spec.side_effect = [first, second]
    beforePath = list(moduleState.path)

    firstResult = MUT.ImportFromPath('original.py')
    secondResult = MUT.ImportFromPath('alias.py')

    assert (firstResult is first)
    assert (secondResult is second)
    assert (first is not second)
    assert (first.__name__ == second.__name__)
    assert (moduleState.modules[moduleName] is second)
    assert ('synthetic' not in moduleState.modules)
    assert (moduleState.path == beforePath)
    assert (pathFactory.call_args_list == [
        mock.call('original.py'), mock.call('alias.py'),
    ])
    assert (originalPath.resolve.call_count == 1)
    assert (aliasPath.resolve.call_count == 1)
    assert (path.is_file.call_count == 2)
    assert (firstSpec.loader.exec_module.call_args_list == [mock.call(first)])
    assert (
        secondSpec.loader.exec_module.call_args_list == [mock.call(second)]
    )
    assert (namePath.parts == ('/', 'synthetic', 'fresh'))


@pytest.mark.parametrize('previousKind', ['missing', 'none', 'module'])
@pytest.mark.parametrize(
    'errorType', [RuntimeError, SystemExit, KeyboardInterrupt],
)
def test_ImportFromPath_RestoresRegistryOnBaseException(
    monkeypatch: pytest.MonkeyPatch,
    moduleState: mock.MagicMock,
    previousKind: str,
    errorType: type[BaseException],
) -> None:
    moduleName = 'synthetic.rollback'
    (_, _, _, _, spec, _) = PrepareImport(
        monkeypatch,
        ('/', 'synthetic', 'rollback.py'),
        ('/', 'synthetic', 'rollback'),
    )
    previous = mock.sentinel.previousModule
    if (previousKind == 'none'): moduleState.modules[moduleName] = None
    elif (previousKind == 'module'): moduleState.modules[moduleName] = previous
    error = errorType('synthetic failure')
    spec.loader.exec_module.side_effect = error

    with pytest.raises(errorType) as caught:
        MUT.ImportFromPath('rollback.py')

    assert (caught.value is error)
    if (previousKind == 'missing'):
        assert (moduleName not in moduleState.modules)
    elif (previousKind == 'none'):
        assert (moduleState.modules[moduleName] is None)
    else: assert (moduleState.modules[moduleName] is previous)


def test_ImportFromPath_RejectsMissingFile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (pathFactory, path, _, importer, _, _) = PrepareImport(
        monkeypatch,
        ('/', 'synthetic', 'absent.py'),
        ('/', 'synthetic', 'absent'),
        fileExists=False,
    )

    with pytest.raises(FileNotFoundError, match='Target module'):
        MUT.ImportFromPath('absent.py')

    pathFactory.assert_called_once_with('absent.py')
    path.is_file.assert_called_once_with()
    importer.util.spec_from_file_location.assert_not_called()


def test_ImportFromPath_RejectsUnsupportedExtension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (pathFactory, path, _, importer, _, _) = PrepareImport(
        monkeypatch,
        ('/', 'synthetic', 'present.txt'),
        fileExists=True,
        suffix='.txt',
    )
    importer.util.spec_from_file_location.return_value = mock.Mock(
        loader=None,
    )

    with pytest.raises(ImportError, match='Could not build module spec'):
        MUT.ImportFromPath('present.txt')

    pathFactory.assert_called_once_with('present.txt')
    path.is_file.assert_called_once_with()
    path.with_suffix.assert_not_called()
    importer.util.module_from_spec.assert_not_called()


@pytest.mark.parametrize('noSpec', [True, False])
def test_ImportFromPath_RejectsMissingSpecOrLoader(
    monkeypatch: pytest.MonkeyPatch,
    noSpec: bool,
) -> None:
    (_, _, _, importer, _, _) = PrepareImport(
        monkeypatch,
        ('/', 'synthetic', 'present.py'),
        ('/', 'synthetic', 'present'),
    )
    if noSpec: importer.util.spec_from_file_location.return_value = None
    else:
        spec = mock.Mock()
        spec.loader = None
        importer.util.spec_from_file_location.return_value = spec

    with pytest.raises(ImportError, match='Could not build module spec'):
        MUT.ImportFromPath('present.py')

    importer.util.spec_from_file_location.assert_called_once()
    importer.util.module_from_spec.assert_not_called()


def test_RunFromPath_PassesArgumentsAndRestoresArgvAndPath(
    monkeypatch: pytest.MonkeyPatch,
    moduleState: mock.MagicMock,
) -> None:
    (pathFactory, path, _) = PreparePath(
        monkeypatch,
        ('/', 'synthetic', 'entry.py'),
        pathText='/synthetic/entry.py',
    )
    path.parent.__str__.return_value = '/synthetic'
    # Without `workDir`, the script path is joined to the caller CWD (`.`).
    base = mock.MagicMock()
    base.__truediv__.return_value = path
    pathFactory.return_value = base
    tempEnv = mock.MagicMock()
    tempEnvFactory = mock.Mock(return_value=tempEnv)
    updateEnv = mock.Mock()
    runpy = mock.Mock()
    result = {'observed': 'synthetic'}
    observed = {}

    def CaptureRunPath(
        scriptPath: str,
        init_globals: dict,
        run_name: str,
    ) -> dict:
        observed['argv'] = list(moduleState.argv)
        observed['path'] = list(moduleState.path)
        return result

    runpy.run_path.side_effect = CaptureRunPath
    monkeypatch.setattr(MUT, 'TempEnv', tempEnvFactory)
    monkeypatch.setattr(MUT, 'UpdEnv', updateEnv)
    monkeypatch.setattr(MUT, 'runpy', runpy)
    envVars = {'UNIT_DYN_REMOVE': None, 'UNIT_DYN_VALUE': 7}
    initGlobals = {'injected': 'safe-global'}
    beforeArgs = moduleState.argv
    beforePath = moduleState.path
    chdir = mock.Mock()
    monkeypatch.setattr(MUT, 'chdir', chdir)
    nullContext = mock.MagicMock()
    monkeypatch.setattr(MUT, 'nullcontext', nullContext)

    runResult = MUT.RunFromPath(
        'entry.py', [42, 'tail'], None, envVars, initGlobals,
    )

    assert (runResult is result)
    assert (observed['argv'] == ['/synthetic/entry.py', '42', 'tail'])
    assert (moduleState.argv is beforeArgs)
    # The script directory leads `sys.path` only while the script runs.
    assert (observed['path'] == ['/synthetic', '/synthetic/import-path'])
    assert (moduleState.path is beforePath)
    assert (moduleState.path == ['/synthetic/import-path'])
    # Without `workDir`, the working directory is left alone.
    chdir.assert_not_called()
    nullContext.assert_called_once_with()
    pathFactory.assert_called_once_with('.')
    base.__truediv__.assert_called_once_with('entry.py')
    path.resolve.assert_called_once_with()
    path.is_file.assert_called_once_with()
    tempEnvFactory.assert_called_once_with()
    tempEnv.__enter__.assert_called_once_with()
    tempEnv.__exit__.assert_called_once()
    updateEnv.assert_called_once_with(envVars)
    runpy.run_path.assert_called_once_with(
        '/synthetic/entry.py', init_globals=initGlobals,
        run_name='__main__',
    )


def test_RunFromPath_RejectsMissingFile(
    monkeypatch: pytest.MonkeyPatch,
    moduleState: mock.MagicMock,
) -> None:
    (pathFactory, path, _) = PreparePath(
        monkeypatch,
        ('/', 'synthetic', 'absent.py'),
        fileExists=False,
        pathText='/synthetic/absent.py',
    )
    base = mock.MagicMock()
    base.__truediv__.return_value = path
    pathFactory.return_value = base
    tempEnvFactory = mock.Mock()
    updateEnv = mock.Mock()
    runpy = mock.Mock()
    monkeypatch.setattr(MUT, 'TempEnv', tempEnvFactory)
    monkeypatch.setattr(MUT, 'UpdEnv', updateEnv)
    monkeypatch.setattr(MUT, 'runpy', runpy)

    with pytest.raises(FileNotFoundError, match='Target script'):
        MUT.RunFromPath('absent.py')

    pathFactory.assert_called_once_with('.')
    base.__truediv__.assert_called_once_with('absent.py')
    path.is_file.assert_called_once_with()
    tempEnvFactory.assert_not_called()
    updateEnv.assert_not_called()
    runpy.run_path.assert_not_called()
    assert (moduleState.path == ['/synthetic/import-path'])


def test_UpdEnv_EmptyUpdatesLeaveEnvironmentUnchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {'UNIT_UPD_EXISTING': 'original'}
    monkeypatch.setattr(MUT, 'os', mock.Mock(environ=environment))
    beforeEnv = dict(environment)

    assert (MUT.UpdEnv({}) is None)
    assert (environment == beforeEnv)


def test_UpdEnv_DeletesNoneAndCoercesOtherValues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {'UNIT_UPD_DELETE': 'old', 'UNIT_UPD_EMPTY': 'old'}
    monkeypatch.setattr(MUT, 'os', mock.Mock(environ=environment))
    beforeEnv = dict(environment)
    envUpd = {
        'UNIT_UPD_DELETE': None, 'UNIT_UPD_MISSING': None,
        'UNIT_UPD_EMPTY': '', 'UNIT_UPD_NUMBER': 42,
        'UNIT_UPD_BOOL': False, 'UNIT_UPD_LITERAL': 'None',
    }
    original = dict(envUpd)

    assert (MUT.UpdEnv(envUpd) is None)

    assert ('UNIT_UPD_DELETE' not in environment)
    assert ('UNIT_UPD_MISSING' not in environment)
    assert (environment['UNIT_UPD_EMPTY'] == '')
    assert (environment['UNIT_UPD_NUMBER'] == '42')
    assert (environment['UNIT_UPD_BOOL'] == 'False')
    assert (environment['UNIT_UPD_LITERAL'] == 'None')
    assert (envUpd == original)
    expectedEnv = dict(beforeEnv)
    expectedEnv.pop('UNIT_UPD_DELETE')
    expectedEnv.update({
        'UNIT_UPD_EMPTY': '', 'UNIT_UPD_NUMBER': '42',
        'UNIT_UPD_BOOL': 'False', 'UNIT_UPD_LITERAL': 'None',
    })
    assert (environment == expectedEnv)


def test_UpdEnv_PreservesConversionErrorAndEarlierUpdates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {'UNIT_UPD_EXISTING': 'original'}
    monkeypatch.setattr(MUT, 'os', mock.Mock(environ=environment))
    error = ValueError('synthetic conversion failure')
    value = mock.MagicMock()
    value.__str__.side_effect = error

    with pytest.raises(ValueError) as raised:
        MUT.UpdEnv({'UNIT_UPD_FIRST': 'temporary', 'UNIT_UPD_BAD': value})

    assert (raised.value is error)
    assert (environment['UNIT_UPD_FIRST'] == 'temporary')
    assert (environment['UNIT_UPD_EXISTING'] == 'original')
    assert ('UNIT_UPD_BAD' not in environment)


@pytest.mark.parametrize('outcome', ['return', 'exit', 'raise'])
@pytest.mark.parametrize('workDir', [None, 'work', '/synthetic/work'])
def test_RunFromPath_ScopesScriptStateToRunAndRestoresItOnEveryOutcome(
    monkeypatch: pytest.MonkeyPatch,
    moduleState: mock.MagicMock,
    workDir: str | None,
    outcome: str,
) -> None:
    (pathFactory, path, _) = PreparePath(
        monkeypatch,
        ('/', 'synthetic', 'entry.py'),
        pathText='/synthetic/entry.py',
    )
    path.parent.__str__.return_value = '/synthetic'
    base = mock.MagicMock()
    base.__truediv__.return_value = path
    pathFactory.return_value = base
    tempEnv = mock.MagicMock()
    updateEnv = mock.Mock()
    # The script runs in `workDir` if given, else in the caller CWD.
    dirContext = mock.MagicMock()
    chdir = mock.Mock(return_value=dirContext)
    nullContext = mock.Mock(return_value=dirContext)
    runpy = mock.Mock()
    result = {'observed': 'synthetic'}
    error = {
        'return': None, 'exit': SystemExit(3),
        'raise': RuntimeError('synthetic script failure'),
    }[outcome]
    observed = {}

    def CaptureRunPath(
        scriptPath: str,
        init_globals: dict,
        run_name: str,
    ) -> dict:
        observed['argv'] = list(moduleState.argv)
        observed['path'] = list(moduleState.path)
        observed['envUpdates'] = list(updateEnv.call_args_list)
        observed['contexts'] = [
            (context.__enter__.call_count, context.__exit__.call_count)
            for context in (tempEnv, dirContext)
        ]
        # Script changes to the replaced lists must not reach the caller.
        moduleState.argv.append('polluted-by-script')
        moduleState.path.append('/polluted/by/script')
        if (error is not None): raise error
        return result

    runpy.run_path.side_effect = CaptureRunPath
    monkeypatch.setattr(MUT, 'TempEnv', mock.Mock(return_value=tempEnv))
    monkeypatch.setattr(MUT, 'UpdEnv', updateEnv)
    monkeypatch.setattr(MUT, 'chdir', chdir)
    monkeypatch.setattr(MUT, 'nullcontext', nullContext)
    monkeypatch.setattr(MUT, 'runpy', runpy)
    initGlobals = {'injected': 'safe-global'}
    beforeArgs = moduleState.argv
    beforePath = moduleState.path
    runArgs = (
        'entry.py', ['first', 2], workDir, None, initGlobals, 'synthetic_run',
    )

    if (error is None): assert (MUT.RunFromPath(*runArgs) is result)
    else:
        with pytest.raises(type(error)) as raised:
            MUT.RunFromPath(*runArgs)
        assert (raised.value is error)

    # Like `python <script>`, while the script runs `sys.argv` holds it and
    #   its arguments, and its directory leads `sys.path`.
    assert (observed['argv'] == ['/synthetic/entry.py', 'first', '2'])
    assert (observed['path'] == ['/synthetic', '/synthetic/import-path'])
    # The environment updates and the directory apply only during the run.
    assert (observed['envUpdates'] == [mock.call({})])
    assert (observed['contexts'] == [(1, 0), (1, 0)])
    tempEnv.__exit__.assert_called_once()
    dirContext.__exit__.assert_called_once()
    # The original objects are restored without the script changes.
    assert (moduleState.argv is beforeArgs)
    assert (moduleState.argv == ['original-script', 'original-argument'])
    assert (moduleState.path is beforePath)
    assert (moduleState.path == ['/synthetic/import-path'])
    # A relative script path is joined to `workDir` if given, else to `.`.
    pathFactory.assert_called_once_with((workDir or '.'))
    base.__truediv__.assert_called_once_with('entry.py')
    if (workDir is None):
        nullContext.assert_called_once_with()
        chdir.assert_not_called()
    else:
        chdir.assert_called_once_with(workDir)
        nullContext.assert_not_called()
    runpy.run_path.assert_called_once_with(
        '/synthetic/entry.py', init_globals=initGlobals,
        run_name='synthetic_run',
    )


def test_RunFromPath_PropagatesWorkDirErrorAndRestoresState(
    monkeypatch: pytest.MonkeyPatch,
    moduleState: mock.MagicMock,
) -> None:
    (pathFactory, path, _) = PreparePath(
        monkeypatch,
        ('/', 'synthetic', 'entry.py'),
        pathText='/synthetic/entry.py',
    )
    path.parent.__str__.return_value = '/synthetic'
    base = mock.MagicMock()
    base.__truediv__.return_value = path
    pathFactory.return_value = base
    tempEnv = mock.MagicMock()
    # Entering an invalid `workDir` fails before the script runs.
    error = NotADirectoryError('synthetic work directory')
    chdir = mock.MagicMock()
    chdir.return_value.__enter__.side_effect = error
    runpy = mock.Mock()
    monkeypatch.setattr(MUT, 'TempEnv', mock.Mock(return_value=tempEnv))
    monkeypatch.setattr(MUT, 'UpdEnv', mock.Mock())
    monkeypatch.setattr(MUT, 'chdir', chdir)
    monkeypatch.setattr(MUT, 'runpy', runpy)
    beforeArgs = moduleState.argv
    beforePath = moduleState.path

    with pytest.raises(NotADirectoryError) as raised:
        MUT.RunFromPath('entry.py', ['first'], 'work')

    assert (raised.value is error)
    chdir.assert_called_once_with('work')
    # The script never ran and every scoped state change was undone.
    runpy.run_path.assert_not_called()
    tempEnv.__exit__.assert_called_once()
    assert (moduleState.argv is beforeArgs)
    assert (moduleState.argv == ['original-script', 'original-argument'])
    assert (moduleState.path is beforePath)
    assert (moduleState.path == ['/synthetic/import-path'])
