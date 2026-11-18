import dataclasses
from datetime import timezone
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest import mock

import pytest

import fixtures.Krkn as MUT


lexicalScenarioPattern = r'(^/|(^|/)\.\.(/|$))'
scenarioLabelPattern = (
    r'(?im)^\s*LABEL\s+'
    r'krknctl\.is_a_scenario\s*=\s*[\'"]?true[\'"]?\s*$'
)


class PathMocks:
    '''Create lexical path values whose filesystem operations are mocks.'''

    def __init__(self) -> None:
        self.values = {}
        self.factory = mock.Mock(side_effect=self.Make)

    def Make(self, value: object) -> mock.MagicMock:
        pathValue = PurePosixPath(str(value))
        if (pathValue in self.values): return self.values[pathValue]
        path = mock.MagicMock(name=f'Path({pathValue})')
        self.values[pathValue] = path
        path.__str__.return_value = str(pathValue)
        path.__fspath__.return_value = str(pathValue)
        path.name = pathValue.name
        path.exists.return_value = False
        path.is_file.return_value = False
        path.is_dir.return_value = False
        path.read_text.return_value = ''
        path.read_bytes.return_value = b''
        path.write_text.return_value = 0
        path.write_bytes.return_value = 0
        parent = pathValue.parent
        path.parent = path if (parent == pathValue) else self.Make(parent)
        path.resolve.return_value = path
        path.__truediv__.side_effect = lambda child: self.Make(
            (pathValue / PurePosixPath(str(child)))
        )
        return path

    def ResolveAs(self, source: object, resolved: object) -> None:
        self.Make(source).resolve.return_value = self.Make(resolved)


class FormattedTime:
    '''Forward the format request to a canned result without formatting.'''

    def __init__(self, formatter: mock.Mock) -> None:
        self.formatter = formatter

    def __format__(self, formatSpec: str) -> str:
        return self.formatter(formatSpec)


@pytest.fixture(name='testBoundaries', autouse=True)
def TestBoundaries(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    paths = PathMocks()
    environ = {}
    getenv = mock.Mock(side_effect=environ.get)
    osProxy = SimpleNamespace(
        environ=environ,
        getenv=getenv,
        path=SimpleNamespace(expanduser=mock.Mock()),
    )
    formatNow = mock.Mock(return_value='20260203T040506Z')
    formattedNow = FormattedTime(formatNow)
    clock = mock.Mock()
    clock.now.return_value = formattedNow
    tempEnv = mock.MagicMock()
    tempEnv.return_value.__enter__.return_value = None
    tempEnv.return_value.__exit__.return_value = False
    chdir = mock.MagicMock()
    chdir.return_value.__enter__.return_value = None
    chdir.return_value.__exit__.return_value = False
    source = mock.Mock()
    updateEnv = mock.Mock()
    getEnvDelta = mock.Mock(return_value={})
    stream = mock.Mock()
    stream.getvalue.return_value = ''
    stringIO = mock.Mock(return_value=stream)
    parsedOptions = {'sourceFile': {'envVar': ':'}}
    yamlToDict = mock.Mock(return_value=parsedOptions)
    regexSearch = mock.Mock(
        side_effect=([None, mock.sentinel.scenarioLabelMatch] * 2),
    )
    options = SimpleNamespace(sourceFile=SimpleNamespace(envVar=':'))
    namedDict = mock.Mock(
        side_effect=lambda *args, **kwargs: SimpleNamespace(**kwargs),
    )
    namedDict.FromDict = mock.Mock(return_value=options)
    success = SimpleNamespace(
        success=True, stdout=b'canned-render', stderr=b'', exitStat=0
    )
    commandRun = mock.Mock(return_value=success)
    commandExecutor = SimpleNamespace(Run=commandRun)
    commandFactory = mock.Mock(return_value=commandExecutor)
    runner = mock.Mock(return_value={'runner': 'canned-result'})
    defaults = mock.MagicMock()
    defaults.kCfg = '/synthetic/config'
    defaults.ns = 'synthetic-ns'
    defaults.cliCfg = SimpleNamespace(ocCfg={}, vcCfg={})
    defaults.fxtInf = SimpleNamespace(
        rootPath=SimpleNamespace(
            krkn=PurePosixPath('/repo/vendor/krkn'),
            krknHub=PurePosixPath('/repo/vendor/krknHub'),
        ),
        krknCfg=SimpleNamespace(krknEnv={}),
    )
    defaults.testPars = SimpleNamespace()
    defaultMiddle = mock.MagicMock()
    defaultFinal = SimpleNamespace(
        fxtInf=defaults.fxtInf, testPars=defaults.testPars,
        ns=defaults.ns, kCfg=defaults.kCfg, cliCfg=defaults.cliCfg,
    )
    defaults.__or__.return_value = defaultMiddle
    defaultMiddle.__or__.return_value = defaultFinal
    monkeypatch.setattr(MUT, 'Path', paths.factory)
    monkeypatch.setattr(MUT, 'os', osProxy)
    monkeypatch.setattr(MUT, 'datetime', clock)
    monkeypatch.setattr(MUT, 'TempEnv', tempEnv)
    monkeypatch.setattr(MUT, 'UpdEnv', updateEnv)
    monkeypatch.setattr(MUT, 'DoShellSource', source)
    monkeypatch.setattr(MUT, 'GetEnvDelta', getEnvDelta)
    monkeypatch.setattr(MUT, 'StringIO', stringIO)
    monkeypatch.setattr(MUT, 'BashEnv', mock.sentinel.BashEnv)
    monkeypatch.setattr(MUT, 'YAMLcfg', SimpleNamespace(ToDict=yamlToDict))
    monkeypatch.setattr(MUT, 're', SimpleNamespace(search=regexSearch))
    monkeypatch.setattr(MUT, 'NamedDict', namedDict)
    monkeypatch.setattr(MUT, 'RunFromPath', runner)
    monkeypatch.setattr(MUT, 'CmdExec', commandFactory)
    monkeypatch.setattr(MUT, 'chdir', chdir)
    monkeypatch.setattr(MUT, 'logs', mock.Mock())
    monkeypatch.setattr(MUT, '_DEF__FXT_CFG', defaults)
    return SimpleNamespace(
        paths=paths, environ=environ, getenv=getenv, clock=clock,
        regexSearch=regexSearch, formatNow=formatNow,
        tempEnv=tempEnv, chdir=chdir, source=source,
        updateEnv=updateEnv, getEnvDelta=getEnvDelta, stream=stream,
        stringIO=stringIO, parsedOptions=parsedOptions,
        namedDict=namedDict, defaults=defaults,
        commandRun=commandRun, commandFactory=commandFactory,
        runner=runner, yamlToDict=yamlToDict,
    )


@pytest.fixture(name='fixtureRequest')
def FixtureRequest(testBoundaries: SimpleNamespace) -> SimpleNamespace:
    root = testBoundaries.paths.Make('/repo/tests')
    return SimpleNamespace(config=SimpleNamespace(rootpath=root))


@pytest.fixture(name='hubContext')
def HubContext(testBoundaries: SimpleNamespace) -> SimpleNamespace:
    context = testBoundaries
    paths = context.paths
    kHubRoot = paths.Make('/synthetic/hub')
    krknRoot = paths.Make('/synthetic/krkn')
    scnDir = kHubRoot / 'pod-scenarios'
    runRoot = paths.Make('/tmp/run-dir--krkn')
    runDir = runRoot / '20260203T040506Z--pod-scenarios'
    scnFile = runDir / 'scenarios/synthetic.yaml'
    cfgFile = runDir / 'config/chaos.yaml'
    script = krknRoot / 'run_kraken.py'
    descriptor = scnDir / 'Dockerfile.template'
    descriptor.is_file.return_value = True
    descriptor.read_text.return_value = 'LABEL krknctl.is_a_scenario=true'
    scnTemplate = scnDir / 'selected.yaml.template'
    cfgTemplate = kHubRoot / 'config.yaml.template'
    scnTemplate.read_bytes.return_value = b'scenario-template'
    cfgTemplate.read_bytes.return_value = b'config-template'
    environ = context.environ
    environ.update({
        'KRKN_HUB__TEMPL__SCN__FN': 'selected.yaml.template',
        'KRKN_HUB__TEMPLATE__CFG__FN': 'config.yaml.template',
        'SCENARIO_FILE': str(scnFile),
        'SCENARIO_TYPE': 'custom_type',
        'ALERTS_PATH': 'config/alerts.yaml',
        'METRICS_PATH': 'config/metrics.yaml',
        'RESILIENCY_FILE': 'config/alerts.yaml',
        'ROLLBACK_VERSIONS_DIRECTORY': 'rollback',
        'TELEMETRY_ARCHIVE_PATH': 'archive',
    })
    config = mock.MagicMock()
    runConfig = SimpleNamespace(
        kCfg='fake-config',
        fxtInf=SimpleNamespace(
            rootPath=SimpleNamespace(krkn=krknRoot, krknHub=kHubRoot),
            krknCfg=SimpleNamespace(krknEnv={}),
        ),
    )
    config.__or__.return_value = runConfig
    hub = MUT.KrknHub(None, MUT.InstParsKrknHub(fxtCfgObj=config))
    return SimpleNamespace(
        boundary=context, paths=paths, environ=environ,
        kHubRoot=kHubRoot, krknRoot=krknRoot, scnDir=scnDir,
        runRoot=runRoot, runDir=runDir, scnFile=scnFile,
        cfgFile=cfgFile, script=script, descriptor=descriptor,
        scnTemplate=scnTemplate, cfgTemplate=cfgTemplate,
        config=config, runConfig=runConfig, hub=hub,
    )


def ConfigureFixture(
    testBoundaries: SimpleNamespace,
    request: SimpleNamespace,
    requestParam: dict | None = None,
    krknEnv: dict | None = None,
    fxtInf: object | None = None,
    testPars: object | None = None,
    ns: str = 'synthetic-ns',
    kCfg: str = '/synthetic/config',
    cliCfg: object | None = None,
) -> tuple[mock.MagicMock, mock.MagicMock, SimpleNamespace]:
    defaults = testBoundaries.defaults
    defaults.fxtInf.krknCfg.krknEnv = krknEnv or {}
    middle = mock.MagicMock()
    if (fxtInf is None):
        fxtInf = SimpleNamespace(
            rootPath=defaults.fxtInf.rootPath,
            krknCfg=SimpleNamespace(krknEnv=krknEnv or {}, targets=[]),
        )
    if (testPars is None): testPars = SimpleNamespace(results=[])
    if (cliCfg is None): cliCfg = SimpleNamespace(ocCfg={}, vcCfg={})
    final = SimpleNamespace(
        fxtInf=fxtInf, testPars=testPars, ns=ns, kCfg=kCfg,
        cliCfg=cliCfg,
    )
    defaults.__or__.return_value = middle
    middle.__or__.return_value = final
    if (requestParam is None):
        if hasattr(request, 'param'): del request.param
    else: request.param = requestParam
    return defaults, middle, final


@pytest.mark.parametrize('field', [
    'krknHub', 'fxtInf', 'testPars', 'cli', 'ns',
])
def test_FxtKrkn_YieldsFrozenObjectWithMutableContents(
    testBoundaries: SimpleNamespace,
    fixtureRequest: SimpleNamespace,
    field: str,
) -> None:
    krknTargets = []
    results = []
    krkn = testBoundaries.paths.Make('/synthetic/krkn')
    krknHub = testBoundaries.paths.Make('/synthetic/hub')
    fxtInf = SimpleNamespace(
        rootPath=SimpleNamespace(krkn=krkn, krknHub=krknHub),
        krknCfg=SimpleNamespace(krknEnv={}, targets=krknTargets),
    )
    testPars = SimpleNamespace(vm='vm-one', results=results)
    cliCfg = SimpleNamespace(
        ocCfg={
            'context': 'oc-context', 'kubeconfig': '/synthetic/oc-config',
        },
        vcCfg={
            'kubeconfig': '/synthetic/virt-config',
            'server': 'https://synthetic.invalid',
        },
    )
    requestParam = {
        'kCfg': '/synthetic/config', 'ns': 'custom-ns',
        'cliCfg': cliCfg, 'fxtInf': fxtInf, 'testPars': testPars,
    }
    config = ConfigureFixture(
        testBoundaries, fixtureRequest, requestParam,
        fxtInf=fxtInf, testPars=testPars, ns='custom-ns',
        kCfg='/synthetic/config', cliCfg=cliCfg,
    )[2]
    ocFactory = mock.Mock()
    virtFactory = mock.Mock()
    generator = MUT.FxtKrkn.__wrapped__(fixtureRequest, ocFactory, virtFactory)
    try:
        context = next(generator)
        assert isinstance(context, MUT.FxtObjKrkn)
        assert (
            {item.name for item in dataclasses.fields(context)} ==
            {'krknHub', 'fxtInf', 'testPars', 'cli', 'ns'}
        )
        assert isinstance(context.krknHub, MUT.KrknHub)
        assert (context.krknHub.request is fixtureRequest)
        assert (context.krknHub.iPars.fxtCfgObj is config)
        assert (config.fxtInf.rootPath.krknHub is krknHub)
        assert (config.fxtInf.rootPath.krkn is krkn)
        assert (config.kCfg == '/synthetic/config')
        assert (context.fxtInf is fxtInf)
        assert (context.fxtInf.krknCfg.targets is krknTargets)
        assert (context.testPars is testPars)
        assert (context.ns == 'custom-ns')
        assert (context.cli.oc is ocFactory.return_value)
        assert (context.cli.virtCtl is virtFactory.return_value)
        ocFactory.assert_called_once_with(
            ns='custom-ns', context='oc-context',
            kubeconfig='/synthetic/config',
        )
        virtFactory.assert_called_once_with(
            ns='custom-ns', kubeconfig='/synthetic/config',
            server='https://synthetic.invalid',
        )
        original = getattr(context, field)
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(context, field, object())
        assert (getattr(context, field) is original)
        krknTargets.append('node-one')
        results.append('recovered')
        context.cli.oc.Run.return_value = 'running'
        assert (context.fxtInf.krknCfg.targets == ['node-one'])
        assert (context.testPars.results == ['recovered'])
        assert (context.cli.oc.Run() == 'running')
        with pytest.raises(StopIteration):
            next(generator)
    finally:
        generator.close()


@pytest.mark.parametrize('hasRequestParam', [False, True])
def test_FxtKrkn_UsesDefaultConfigurationWithAbsentOrEmptyRequestParam(
    testBoundaries: SimpleNamespace,
    fixtureRequest: SimpleNamespace,
    hasRequestParam: bool,
) -> None:
    if hasRequestParam: fixtureRequest.param = {}
    defaults = testBoundaries.defaults
    ocFactory = mock.Mock()
    virtFactory = mock.Mock()
    generator = MUT.FxtKrkn.__wrapped__(fixtureRequest, ocFactory, virtFactory)
    try:
        context = next(generator)
        config = defaults.__or__.return_value.__or__.return_value
        assert (context.krknHub.request is fixtureRequest)
        assert (context.krknHub.iPars.fxtCfgObj is config)
        assert (config.fxtInf.rootPath is defaults.fxtInf.rootPath)
        assert (config.kCfg == '/synthetic/config')
        assert (context.fxtInf is defaults.fxtInf)
        assert (context.testPars is defaults.testPars)
        assert (context.ns == 'synthetic-ns')
        assert (context.cli.oc is ocFactory.return_value)
        assert (context.cli.virtCtl is virtFactory.return_value)
        defaults.__or__.assert_called_once()
        defaults.__or__.return_value.__or__.assert_called_once()
        assert (mock.call() in testBoundaries.namedDict.call_args_list)
        ocFactory.assert_called_once_with(
            ns='synthetic-ns', kubeconfig='/synthetic/config',
        )
        virtFactory.assert_called_once_with(
            ns='synthetic-ns', kubeconfig='/synthetic/config',
        )
        with pytest.raises(StopIteration):
            next(generator)
    finally:
        generator.close()


@pytest.mark.parametrize('failingCLI', ['oc', 'virtCtl'])
def test_FxtKrkn_PropagatesCLIFactoryErrors(
    testBoundaries: SimpleNamespace,
    fixtureRequest: SimpleNamespace,
    failingCLI: str,
) -> None:
    fixtureRequest.param = {'kCfg': 'fake-config'}
    config = testBoundaries.defaults.__or__.return_value.__or__.return_value
    config.kCfg = 'fake-config'
    config.cliCfg = SimpleNamespace(ocCfg={}, vcCfg={})
    error = RuntimeError('synthetic factory failure')
    ocFactory = mock.Mock()
    virtFactory = mock.Mock()
    (ocFactory if (failingCLI == 'oc') else virtFactory).side_effect = error
    generator = MUT.FxtKrkn.__wrapped__(fixtureRequest, ocFactory, virtFactory)
    try:
        with pytest.raises(RuntimeError) as raised:
            next(generator)
        assert (raised.value is error)
        ocFactory.assert_called_once_with(
            ns='synthetic-ns', kubeconfig='fake-config',
        )
        if (failingCLI == 'oc'): virtFactory.assert_not_called()
        else:
            virtFactory.assert_called_once_with(
                ns='synthetic-ns', kubeconfig='fake-config',
            )
    finally:
        generator.close()


@pytest.mark.parametrize('customOptions', [False, True])
@pytest.mark.parametrize('indirectOverride', [False, True])
def test_FxtKrkn_PassesOptionsAndEnvironmentInputsToHelpers(
    testBoundaries: SimpleNamespace,
    fixtureRequest: SimpleNamespace,
    customOptions: bool,
    indirectOverride: bool,
) -> None:
    context = testBoundaries
    options = fixtureRequest.config.rootpath / 'conf/Krkn/opts--krkn.yaml'
    if customOptions:
        options = context.paths.Make('/repo/tests/custom options.yaml')
        context.environ['IENG_PYTEST____FXT__KRKN____OPTS'] = str(options)
    context.paths.Make(options).read_text.return_value = 'yaml-canned'
    script = (
        'export ENABLE_ES=True\n'
        'export ES_SERVER=https://search.invalid\n'
        'export ES_PORT=443\n'
        'export KRKN_TEST_OPTS_SHARED=config\n'
        'export KRKN_TEST_OPTS_ONLY="from content"\n'
        'unset KRKN_TEST_OPTS_REMOVE\n'
    )
    parsed = {
        'sourceFile': {'envVar': script},
        'blocked': [{'not-valid': 'retained'}],
        'paired': ({'not-valid': 'retained'},),
    }
    context.yamlToDict.return_value = parsed
    context.namedDict.FromDict.return_value = SimpleNamespace(
        sourceFile=SimpleNamespace(envVar=script),
    )
    inherited = {
        'ENABLE_ES': 'True', 'ES_SERVER': 'https://search.invalid',
        'ES_PORT': '443',
    }
    defaultEnv = {
        'ENABLE_ES': 'False', 'ES_SERVER': 'http://0.0.0.0',
        'ES_PORT': '443',
    }
    context.environ.update({
        **inherited, 'KRKN_TEST_OPTS_REMOVE': 'parent',
        'KRKN_TEST_OPTS_SHARED': 'parent',
        'KRKN_TEST_UNRELATED': 'not copied',
    })
    shellDelta = {
        **inherited, 'KRKN_TEST_OPTS_SHARED': 'config',
        'KRKN_TEST_OPTS_ONLY': 'from content',
        'KRKN_TEST_OPTS_REMOVE': None,
    }
    context.getEnvDelta.return_value = shellDelta
    overrides = {}
    if indirectOverride:
        overrides = {
            'ENABLE_ES': 'False', 'KRKN_TEST_OPTS_SHARED': 'indirect',
            'KRKN_TEST_OPTS_ONLY': None,
        }
    requestParam = {}
    if indirectOverride:
        requestParam = {'fxtInf': SimpleNamespace(
            krknCfg=SimpleNamespace(krknEnv=overrides),
        )}
    expectedEnvInput = {**inherited, **shellDelta}
    expectedFinalEnv = {**expectedEnvInput, **overrides}
    fxtInf = SimpleNamespace(
        rootPath=context.defaults.fxtInf.rootPath,
        krknCfg=SimpleNamespace(krknEnv=dict(expectedFinalEnv)),
    )
    config = ConfigureFixture(
        context, fixtureRequest, requestParam,
        krknEnv=defaultEnv, fxtInf=fxtInf,
    )[2]
    beforeEnv = dict(context.environ)
    beforeDefaults = dict(context.defaults.fxtInf.krknCfg.krknEnv)
    generator = MUT.FxtKrkn.__wrapped__(
        fixtureRequest, mock.Mock(), mock.Mock(),
    )
    try:
        fixtureContext = next(generator)
        context.paths.Make(options).read_text.assert_called_once_with()
        context.yamlToDict.assert_called_once_with('yaml-canned')
        context.namedDict.FromDict.assert_called_once_with(parsed)
        assert (context.namedDict.FromDict.call_args.args[0] is parsed)
        context.stringIO.assert_called_once_with(script)
        context.getEnvDelta.assert_called_once_with(
            MUT.BashEnv, [context.stream],
        )
        actualEnvInput = context.namedDict.call_args_list[0].kwargs['krknEnv']
        assert (actualEnvInput == expectedEnvInput)
        context.defaults.__or__.assert_called_once()
        context.defaults.__or__.return_value.__or__.assert_called_once()
        assert (
            context.namedDict.call_args_list[-2] == mock.call(**requestParam)
        )
        assert (fixtureContext.krknHub.request is fixtureRequest)
        assert (fixtureContext.krknHub.iPars.fxtCfgObj is config)
        assert (fixtureContext.fxtInf is fxtInf)
        assert (fixtureContext.fxtInf.krknCfg.krknEnv == expectedFinalEnv)
        assert (context.defaults.fxtInf.krknCfg.krknEnv == beforeDefaults)
        assert (context.environ == beforeEnv)
        with pytest.raises(StopIteration):
            next(generator)
    finally:
        generator.close()


@pytest.mark.parametrize(('script', 'delta'), [
    (':', {}),
    ('export ES_PORT=8443', {'ES_PORT': '8443'}),
    ('unset ES_PORT', {'ES_PORT': None}),
    ('export ES_PORT=', {'ES_PORT': ''}),
])
def test_FxtKrkn_PassesInheritedAndShellValuesToConfigHelper(
    testBoundaries: SimpleNamespace,
    fixtureRequest: SimpleNamespace,
    script: str,
    delta: dict,
) -> None:
    context = testBoundaries
    inherited = {
        'ENABLE_ES': 'True', 'ES_SERVER': 'https://search.invalid',
        'ES_PORT': '443',
    }
    defaultEnv = {
        'ENABLE_ES': 'False', 'ES_SERVER': 'http://0.0.0.0',
        'ES_PORT': 'ES_PORT:=443',
    }
    context.environ.update(inherited)
    context.environ['KRKN_TEST_UNRELATED'] = 'not copied'
    options = fixtureRequest.config.rootpath / 'conf/Krkn/opts--krkn.yaml'
    context.paths.Make(options).read_text.return_value = 'yaml-canned'
    parsed = {'sourceFile': {'envVar': script}}
    context.yamlToDict.return_value = parsed
    context.namedDict.FromDict.return_value = SimpleNamespace(
        sourceFile=SimpleNamespace(envVar=script),
    )
    context.getEnvDelta.return_value = delta
    expectedEnvInput = {**inherited, **delta}
    fxtInf = SimpleNamespace(
        rootPath=context.defaults.fxtInf.rootPath,
        krknCfg=SimpleNamespace(krknEnv=dict(expectedEnvInput)),
    )
    config = ConfigureFixture(
        context, fixtureRequest, krknEnv=defaultEnv, fxtInf=fxtInf,
    )[2]
    beforeEnv = dict(context.environ)
    beforeDefaults = dict(context.defaults.fxtInf.krknCfg.krknEnv)
    generator = MUT.FxtKrkn.__wrapped__(
        fixtureRequest, mock.Mock(), mock.Mock(),
    )
    try:
        fixtureContext = next(generator)
        context.yamlToDict.assert_called_once_with('yaml-canned')
        context.namedDict.FromDict.assert_called_once_with(parsed)
        assert (context.namedDict.FromDict.call_args.args[0] is parsed)
        context.stringIO.assert_called_once_with(script)
        context.getEnvDelta.assert_called_once_with(
            MUT.BashEnv, [context.stream],
        )
        actualEnvInput = context.namedDict.call_args_list[0].kwargs['krknEnv']
        assert (actualEnvInput == expectedEnvInput)
        assert (fixtureContext.krknHub.iPars.fxtCfgObj is config)
        assert (fixtureContext.fxtInf.krknCfg.krknEnv == expectedEnvInput)
        assert (context.environ == beforeEnv)
        assert (context.defaults.fxtInf.krknCfg.krknEnv == beforeDefaults)
    finally:
        generator.close()


def test_Init_PreservesRequestAndInstanceParameters() -> None:
    request = SimpleNamespace()
    config = mock.Mock()
    parameters = MUT.InstParsKrknHub(fxtCfgObj=config)
    hub = MUT.KrknHub(request, parameters)
    assert (hub.request is request)
    assert (hub.iPars is parameters)
    assert (hub.iPars.fxtCfgObj is config)


@pytest.mark.parametrize(('scnName', 'descriptor'), [
    ('dummy-scenario', 'LABEL krknctl.is_a_scenario=true\n'),
    ('pod-scenarios', None),
    ('pod-scenarios', 'FROM scratch\n'),
    ('pod-scenarios', 'LABEL krknctl.is_a_scenario=false\n'),
])
def test_Run_RejectsInvalidScenariosBeforeSourcingOrCreatingArtifacts(
    hubContext: SimpleNamespace,
    scnName: str,
    descriptor: str | None,
) -> None:
    context = hubContext
    dockerfile = context.kHubRoot / scnName / 'Dockerfile.template'
    dockerfile.is_file.return_value = (descriptor is not None)
    dockerfile.read_text.return_value = descriptor or ''
    context.boundary.regexSearch.side_effect = [None, None]
    with pytest.raises(AssertionError):
        context.hub.Run(scnName)
    context.boundary.source.assert_not_called()
    context.boundary.tempEnv.assert_not_called()
    assert (context.runDir.mkdir.call_count == 0)


@pytest.mark.parametrize('descriptor', [
    'LABEL krknctl.is_a_scenario=true\n',
    'LABEL krknctl.is_a_scenario=\'true\'\n',
    'FROM scratch\n  label KRKNCTL.IS_A_SCENARIO = "TRUE"  \n',
])
def test_Run_PassesThreeScenarioLabelsToMockedSearch(
    hubContext: SimpleNamespace,
    descriptor: str,
) -> None:
    context = hubContext
    context.descriptor.read_text.return_value = descriptor
    context.boundary.regexSearch.side_effect = [
        None, mock.sentinel.scenarioLabelMatch,
    ]
    result = context.hub.Run('pod-scenarios')
    assert (result is context.boundary.runner.return_value)
    assert (
        context.boundary.regexSearch.call_args_list == [
            mock.call(lexicalScenarioPattern, 'pod-scenarios'),
            mock.call(
                scenarioLabelPattern,
                context.descriptor.read_text.return_value,
            ),
        ]
    )
    context.boundary.source.assert_called_once_with(envFiles=[
        (context.kHubRoot / 'env.sh'), (context.scnDir / 'env.sh'),
    ])
    context.boundary.runner.assert_called_once()


@pytest.mark.parametrize('name', [
    '../outside', '/outside', 'a/../b', 'a/..', '..',
])
def test_Run_RejectsLexicalScenarioPathsUsingMockedSearch(
    hubContext: SimpleNamespace,
    name: str,
) -> None:
    context = hubContext
    with pytest.raises(AssertionError):
        context.boundary.regexSearch.side_effect = [mock.sentinel.lexicalMatch]
        context.hub.Run(name)
    context.boundary.source.assert_not_called()
    context.boundary.tempEnv.assert_not_called()
    context.boundary.commandFactory.assert_not_called()
    for path in context.paths.values.values():
        path.read_text.assert_not_called()
    context.boundary.regexSearch.assert_called_once_with(
        r'(^/|(^|/)\.\.(/|$))', str(name),
    )
    assert (context.runDir.mkdir.call_count == 0)


@pytest.mark.parametrize('name', [
    PurePosixPath('group/pod-scenarios'),
    PurePosixPath('group/scenario'),
    PurePosixPath('foo..bar'),
    PurePosixPath('group/..hidden'),
])
def test_Run_AcceptsNestedRelativePathWithMockedSearch(
    hubContext: SimpleNamespace,
    name: PurePosixPath,
) -> None:
    context = hubContext
    scenarioDir = context.kHubRoot / name
    descriptor = context.paths.Make((scenarioDir / 'Dockerfile.template'))
    descriptor.is_file.return_value = True
    descriptor.read_text.return_value = 'LABEL krknctl.is_a_scenario=true'
    context.hub.Run(name)
    expectedRunDir = context.runRoot / f'20260203T040506Z--{name}'
    context.boundary.clock.now.assert_called_once_with(timezone.utc)
    context.boundary.formatNow.assert_called_once_with('%Y%m%dT%H%M%SZ')
    configFile = expectedRunDir / 'config/chaos.yaml'
    context.boundary.chdir.assert_called_once_with(expectedRunDir)
    context.boundary.runner.assert_called_once_with(
        (context.krknRoot / 'run_kraken.py'),
        [f'--config={configFile}'],
        {}, None, '__main__',
    )


@pytest.mark.parametrize(('configured', 'override', 'scriptValue'), [
    ('configured-config', None, None),
    ('configured-config', 'call-config', None),
    (None, None, 'script-config'),
    ('configured-config', None, 'script-config'),
    ('configured-config', 'call-config', 'script-config'),
])
def test_Run_InitialKubeconfigSeedAndLaterOverrides_ReviewRegression(
    hubContext: SimpleNamespace,
    configured: str | None,
    override: str | None,
    scriptValue: str | None,
) -> None:
    context = hubContext
    context.environ['KRKN_KUBE_CONFIG'] = scriptValue or 'ambient-config'
    configEnv = (
        {} if (configured is None) else {'KRKN_KUBE_CONFIG': configured}
    )
    overrides = {} if (override is None) else {'KRKN_KUBE_CONFIG': override}
    context.runConfig.fxtInf.krknCfg.krknEnv = configEnv
    fxtInf = SimpleNamespace(krknCfg=SimpleNamespace(krknEnv=configEnv))
    seed = {**configEnv, 'KRKN_KUBE_CONFIG': 'fake-config'}
    files = [(context.kHubRoot / 'env.sh'), (context.scnDir / 'env.sh')]
    events = mock.Mock()
    events.attach_mock(context.boundary.updateEnv, 'update_env')
    events.attach_mock(context.boundary.source, 'source')
    context.hub.Run('pod-scenarios', overrides, fxtInf=fxtInf)
    context.boundary.namedDict.assert_called_once_with({'fxtInf': fxtInf})
    context.boundary.updateEnv.assert_any_call(seed)
    context.boundary.source.assert_called_once_with(envFiles=files)
    assert (
        events.method_calls.index(mock.call.update_env(seed)) <
        events.method_calls.index(mock.call.source(envFiles=files))
    )
    context.boundary.runner.assert_called_once_with(
        (context.krknRoot / 'run_kraken.py'),
        [f'--config={context.cfgFile}'], overrides, None, '__main__',
    )


@pytest.mark.parametrize(('label', 'accepted'), [
    (None, False), ('', False),
    ('LABEL krknctl.is_a_scenario=false', False),
    ('LABEL other=true', False),
    ('LABEL krknctl.is_a_scenario=true', True),
    ('  label krknctl.is_a_scenario = "true"  ', True),
    ('LABEL krknctl.is_a_scenario=\'true\'', True),
])
def test_Run_UsesCannedDescriptorSearchResults(
    hubContext: SimpleNamespace,
    label: str | None,
    accepted: bool,
) -> None:
    context = hubContext
    context.descriptor.is_file.return_value = (label is not None)
    context.descriptor.read_text.return_value = label or ''
    context.boundary.regexSearch.side_effect = [
        None, mock.sentinel.scenarioLabelMatch if accepted else None,
    ]
    expectedSearchCalls = [mock.call(lexicalScenarioPattern, 'pod-scenarios')]
    if (label is not None):
        expectedSearchCalls.append(
            mock.call(scenarioLabelPattern, label or ''),
        )
    if accepted:
        context.hub.Run('pod-scenarios')
        context.boundary.runner.assert_called_once()
    else:
        with pytest.raises(AssertionError):
            context.hub.Run('pod-scenarios')
        context.boundary.source.assert_not_called()
        assert (context.runDir.mkdir.call_count == 0)


    assert (context.boundary.regexSearch.call_args_list == expectedSearchCalls)


def test_Run_RejectsDummyBeforeSource_ReviewRegression(
    hubContext: SimpleNamespace,
) -> None:
    context = hubContext
    context.paths.Make(
        (context.kHubRoot / 'dummy-scenario' / 'Dockerfile.template')
    ).is_file.return_value = True
    with pytest.raises(AssertionError):
        context.hub.Run('dummy-scenario')
    context.boundary.source.assert_not_called()
    assert (context.runDir.mkdir.call_count == 0)


@pytest.mark.parametrize('debug', [None, 'False', 'True', 'true', '1'])
@pytest.mark.parametrize('overrides', [None, {}, {
    'KRKN_KUBE_CONFIG': 'override-config', 'KRKN_TEST_ORDER': 7,
    'KRKN_TEST_REMOVE': None, 'ALERTS_PATH': 'execution-only',
}])
def test_Run_PassesTemplateBytesAndPreservesDispatchContract(
    hubContext: SimpleNamespace,
    debug: str | None,
    overrides: dict | None,
) -> None:
    context = hubContext
    if (debug is not None): context.environ['KRKN_DEBUG'] = debug
    events = mock.Mock()
    events.attach_mock(context.scnFile.write_bytes, 'scenario_write')
    events.attach_mock(context.cfgFile.write_bytes, 'config_write')
    events.attach_mock(context.boundary.runner, 'runner')
    initialGlobals = {'injected': object()}
    result = context.hub.Run(
        'pod-scenarios', overrides, initialGlobals, 'synthetic_run',
    )
    args = [f'--config={context.cfgFile}']
    callOrder = [call[0] for call in events.method_calls]
    assert (callOrder.index('scenario_write') < callOrder.index('runner'))
    assert (callOrder.index('config_write') < callOrder.index('runner'))
    if (debug == 'True'): args.extend(['--debug', 'True'])
    assert (result is context.boundary.runner.return_value)
    assert (
        context.boundary.regexSearch.call_args_list == [
            mock.call(lexicalScenarioPattern, 'pod-scenarios'),
            mock.call(
                scenarioLabelPattern,
                context.descriptor.read_text.return_value,
            ),
        ]
    )
    context.boundary.source.assert_called_once_with(envFiles=[
        (context.kHubRoot / 'env.sh'), (context.scnDir / 'env.sh'),
    ])
    context.boundary.runner.assert_called_once_with(
        (context.krknRoot / 'run_kraken.py'), args, overrides or {},
        initialGlobals, 'synthetic_run',
    )
    context.scnTemplate.read_bytes.assert_called_once_with()
    context.cfgTemplate.read_bytes.assert_called_once_with()
    assert (
        context.scnFile.write_bytes.call_args == mock.call(b'canned-render')
    )
    assert (
        context.cfgFile.write_bytes.call_args == mock.call(b'canned-render')
    )
    assert (
        context.boundary.commandRun.call_args_list == [
            mock.call(
                ['envsubst'], input=b'scenario-template',
                spPars={'text': False},
            ),
            mock.call(
                ['envsubst'], input=b'config-template',
                spPars={'text': False},
            ),
        ]
    )


@pytest.mark.parametrize('absolute', [False, True])
def test_Run_UsesRelativeAndAbsoluteTemplateSelectors(
    hubContext: SimpleNamespace,
    absolute: bool,
) -> None:
    context = hubContext
    if absolute:
        scnSelector = '/external/scenario.yaml.template'
        cfgSelector = '/external/config.yaml.template'
        context.environ.update({
            'KRKN_HUB__TEMPL__SCN__FN': scnSelector,
            'KRKN_HUB__TEMPLATE__CFG__FN': cfgSelector,
        })
    else:
        scnSelector = 'selected.yaml.template'
        cfgSelector = 'config.yaml.template'
    scnTemplate = context.scnDir / scnSelector
    cfgTemplate = context.kHubRoot / cfgSelector
    scnTemplate.read_bytes.return_value = b'canned-scenario-template'
    cfgTemplate.read_bytes.return_value = b'canned-config-template'
    context.hub.Run(PurePosixPath('pod-scenarios'))
    assert (scnTemplate.read_bytes.call_count == 1)
    assert (cfgTemplate.read_bytes.call_count == 1)
    assert (
        context.boundary.commandRun.call_args_list == [
            mock.call(
                ['envsubst'], input=b'canned-scenario-template',
                spPars={'text': False},
            ),
            mock.call(
                ['envsubst'], input=b'canned-config-template',
                spPars={'text': False},
            ),
        ]
    )


@pytest.mark.parametrize('failure', [
    'root', 'scenario', 'selector', 'template', 'envsubst',
    'missingRunner', 'runner', 'exit',
])
def test_Run_PropagatesErrorsAndExitsMockedContexts(
    hubContext: SimpleNamespace,
    failure: str,
) -> None:
    context = hubContext
    error = RuntimeError(f'synthetic {failure} failure')
    expectedError = RuntimeError
    if (failure in ('root', 'scenario')):
        context.boundary.source.side_effect = error
    elif (failure == 'selector'):
        del context.environ['KRKN_HUB__TEMPL__SCN__FN']
        expectedError = KeyError
    elif (failure == 'template'):
        context.scnTemplate.read_bytes.side_effect = FileNotFoundError(
            'synthetic template missing'
        )
        expectedError = FileNotFoundError
    elif (failure == 'envsubst'):
        failed = SimpleNamespace(
            success=False, stdout=b'', stderr=b'synthetic failure',
            exitStat=7,
        )
        context.boundary.commandRun.return_value = failed
        error = RuntimeError('The `envsubst` failed (7): synthetic failure')
    elif (failure == 'missingRunner'):
        context.boundary.runner.side_effect = FileNotFoundError(
            'synthetic runner missing'
        )
        expectedError = FileNotFoundError
    elif (failure == 'exit'):
        error = SystemExit(7)
        expectedError = SystemExit
        context.boundary.runner.side_effect = error
    else: context.boundary.runner.side_effect = error
    beforeEnv = dict(context.environ)
    with pytest.raises(expectedError) as raised:
        context.hub.Run('pod-scenarios')
    if (failure in ('root', 'scenario')):
        context.boundary.source.assert_called_once_with(envFiles=[
            (context.kHubRoot / 'env.sh'), (context.scnDir / 'env.sh'),
        ])
    if (failure in ('root', 'scenario', 'runner', 'exit')):
        assert (raised.value is error)

    assert (context.environ == beforeEnv)
    assert context.boundary.tempEnv.return_value.__exit__.called
    if (failure in ('missingRunner', 'runner', 'exit')):
        assert context.boundary.chdir.return_value.__exit__.called


@pytest.mark.parametrize('overlap', ['scenario', 'config', 'cross', 'symlink'])
def test_Run_UsesCannedTemplatesForFourOverlapCases(
    hubContext: SimpleNamespace,
    overlap: str,
) -> None:
    context = hubContext
    scnTemplate = context.scnTemplate
    cfgTemplate = context.cfgTemplate
    scnOutput = context.scnFile
    cfgOutput = context.cfgFile
    if (overlap == 'scenario'):
        context.environ['KRKN_HUB__TEMPL__SCN__FN'] = str(scnOutput)
        scnTemplate = scnOutput
    elif (overlap == 'config'):
        context.environ['KRKN_HUB__TEMPLATE__CFG__FN'] = str(cfgOutput)
        cfgTemplate = cfgOutput
    elif (overlap == 'cross'):
        context.environ.update({
            'KRKN_HUB__TEMPL__SCN__FN': str(cfgOutput),
            'KRKN_HUB__TEMPLATE__CFG__FN': str(scnOutput),
        })
        (scnTemplate, cfgTemplate) = cfgOutput, scnOutput
    else:
        alias = context.scnDir / 'alias.template'
        context.paths.ResolveAs(alias, scnOutput)
        context.environ['KRKN_HUB__TEMPL__SCN__FN'] = 'alias.template'
        scnTemplate = context.scnFile
    scnTemplate.read_bytes.return_value = b'scenario-template-A'
    cfgTemplate.read_bytes.return_value = b'config-template-B'
    context.boundary.commandRun.side_effect = [
        SimpleNamespace(
            success=True, stdout=b'scenario-render-A', stderr=b'',
            exitStat=0,
        ),
        SimpleNamespace(
            success=True, stdout=b'config-render-B', stderr=b'',
            exitStat=0,
        ),
    ]
    events = mock.Mock()
    events.attach_mock(scnTemplate.read_bytes, 'read_scenario')
    events.attach_mock(cfgTemplate.read_bytes, 'read_config')
    events.attach_mock(scnOutput.write_bytes, 'write_scenario')
    events.attach_mock(cfgOutput.write_bytes, 'write_config')
    context.hub.Run('pod-scenarios')
    names = [call[0] for call in events.method_calls]
    assert (names.index('read_scenario') < names.index('write_scenario'))
    assert (names.index('read_config') < names.index('write_scenario'))
    assert (names.index('read_scenario') < names.index('write_config'))
    assert (names.index('read_config') < names.index('write_config'))
    assert (
        context.boundary.commandRun.call_args_list == [
            mock.call(
                ['envsubst'], input=b'scenario-template-A',
                spPars={'text': False},
            ),
            mock.call(
                ['envsubst'], input=b'config-template-B',
                spPars={'text': False},
            ),
        ]
    )
    scnTemplate.read_bytes.assert_called_once_with()
    cfgTemplate.read_bytes.assert_called_once_with()
    scnOutput.write_bytes.assert_called_once_with(b'scenario-render-A')
    cfgOutput.write_bytes.assert_called_once_with(b'config-render-B')


def test_Run_RequiresBothTemplatesBeforeWritingOutputs(
    hubContext: SimpleNamespace,
) -> None:
    context = hubContext
    context.cfgTemplate.read_bytes.side_effect = FileNotFoundError(
        'synthetic config template missing'
    )
    with pytest.raises(FileNotFoundError):
        context.hub.Run('pod-scenarios')
    context.scnTemplate.read_bytes.assert_called_once_with()
    context.cfgTemplate.read_bytes.assert_called_once_with()
    context.scnFile.write_bytes.assert_not_called()
    context.cfgFile.write_bytes.assert_not_called()


@pytest.mark.parametrize('error', [RuntimeError('synthetic'), SystemExit(7)])
def test_Run_PreservesInjectedExceptionIdentity(
    hubContext: SimpleNamespace,
    error: BaseException,
) -> None:
    context = hubContext
    context.boundary.runner.side_effect = error
    initialGlobals = {'injectedError': error}
    with pytest.raises(type(error)) as raised:
        context.hub.Run('pod-scenarios', initGlb=initialGlobals)
    assert (raised.value is error)
    assert (context.boundary.runner.call_args.args[3] is initialGlobals)
    assert context.boundary.chdir.return_value.__exit__.called
    assert context.boundary.tempEnv.return_value.__exit__.called


def test_Run_ResolvesRelativeRunnerBeforeChangingDirectory(
    hubContext: SimpleNamespace,
) -> None:
    context = hubContext
    relativeRunner = context.krknRoot / 'run_kraken.py'
    events = mock.Mock()
    events.attach_mock(relativeRunner.resolve, 'resolve_runner')
    events.attach_mock(context.boundary.chdir, 'change_directory')
    context.hub.Run('pod-scenarios', krknScr='run_kraken.py')
    names = [call[0] for call in events.method_calls]
    assert (names.index('resolve_runner') < names.index('change_directory'))
    assert (context.boundary.runner.call_args.args[0] is relativeRunner)


def test_Run_UsesUTCAndRejectsSameSecondCollisions(
    hubContext: SimpleNamespace,
) -> None:
    context = hubContext
    collision = FileExistsError('synthetic same-second collision')
    context.runDir.mkdir.side_effect = [None, collision]
    context.hub.Run('pod-scenarios')
    context.boundary.clock.now.assert_called_once_with(timezone.utc)
    context.boundary.formatNow.assert_called_once_with('%Y%m%dT%H%M%SZ')
    with pytest.raises(FileExistsError) as raised:
        context.hub.Run('pod-scenarios')
    assert (raised.value is collision)
    assert (
        context.boundary.clock.now.call_args_list == [
            mock.call(timezone.utc), mock.call(timezone.utc),
        ]
    )
    assert (
        context.boundary.formatNow.call_args_list == [
            mock.call('%Y%m%dT%H%M%SZ'),
            mock.call('%Y%m%dT%H%M%SZ'),
        ]
    )
    context.runDir.mkdir.assert_has_calls([
        mock.call(parents=True, exist_ok=False),
        mock.call(parents=True, exist_ok=False),
    ])
    assert (context.runDir.mkdir.call_count == 2)
    assert (context.scnFile.write_bytes.call_count == 1)
    assert (context.boundary.runner.call_count == 1)


def test_Run_RetainsNestedSuffixAndResolvesRelativeRunRoot(
    hubContext: SimpleNamespace,
) -> None:
    context = hubContext
    context.environ[
        'IENG_PYTEST____FXT__KRKN____KRKN__RUN_DIR__ROOT'
    ] = 'relative/runs'
    context.boundary.getenv.return_value = 'relative/runs'
    context.paths.ResolveAs('relative/runs', '/caller/relative/runs')
    scenarioDir = context.kHubRoot / 'group/pod-scenarios'
    descriptor = context.paths.Make((scenarioDir / 'Dockerfile.template'))
    descriptor.is_file.return_value = True
    descriptor.read_text.return_value = 'LABEL krknctl.is_a_scenario=true'
    context.hub.Run('group/pod-scenarios')
    expectedRunDir = PurePosixPath(
        '/caller/relative/runs/20260203T040506Z--group/pod-scenarios'
    )
    context.paths.Make('relative/runs').resolve.assert_called_once_with()
    context.boundary.chdir.assert_called_once_with(
        context.paths.Make(expectedRunDir)
    )
    context.paths.Make(expectedRunDir).mkdir.assert_called_once_with(
        parents=True, exist_ok=False,
    )


@pytest.mark.parametrize('absolute', [False, True])
def test_Run_AnchorsPathsAndCreatesOnlyOutputDirectories(
    hubContext: SimpleNamespace,
    absolute: bool,
) -> None:
    context = hubContext
    inputKeys = ('ALERTS_PATH', 'METRICS_PATH', 'RESILIENCY_FILE')
    outputKeys = (
        'SCENARIO_FILE', 'ROLLBACK_VERSIONS_DIRECTORY',
        'TELEMETRY_ARCHIVE_PATH',
    )
    values = {
        key: (PurePosixPath('nested') / key.lower())
        for key in (*inputKeys, *outputKeys)
    }
    if absolute:
        values = {
            key: (PurePosixPath('/absolute') / value)
            for (key, value) in values.items()
        }
    context.environ.update(
        {key: str(value) for (key, value) in values.items()}
    )
    expected = {}
    for key in inputKeys:
        expected[key] = context.paths.Make((context.krknRoot / values[key]))
    for key in outputKeys:
        expected[key] = context.paths.Make((context.runDir / values[key]))

    rawEnvironment = dict(context.environ)
    resolvedEnvironment = {
        **rawEnvironment,
        **{key: str(value) for (key, value) in expected.items()},
    }

    def CannedPostUpdate(changes: dict) -> None:
        '''Expose a fixed post-call mapping, not an update algorithm.'''
        if (changes == expected): MUT.os.environ = resolvedEnvironment

    context.boundary.updateEnv.side_effect = CannedPostUpdate
    context.hub.Run('pod-scenarios', {'ALERTS_PATH': 'execution-only'})
    pathUpdate = context.boundary.updateEnv.call_args_list[-1].args[0]
    assert (pathUpdate == expected)
    context.paths.Make((context.runDir / 'config')).mkdir.assert_called_once()
    scenarioOutput = expected['SCENARIO_FILE']
    assert scenarioOutput.parent.mkdir.called
    expected['ROLLBACK_VERSIONS_DIRECTORY'].mkdir.assert_called_once()
    expected['TELEMETRY_ARCHIVE_PATH'].mkdir.assert_called_once()
    scenarioOutput.write_bytes.assert_called_once_with(b'canned-render')
    context.boundary.tempEnv.assert_called_once_with()
    context.boundary.tempEnv.return_value.__exit__.assert_called_once()
    for key in inputKeys:
        expected[key].mkdir.assert_not_called()


@pytest.mark.parametrize('alerts', [None, '', 'custom/alerts.yaml'])
@pytest.mark.parametrize('resiliency', [None, ''])
def test_Run_PreservesUnsetAndEmptyOptionalInputs(
    hubContext: SimpleNamespace,
    alerts: str | None,
    resiliency: str | None,
) -> None:
    context = hubContext
    for key in (
        'METRICS_PATH', 'ROLLBACK_VERSIONS_DIRECTORY',
        'TELEMETRY_ARCHIVE_PATH',
    ):
        context.environ.pop(key, None)
    if (alerts is None): context.environ.pop('ALERTS_PATH', None)
    else: context.environ['ALERTS_PATH'] = alerts
    if (resiliency is None): context.environ.pop('RESILIENCY_FILE', None)
    else: context.environ['RESILIENCY_FILE'] = resiliency
    context.hub.Run('pod-scenarios')
    pathUpdate = context.boundary.updateEnv.call_args_list[-1].args[0]
    assert ('METRICS_PATH' not in pathUpdate)
    assert ('ROLLBACK_VERSIONS_DIRECTORY' not in pathUpdate)
    assert ('TELEMETRY_ARCHIVE_PATH' not in pathUpdate)
    assert (('ALERTS_PATH' in pathUpdate) == bool(alerts))
    assert (('RESILIENCY_FILE' in pathUpdate) == bool(resiliency))


@pytest.mark.parametrize('exitStat', [7, -2])
@pytest.mark.parametrize('failureIndex', [0, 1])
def test_Run_ConvertsCmdExecFailuresAndExitsTemporaryEnvironment(
    hubContext: SimpleNamespace,
    exitStat: int,
    failureIndex: int,
) -> None:
    context = hubContext
    success = SimpleNamespace(
        success=True, stdout=b'rendered', stderr=b'', exitStat=0,
    )
    failure = SimpleNamespace(
        success=False, stdout=b'', stderr=b'synthetic failure',
        exitStat=exitStat,
    )
    context.boundary.commandRun.side_effect = [
        *([success] * failureIndex), failure,
    ]
    with pytest.raises(
        RuntimeError,
        match=f'`envsubst` failed \\({exitStat}\\)',
    ):
        context.hub.Run('pod-scenarios')
    assert (context.boundary.commandRun.call_count == (failureIndex + 1))
    actualFirstCommand = context.boundary.commandRun.call_args_list[0]
    expectedFirstCommand = mock.call(
        ['envsubst'], input=b'scenario-template',
        spPars={'text': False},
    )
    assert (actualFirstCommand == expectedFirstCommand)
    assert (context.scnFile.write_bytes.call_count == bool(failureIndex))
    context.cfgFile.write_bytes.assert_not_called()
    context.boundary.tempEnv.return_value.__exit__.assert_called_once()


def test_Run_UsesCannedCmdExecAndWritesReturnedBytes(
    hubContext: SimpleNamespace,
) -> None:
    context = hubContext
    context.boundary.commandRun.return_value = SimpleNamespace(
        success=True, stdout=b'opaque-byte-result', stderr=b'', exitStat=0,
    )
    context.hub.Run('pod-scenarios')
    assert (context.boundary.commandFactory.call_count == 2)
    assert (context.boundary.commandRun.call_count == 2)
    context.scnFile.write_bytes.assert_called_once_with(b'opaque-byte-result')
    context.cfgFile.write_bytes.assert_called_once_with(b'opaque-byte-result')


def test_Run_SeedsEnvironmentBeforeSourcingAndKeepsRunnerOverrides(
    hubContext: SimpleNamespace,
) -> None:
    context = hubContext
    configured = {'KRKN_TEST_SEED': 'config', 'KRKN_TEST_REMOVE': None}
    context.runConfig.fxtInf.krknCfg.krknEnv = configured
    overrides = {'KRKN_TEST_SEED': 'explicit'}
    events = mock.Mock()
    events.attach_mock(context.boundary.updateEnv, 'update_env')
    events.attach_mock(context.boundary.source, 'source')
    beforeEnv = dict(context.environ)
    fxtInf = SimpleNamespace(krknCfg=SimpleNamespace(krknEnv=configured))
    context.hub.Run(
        'pod-scenarios', overrides, runName='synthetic_run',
        fxtInf=fxtInf,
    )
    context.boundary.namedDict.assert_called_once_with({'fxtInf': fxtInf})
    calls = [call[0] for call in events.method_calls]
    assert (calls.index('update_env') < calls.index('source'))
    context.boundary.updateEnv.assert_any_call({
        **configured, 'KRKN_KUBE_CONFIG': 'fake-config',
    })
    context.boundary.runner.assert_called_once_with(
        (context.krknRoot / 'run_kraken.py'),
        [f'--config={context.cfgFile}'], overrides, None,
        'synthetic_run',
    )

    assert (context.environ == beforeEnv)


@pytest.mark.parametrize('preparation', [
    'absent', 'success', 'nonzero', 'notExecutable',
])
def test_Run_PreparesBeforeRunnerAndPropagatesFailures(
    hubContext: SimpleNamespace,
    preparation: str,
) -> None:
    context = hubContext
    prepFile = context.scnDir / 'prep.sh'
    prepFile.is_file.return_value = (preparation != 'absent')
    success = SimpleNamespace(
        success=True, stdout=b'rendered', stderr=b'', exitStat=0,
    )
    prepResult = success
    errorText = ''
    exitStat = 0
    if (preparation == 'nonzero'):
        prepResult = SimpleNamespace(
            success=False, stdout=b'', stderr=b'synthetic-prep-error',
            exitStat=7,
        )
        errorText = "b'synthetic-prep-error'"
        exitStat = 7
    elif (preparation == 'notExecutable'):
        prepResult = SimpleNamespace(
            success=False, stdout=b'', stderr=b'Permission denied',
            exitStat=-2,
        )
        errorText = "b'Permission denied'"
        exitStat = -2
    commandResults = [success, success]
    if (preparation != 'absent'): commandResults.append(prepResult)
    context.boundary.commandRun.side_effect = commandResults
    events = mock.Mock()
    events.attach_mock(context.boundary.commandRun, 'command')
    events.attach_mock(context.boundary.runner, 'runner')
    if (preparation in ('nonzero', 'notExecutable')):
        with pytest.raises(RuntimeError) as raised:
            context.hub.Run('pod-scenarios')
        actualError = str(raised.value)
        expectedError = f'The `prep.sh` failed ({exitStat}): {errorText}'

        assert (actualError == expectedError)
        context.boundary.runner.assert_not_called()
    else:
        context.hub.Run('pod-scenarios')
        context.boundary.runner.assert_called_once()
    actualCommandCount = context.boundary.commandRun.call_count
    expectedCommandCount = 2 if (preparation == 'absent') else 3
    if (preparation != 'absent'):
        context.boundary.commandRun.assert_called_with([str(prepFile)])
    assert (actualCommandCount == expectedCommandCount)
    if (preparation == 'success'):
        calls = [call[0] for call in events.method_calls]
        assert (calls.index('command', 2) < calls.index('runner'))
    assert context.boundary.tempEnv.return_value.__exit__.called


def test_Run_AcceptsScnNameKeyword(hubContext: SimpleNamespace) -> None:
    context = hubContext
    context.hub.Run(scnName='pod-scenarios')
    context.boundary.runner.assert_called_once()
    context.boundary.chdir.assert_called_once_with(context.runDir)


def test_Run_UsesDefaultRunRootAndPropagatesSetupFailure(
    hubContext: SimpleNamespace,
) -> None:
    context = hubContext
    context.environ.pop(
        'IENG_PYTEST____FXT__KRKN____KRKN__RUN_DIR__ROOT', None,
    )
    error = OSError('synthetic mkdir failure')
    context.runDir.mkdir.side_effect = error
    with pytest.raises(OSError) as raised:
        context.hub.Run('pod-scenarios')
    assert (raised.value is error)
    context.boundary.getenv.assert_called_once_with(
        'IENG_PYTEST____FXT__KRKN____KRKN__RUN_DIR__ROOT',
        '/tmp/run-dir--krkn',
    )
    context.runDir.mkdir.assert_called_once_with(parents=True, exist_ok=False)
    context.boundary.tempEnv.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize(('missing', 'exitStat'), [(False, 7), (True, -2)])
def test_Run_ConvertsCannedEnvsubstFailuresToRuntimeError(
    hubContext: SimpleNamespace,
    missing: bool,
    exitStat: int,
) -> None:
    context = hubContext
    errorText = 'synthetic-error' if not missing else 'not found'
    context.boundary.commandRun.return_value = SimpleNamespace(
        success=False, stdout=b'', stderr=errorText, exitStat=exitStat,
    )
    with pytest.raises(
        RuntimeError,
        match=f'`envsubst` failed \\({exitStat}\\)',
    ):
        context.hub.Run('pod-scenarios')
    context.boundary.commandRun.assert_called_once_with(
        ['envsubst'], input=b'scenario-template',
        spPars={'text': False},
    )
    context.scnFile.write_bytes.assert_not_called()
    context.boundary.tempEnv.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize('selector', [
    'KRKN_HUB__TEMPLATE__CFG__FN', 'KRKN_HUB__TEMPL__SCN__FN',
    'SCENARIO_FILE',
])
def test_Run_RejectsMissingRequiredVariablesAndExitsTemporaryEnvironment(
    hubContext: SimpleNamespace,
    selector: str,
) -> None:
    context = hubContext
    context.environ.pop(selector)
    beforeEnv = dict(context.environ)
    with pytest.raises(KeyError) as raised:
        context.hub.Run('pod-scenarios')
    assert (raised.value.args == (selector,))

    assert (context.environ == beforeEnv)
    context.scnFile.write_bytes.assert_not_called()
    context.cfgFile.write_bytes.assert_not_called()
    context.boundary.tempEnv.return_value.__exit__.assert_called_once()


def test_Run_PropagatesOutputWriteErrorAndExitsTemporaryEnvironment(
    hubContext: SimpleNamespace,
) -> None:
    context = hubContext
    error = PermissionError('synthetic output write failure')
    context.scnFile.write_bytes.side_effect = error
    with pytest.raises(PermissionError) as raised:
        context.hub.Run(scnName='pod-scenarios')
    assert (raised.value is error)
    context.scnFile.write_bytes.assert_called_once_with(b'canned-render')
    context.boundary.runner.assert_not_called()
    context.cfgFile.write_bytes.assert_not_called()
    context.boundary.tempEnv.return_value.__exit__.assert_called_once()
