import dataclasses
from datetime import datetime, timezone
import importlib.util
import inspect
import os
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import fixtures.Krkn as MUT
from libs.CustomTypes import NamedDict
from libs.Utils.CmdExec import CmdRes


@pytest.mark.parametrize('scenario', [
    'test_VMsurvivesVirtLauncherKill',
    'test_HostSideKernelScanOnVMnode',
])
def test_FxtKrkn_ExamplesRemainSkippedHubPlaceholders(
    pytestconfig: pytest.Config,
    scenario: str,
) -> None:
    pytestconfig.addinivalue_line('markers', 'krkn: Krkn chaos scenario.')
    path = (
        Path(MUT.__file__).resolve().parents[1] /
        'test--suites/ts--example/test_Krkn.py'
    )
    spec = importlib.util.spec_from_file_location('krkn_examples', path)
    assert ((spec is not None) and (spec.loader is not None))
    examples = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(examples)
    method = getattr(examples.TestChaos, scenario)
    marks = {mark.name: mark for mark in method.pytestmark}
    assert (set(marks) == {'krkn', 'skip'})
    assert (marks['skip'].kwargs == {
        'reason': 'Krkn Hub support for the new design is pending.',
    })
    assert (list(inspect.signature(method).parameters) == ['self'])


@pytest.mark.parametrize('field', [
    'krknHub', 'krknInf', 'testPars', 'cli', 'ns',
])
def test_FxtKrkn_YieldsFrozenObjectWithMutableContents(field: str) -> None:
    krknPars = NamedDict(targets=[])
    fxtInf = NamedDict(
        rootPath=NamedDict(
            krkn=Path('/synthetic/krkn'), krknHub=Path('/synthetic/hub'),
        ),
        krknCfg=krknPars,
    )
    testPars = NamedDict(vm='vm-one', results=[])
    ocFactory = mock.Mock()
    virtCtlFactory = mock.Mock()
    generator = MUT.FxtKrkn.__wrapped__(
        request=SimpleNamespace(param={
            'kCfg': '/synthetic/config', 'ns': 'custom-ns',
            'cliCfg': NamedDict(
                ocCfg={'context': 'oc-context'},
                vcCfg={'kubeconfig': '/synthetic/virt-config'},
            ),
            'fxtInf': fxtInf, 'testPars': testPars,
        }),
        fxtOCcli=ocFactory, fxtVirtCtlCLI=virtCtlFactory,
    )
    try:
        context = next(generator)
        assert (isinstance(context, MUT.FxtObjKrkn))
        assert ({field.name for field in dataclasses.fields(context)} == {
            'krknHub', 'krknInf', 'testPars', 'cli', 'ns',
        })
        assert (isinstance(context.krknHub, MUT.KrknHub))
        assert (context.krknHub.rootPath == Path('/synthetic/hub'))
        assert (context.krknHub.krkn == Path('/synthetic/krkn/run_kraken.py'))
        assert (context.krknHub.kCfg == '/synthetic/config')
        assert (context.krknInf == krknPars)
        assert (context.testPars == testPars)
        assert (context.ns == 'custom-ns')
        assert (context.cli.oc is ocFactory.return_value)
        assert (context.cli.virtCtl is virtCtlFactory.return_value)
        ocFactory.assert_called_once_with(ns='custom-ns', context='oc-context')
        virtCtlFactory.assert_called_once_with(
            ns='custom-ns', kubeconfig='/synthetic/virt-config',
        )
        original = getattr(context, field)
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(context, field, object())
        assert (getattr(context, field) is original)
        context.krknInf.targets.append('node-one')
        context.testPars.results.append('recovered')
        context.cli.oc.Run.return_value = 'running'
        assert (context.krknInf.targets == ['node-one'])
        assert (context.testPars.results == ['recovered'])
        assert (context.cli.oc.Run() == 'running')
        with pytest.raises(StopIteration):
            next(generator)
    finally:
        generator.close()


@pytest.mark.parametrize('hasRequestParam', [False, True])
def test_FxtKrkn_UsesDefaultConfigurationWithAbsentOrEmptyRequestParam(
    hasRequestParam: bool,
) -> None:
    request = (
        SimpleNamespace(param={}) if hasRequestParam
        else SimpleNamespace()
    )
    ocFactory = mock.Mock()
    virtCtlFactory = mock.Mock()
    generator = MUT.FxtKrkn.__wrapped__(request, ocFactory, virtCtlFactory)
    try:
        context = next(generator)
        defaults = MUT._DEF__FXT_CFG
        assert (context.krknHub.rootPath == defaults.fxtInf.rootPath.krknHub)
        assert (context.krknHub.krkn == (
            defaults.fxtInf.rootPath.krkn / 'run_kraken.py'
        ))
        assert (context.krknHub.kCfg == defaults.kCfg)
        assert (context.krknInf == defaults.fxtInf.krknCfg)
        assert (context.testPars == defaults.testPars)
        assert (context.ns == defaults.ns)
        assert (context.cli.oc is ocFactory.return_value)
        assert (context.cli.virtCtl is virtCtlFactory.return_value)
        ocFactory.assert_called_once_with(ns=defaults.ns)
        virtCtlFactory.assert_called_once_with(ns=defaults.ns)
        with pytest.raises(StopIteration):
            next(generator)
    finally:
        generator.close()


@pytest.mark.parametrize('failingCLI', ['oc', 'virtCtl'])
def test_FxtKrkn_PropagatesCLIFactoryErrors(failingCLI: str) -> None:
    factories = NamedDict(oc=mock.Mock(), virtCtl=mock.Mock())
    error = RuntimeError('synthetic factory failure')
    factories[failingCLI].side_effect = error
    generator = MUT.FxtKrkn.__wrapped__(
        SimpleNamespace(param={'kCfg': 'fake-config'}),
        factories.oc, factories.virtCtl,
    )
    with pytest.raises(RuntimeError) as raised:
        next(generator)
    assert (raised.value is error)
    factories.oc.assert_called_once_with(ns=MUT._DEF__FXT_CFG.ns)
    if (failingCLI == 'oc'): factories.virtCtl.assert_not_called()
    else:
        factories.virtCtl.assert_called_once_with(ns=MUT._DEF__FXT_CFG.ns)


def test_Init_NormalizesHubRootAndPreservesScriptAndConfigPaths() -> None:
    script = Path('/synthetic/run.py')
    config = Path('/synthetic/config')
    hub = MUT.KrknHub('/synthetic/hub', script, config)
    assert (hub.rootPath == Path('/synthetic/hub'))
    assert (hub.krkn == script)
    assert (hub.kCfg is config)


@pytest.fixture(name='hubContext')
def HubContext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NamedDict:
    kHubRoot = tmp_path / 'hub root'
    scnDir = kHubRoot / 'pod-scenarios'
    krknRoot = tmp_path / 'krkn root'
    scnDir.mkdir(parents=True)
    krknRoot.mkdir()
    runRoot = tmp_path / 'runs'
    runDir = runRoot / '20260203T040506Z--pod-scenarios'
    monkeypatch.setenv(
        'IENG_PYTEST____FXT__KRKN____KRKN__RUN_DIR__ROOT', str(runRoot),
    )
    clock = mock.Mock()
    clock.now.return_value = datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    monkeypatch.setattr(MUT, 'datetime', clock)
    monkeypatch.setenv('KRKN_TEST_REMOVE', 'parent')
    monkeypatch.delenv('KRKN_TEST_ORDER', raising=False)
    (kHubRoot / 'env.sh').write_text(
        'RootValue() { printf "%s" root; }\n'
        'export KRKN_HUB__TEMPL__SCN__FN=selected.yaml.template\n'
        'export KRKN_HUB__TEMPLATE__CFG__FN=config.yaml.template\n'
        'export ALERTS_PATH=config/alerts.yaml\n'
        'export METRICS_PATH=config/metrics-aggregated.yaml\n'
        'export RESILIENCY_FILE=$ALERTS_PATH\n'
        'export ROLLBACK_VERSIONS_DIRECTORY=rollback\n'
        'export TELEMETRY_ARCHIVE_PATH=archive\n'
        'export KRKN_TEST_ORDER=root\n'
        'unset KRKN_TEST_REMOVE\n'
    )
    (scnDir / 'env.sh').write_text(
        'export KRKN_TEST_ORDER="$(RootValue)-scenario"\n'
        'export SCENARIO_FILE=scenarios/synthetic.yaml\n'
        'export SCENARIO_TYPE=custom_type\n'
    )
    (scnDir / 'selected.yaml.template').write_text(
        'value: "$KRKN_TEST_ORDER"\n'
        'literal: "${UNCHANGED:-default}"\n'
    )
    (kHubRoot / 'config.yaml.template').write_text(
        'scenario: "$SCENARIO_FILE"\n'
        'kind: "$SCENARIO_TYPE"\n'
    )
    script = krknRoot / 'run_kraken.py'
    script.write_text(
        'import os, sys\n'
        'from pathlib import Path\n'
        'observed = (Path.cwd(), sys.argv[1:], __name__,\n'
        '            os.getenv("KUBECONFIG"),\n'
        '            os.getenv("KRKN_TEST_REMOVE"),\n'
        '            os.getenv("KRKN_TEST_ORDER"))\n'
        'os.environ["KRKN_TEST_ADDED"] = "temporary"\n'
    )
    return NamedDict(
        hub=MUT.KrknHub(kHubRoot, script, 'fake-config'),
        kHubRoot=kHubRoot, scnDir=scnDir, krknRoot=krknRoot, script=script,
        scnFile=runDir / 'scenarios/synthetic.yaml',
        cfgFile=runDir / 'config/chaos.yaml',
        runRoot=runRoot, runDir=runDir, clock=clock,
    )


@pytest.mark.parametrize('debug', [None, 'False', 'True', 'true', '1'])
@pytest.mark.parametrize('overrides', [None, {}, {
    'KUBECONFIG': 'override-config', 'KRKN_TEST_ORDER': 7,
    'KRKN_TEST_REMOVE': None,
}])
def test_Run_RendersSelectedTemplatesAndPreservesDispatchContract(
    hubContext: NamedDict,
    monkeypatch: pytest.MonkeyPatch,
    debug: str | None,
    overrides: dict | None,
) -> None:
    context = hubContext
    if (debug is None): monkeypatch.delenv('KRKN_DEBUG', raising=False)
    else: monkeypatch.setenv('KRKN_DEBUG', debug)
    beforeEnv = dict(os.environ)
    beforeCwd = Path.cwd()
    beforeArgv = sys.argv[:]
    beforePython = sys.executable
    source = mock.Mock(wraps=MUT.DoShellSource)
    runner = mock.Mock(wraps=MUT.RunFromPath)
    monkeypatch.setattr(MUT, 'DoShellSource', source)
    monkeypatch.setattr(MUT, 'RunFromPath', runner)
    initialGlobals = {'injected': object()}
    result = context.hub.Run(
        'pod-scenarios', overrides, initialGlobals, 'synthetic_run',
    )
    args = [f'--config={context.cfgFile}']
    if (debug == 'True'): args.extend(['--debug', 'True'])
    source.assert_called_once_with(envFiles=[
        context.kHubRoot / 'env.sh', context.scnDir / 'env.sh',
    ])
    runner.assert_called_once_with(
        context.script, args,
        {'KUBECONFIG': 'fake-config', **(overrides or {})},
        initialGlobals, 'synthetic_run',
    )
    assert (result['injected'] is initialGlobals['injected'])
    assert (result['observed'] == (
        context.runDir, args, 'synthetic_run',
        (overrides or {}).get('KUBECONFIG', 'fake-config'), None,
        '7' if overrides else 'root-scenario',
    ))
    assert (context.scnFile.read_text() == (
        'value: "root-scenario"\nliteral: "${UNCHANGED:-default}"\n'
    ))
    assert (context.cfgFile.read_text() == (
        f'scenario: "{context.scnFile}"\nkind: "custom_type"\n'
    ))
    assert (dict(os.environ) == beforeEnv)
    assert (Path.cwd() == beforeCwd)
    assert (sys.argv == beforeArgv)
    assert (sys.executable == beforePython)


@pytest.mark.parametrize('absolute', [False, True])
def test_Run_UsesDefaultOptionsAndAbsoluteTemplateSelectors(
    hubContext: NamedDict,
    absolute: bool,
) -> None:
    context = hubContext
    if absolute:
        with (context.scnDir / 'env.sh').open('a') as envFile:
            envFile.write(
                'export KRKN_HUB__TEMPL__SCN__FN=' +
                shlex.quote(str(context.scnDir / 'selected.yaml.template')) +
                '\nexport KRKN_HUB__TEMPLATE__CFG__FN=' +
                shlex.quote(str(context.kHubRoot / 'config.yaml.template')) +
                '\n'
            )
    context.script.write_text(
        'import sys, yaml\n'
        'from pathlib import Path\n'
        'config = yaml.safe_load(Path(sys.argv[1].split("=", 1)[1])'
        '.read_text())\n'
        'scenario = yaml.safe_load(Path(config["scenario"]).read_text())\n'
        'assert Path("scenarios/synthetic.yaml").read_text() == '
        'Path(config["scenario"]).read_text()\n'
        'observed = (__name__, scenario["value"])\n'
    )
    result = context.hub.Run(Path('pod-scenarios'))
    assert (result['observed'] == ('__main__', 'root-scenario'))


@pytest.mark.parametrize('failure', [
    'root', 'scenario', 'selector', 'template', 'envsubst', 'missingRunner',
    'runner', 'exit',
])
def test_Run_PreservesErrorsAndRestoresEnvironmentAndWorkingDirectory(
    hubContext: NamedDict,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    context = hubContext
    if (failure in ('root', 'scenario')):
        envPath = (
            context.kHubRoot if (failure == 'root')
            else context.scnDir
        ) / 'env.sh'
        with envPath.open('a') as envFile: envFile.write('return 7\n')
        errorType = RuntimeError
    elif (failure == 'selector'):
        with (context.scnDir / 'env.sh').open('a') as envFile:
            envFile.write('unset KRKN_HUB__TEMPL__SCN__FN\n')
        errorType = KeyError
    elif (failure == 'template'):
        (context.scnDir / 'selected.yaml.template').unlink()
        errorType = FileNotFoundError
    elif (failure == 'envsubst'):
        executor = mock.Mock()
        executor.Run.return_value = CmdRes(b'', b'synthetic failure', 7, False)
        monkeypatch.setattr(MUT, 'CmdExec', mock.Mock(return_value=executor))
        errorType = RuntimeError
    elif (failure == 'missingRunner'):
        context.script.unlink()
        errorType = FileNotFoundError
    else:
        context.script.write_text(
            'import os\n'
            'os.chdir("..")\n'
            'os.environ["KRKN_TEST_ADDED"] = "temporary"\n' +
            (
                'raise SystemExit(7)\n' if (failure == 'exit')
                else 'raise RuntimeError("synthetic runner failure")\n'
            )
        )
        errorType = SystemExit if (failure == 'exit') else RuntimeError
    beforeEnv = dict(os.environ)
    beforeCwd = Path.cwd()
    beforeArgv = sys.argv[:]
    with pytest.raises(errorType) as raised:
        context.hub.Run('pod-scenarios')
    if (failure == 'exit'): assert (raised.value.code == 7)
    assert (dict(os.environ) == beforeEnv)
    assert (Path.cwd() == beforeCwd)
    assert (sys.argv == beforeArgv)


@pytest.mark.parametrize('overlap', ['scenario', 'config', 'cross', 'symlink'])
def test_Run_ReadsTemplatesBeforeOpeningOverlappingOutputs(
    hubContext: NamedDict,
    monkeypatch: pytest.MonkeyPatch,
    overlap: str,
) -> None:
    context = hubContext
    scnTmpl = context.scnDir / 'selected.yaml.template'
    cfgTmpl = context.kHubRoot / 'config.yaml.template'
    scenText = scnTmpl.read_text()
    cfgText = cfgTmpl.read_text()
    if (overlap == 'scenario'):
        scnTmpl = context.scnFile
    elif (overlap == 'config'):
        cfgTmpl = context.cfgFile
    elif (overlap == 'cross'):
        (scnTmpl, cfgTmpl) = (context.cfgFile, context.scnFile)
    else:
        scnTmpl = context.scnDir / 'alias.template'
        scnTmpl.symlink_to(context.scnFile)
    originalMkdir = Path.mkdir

    def CreateTemplates(path: Path, *args: object, **kwArgs: object) -> None:
        originalMkdir(path, *args, **kwArgs)
        if ((path == context.runDir) and not context.scnFile.parent.exists()):
            context.scnFile.parent.mkdir()
            context.cfgFile.parent.mkdir()
            scnTmpl.write_text(scenText)
            cfgTmpl.write_text(cfgText)

    monkeypatch.setattr(Path, 'mkdir', CreateTemplates)
    with (context.scnDir / 'env.sh').open('a') as envFile:
        envFile.write(
            'export KRKN_HUB__TEMPL__SCN__FN=' +
            shlex.quote(str(scnTmpl)) +
            '\nexport KRKN_HUB__TEMPLATE__CFG__FN=' +
            shlex.quote(str(cfgTmpl)) + '\n'
        )
    context.hub.Run('pod-scenarios')
    assert (context.scnFile.read_text() == (
        'value: "root-scenario"\nliteral: "${UNCHANGED:-default}"\n'
    ))
    assert (context.cfgFile.read_text() == (
        f'scenario: "{context.scnFile}"\nkind: "custom_type"\n'
    ))


def test_Run_MissingConfigTemplatePreservesExistingScenarioOutput(
    hubContext: NamedDict,
) -> None:
    context = hubContext
    scnFile = context.scnDir / 'existing.yaml'
    scnFile.write_text('original scenario\n')
    with (context.scnDir / 'env.sh').open('a') as envFile:
        envFile.write('export SCENARIO_FILE=' + shlex.quote(str(scnFile)))
    (context.kHubRoot / 'config.yaml.template').unlink()
    beforeEnv = dict(os.environ)
    beforeCwd = Path.cwd()
    with pytest.raises(FileNotFoundError): context.hub.Run('pod-scenarios')
    assert (scnFile.read_text() == 'original scenario\n')
    assert (dict(os.environ) == beforeEnv)
    assert (Path.cwd() == beforeCwd)


@pytest.mark.parametrize('error', [RuntimeError('synthetic'), SystemExit(7)])
def test_Run_PreservesInjectedExceptionIdentity(
    hubContext: NamedDict,
    error: BaseException,
) -> None:
    context = hubContext
    context.script.write_text('raise injectedError\n')
    beforeEnv = dict(os.environ)
    beforeCwd = Path.cwd()
    beforePython = sys.executable
    with pytest.raises(type(error)) as raised:
        context.hub.Run('pod-scenarios', initGlb={'injectedError': error})
    assert (raised.value is error)
    assert (dict(os.environ) == beforeEnv)
    assert (Path.cwd() == beforeCwd)
    assert (sys.executable == beforePython)


def test_Init_ResolvesRelativeRunnerBeforeChangingDirectory(
    hubContext: NamedDict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = hubContext
    monkeypatch.chdir(context.krknRoot.parent)
    hub = MUT.KrknHub(
        context.kHubRoot, 'krkn root/run_kraken.py', 'fake-config',
    )
    assert (hub.krkn == context.script)
    result = hub.Run('pod-scenarios')
    assert (result['observed'][0] == context.runDir)


def test_Run_UsesUTCAndRejectsSameSecondCollisions(
    hubContext: NamedDict,
) -> None:
    context = hubContext
    context.hub.Run('pod-scenarios')
    context.clock.now.assert_called_once_with(timezone.utc)
    assert (context.runDir.is_dir())
    context.scnFile.write_bytes(b'preserved artifact\r\n')
    beforeEnv = dict(os.environ)
    beforeCwd = Path.cwd()
    with pytest.raises(FileExistsError): context.hub.Run('pod-scenarios')
    assert (context.scnFile.read_bytes() == b'preserved artifact\r\n')
    assert (dict(os.environ) == beforeEnv)
    assert (Path.cwd() == beforeCwd)


def test_Run_RetainsNestedSuffixAndResolvesRelativeRunRoot(
    hubContext: NamedDict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = hubContext
    monkeypatch.chdir(context.kHubRoot.parent)
    monkeypatch.setenv(
        'IENG_PYTEST____FXT__KRKN____KRKN__RUN_DIR__ROOT', 'relative/runs',
    )
    nested = context.kHubRoot / 'group/pod-scenarios'
    nested.parent.mkdir()
    context.scnDir.rename(nested)
    result = context.hub.Run('group/pod-scenarios')
    runDir = Path.cwd() / 'relative/runs/20260203T040506Z--group/pod-scenarios'
    assert (result['observed'][0] == runDir)
    assert ((runDir / 'config/chaos.yaml').is_file())


@pytest.mark.parametrize('absolute', [False, True])
def test_Run_AnchorsPathsAndCreatesOnlyOutputDirectories(
    hubContext: NamedDict,
    absolute: bool,
) -> None:
    context = hubContext
    inputKeys = ('ALERTS_PATH', 'METRICS_PATH', 'RESILIENCY_FILE')
    outputKeys = (
        'SCENARIO_FILE', 'ROLLBACK_VERSIONS_DIRECTORY',
        'TELEMETRY_ARCHIVE_PATH',
    )
    paths = {
        key: Path('nested') / key.lower() for key in (*inputKeys, *outputKeys)
    }
    if absolute:
        paths = {key: context.kHubRoot / path for (key, path) in paths.items()}
    expected = {
        key: (root / paths[key]).resolve()
        for (root, keys) in (
            (context.krknRoot, inputKeys), (context.runDir, outputKeys),
        ) for key in keys
    }
    with (context.scnDir / 'env.sh').open('a') as envFile:
        for (key, path) in paths.items():
            envFile.write(f'export {key}={shlex.quote(str(path))}\n')
        envFile.write('export KRKN_KUBE_CONFIG=unchanged/config\n')
    template = context.scnDir / 'selected.yaml.template'
    template.write_bytes((
        '\r\n'.join(f'${key}' for key in (*inputKeys, *outputKeys)) +
        '\r\n$KRKN_KUBE_CONFIG\r\n'
    ).encode())
    beforeEnv = dict(os.environ)
    context.hub.Run('pod-scenarios', {'ALERTS_PATH': 'execution-only'})
    assert (expected['SCENARIO_FILE'].read_bytes() == (
        '\r\n'.join(str(path) for path in expected.values()) +
        '\r\nunchanged/config\r\n'
    ).encode())
    assert (context.cfgFile.is_file())
    assert (expected['ROLLBACK_VERSIONS_DIRECTORY'].is_dir())
    assert (expected['TELEMETRY_ARCHIVE_PATH'].is_dir())
    assert (all(not expected[key].exists() for key in inputKeys))
    assert (dict(os.environ) == beforeEnv)


@pytest.mark.parametrize('alerts', [None, '', 'custom/alerts.yaml'])
@pytest.mark.parametrize('resiliency', [None, ''])
def test_Run_PreservesUnsetAndEmptyOptionalInputs(
    hubContext: NamedDict,
    alerts: str | None,
    resiliency: str | None,
) -> None:
    context = hubContext
    with (context.scnDir / 'env.sh').open('a') as envFile:
        envFile.write(
            'unset METRICS_PATH ROLLBACK_VERSIONS_DIRECTORY '
            'TELEMETRY_ARCHIVE_PATH\n'
        )
        for (key, value) in (
            ('ALERTS_PATH', alerts), ('RESILIENCY_FILE', resiliency),
        ):
            envFile.write(
                f'unset {key}\n' if (value is None) else
                f'export {key}={shlex.quote(value)}\n'
            )
    (context.scnDir / 'selected.yaml.template').write_text(
        '$RESILIENCY_FILE\n$ALERTS_PATH\n$METRICS_PATH\n'
        '$ROLLBACK_VERSIONS_DIRECTORY\n$TELEMETRY_ARCHIVE_PATH\n'
    )
    context.script.write_text(
        'import os\n'
        'optionalInputs = {key: os.getenv(key) for key in '
        '("ALERTS_PATH", "RESILIENCY_FILE")}\n'
    )
    beforeEnv = dict(os.environ)
    result = context.hub.Run('pod-scenarios')
    expectedAlerts = str(context.krknRoot / alerts) if alerts else ''
    assert (context.scnFile.read_text() == (
        f'\n{expectedAlerts}\n\n\n\n'
    ))
    assert (result['optionalInputs'] == {
        'ALERTS_PATH': expectedAlerts if alerts else alerts,
        'RESILIENCY_FILE': resiliency,
    })
    assert (dict(os.environ) == beforeEnv)


@pytest.mark.parametrize('exitStat', [7, -2])
@pytest.mark.parametrize('failureIndex', [0, 1])
def test_Run_ChecksCmdExecFailuresAndRestoresState(
    hubContext: NamedDict,
    monkeypatch: pytest.MonkeyPatch,
    exitStat: int,
    failureIndex: int,
) -> None:
    context = hubContext
    executor = mock.Mock()
    executor.Run.side_effect = [
        *([CmdRes(b'rendered', b'', 0, True)] * failureIndex),
        CmdRes(b'', b'synthetic failure', exitStat, False),
    ]
    monkeypatch.setattr(MUT, 'CmdExec', mock.Mock(return_value=executor))
    beforeEnv = dict(os.environ)
    beforeCwd = Path.cwd()
    beforeArgv = sys.argv
    with pytest.raises(
        RuntimeError, match=f'`envsubst` failed \\({exitStat}\\)',
    ):
        context.hub.Run('pod-scenarios')
    assert (executor.Run.call_count == (failureIndex + 1))
    firstCall = executor.Run.call_args_list[0]
    assert (firstCall == mock.call(
        ['envsubst'],
        input=(context.scnDir / 'selected.yaml.template').read_bytes(),
        spPars={'text': False},
    ))
    assert (context.scnFile.exists() == bool(failureIndex))
    assert not context.cfgFile.exists()
    assert (dict(os.environ) == beforeEnv)
    assert (Path.cwd() == beforeCwd)
    assert (sys.argv is beforeArgv)


def test_Run_UsesByteCapturingCmdExec(hubContext: NamedDict) -> None:
    context = hubContext
    with mock.patch.object(MUT, 'CmdExec', wraps=MUT.CmdExec) as factory:
        context.hub.Run('pod-scenarios')
    assert (factory.call_count == 2)
    assert (context.scnFile.read_bytes().startswith(b'value: "root-scenario"'))


def test_Run_AcceptsScnNameKeyword(hubContext: NamedDict) -> None:
    context = hubContext
    result = context.hub.Run(scnName='pod-scenarios')
    assert (result['observed'][0] == context.runDir)
    assert (context.cfgFile.is_file())


def test_Run_DefaultsToTmpAndRestoresStateOnSetupFailure(
    hubContext: NamedDict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv('IENG_PYTEST____FXT__KRKN____KRKN__RUN_DIR__ROOT')
    error = OSError('synthetic mkdir failure')
    mkdir = mock.create_autospec(Path.mkdir, side_effect=error)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    beforeEnv = dict(os.environ)
    beforeCwd = Path.cwd()
    with pytest.raises(OSError) as raised:
        hubContext.hub.Run('pod-scenarios')
    assert (raised.value is error)
    mkdir.assert_called_once_with(
        Path('/tmp/run-dir--krkn/20260203T040506Z--pod-scenarios'),
        parents=True, exist_ok=False,
    )
    assert (dict(os.environ) == beforeEnv)
    assert (Path.cwd() == beforeCwd)


@pytest.mark.parametrize('missing', [False, True])
def test_Run_HandlesNativeEnvsubstFailure(
    hubContext: NamedDict,
    monkeypatch: pytest.MonkeyPatch,
    missing: bool,
) -> None:
    context = hubContext
    tools = context.kHubRoot / 'tools'
    tools.mkdir()
    if not missing:
        executable = tools / 'envsubst'
        executable.write_text(
            '#!/bin/sh\nprintf synthetic-error >&2\nexit 7\n',
        )
        executable.chmod(0o755)
    originalSource = MUT.DoShellSource

    def SourceAndRestrictPath(**kwArgs: object) -> None:
        originalSource(**kwArgs)
        os.environ['PATH'] = str(tools)

    monkeypatch.setattr(MUT, 'DoShellSource', SourceAndRestrictPath)
    beforeEnv = dict(os.environ)
    beforeCwd = Path.cwd()
    exitStat = -2 if missing else 7
    with pytest.raises(
        RuntimeError, match=f'`envsubst` failed \\({exitStat}\\)',
    ):
        context.hub.Run('pod-scenarios')
    assert not context.scnFile.exists()
    assert (dict(os.environ) == beforeEnv)
    assert (Path.cwd() == beforeCwd)


@pytest.mark.parametrize('selector', [
    'KRKN_HUB__TEMPLATE__CFG__FN', 'KRKN_HUB__TEMPL__SCN__FN',
    'SCENARIO_FILE',
])
def test_Run_RejectsMissingRequiredVariablesAndRestoresState(
    hubContext: NamedDict,
    selector: str,
) -> None:
    context = hubContext
    with (context.scnDir / 'env.sh').open('a') as envFile:
        envFile.write(f'unset {selector}\n')
    beforeEnv = dict(os.environ)
    beforeCwd = Path.cwd()
    beforeArgv = sys.argv
    with pytest.raises(KeyError) as raised:
        context.hub.Run(scnName='pod-scenarios')
    assert (raised.value.args == (selector,))
    assert not context.scnFile.exists()
    assert not context.cfgFile.exists()
    assert (dict(os.environ) == beforeEnv)
    assert (Path.cwd() == beforeCwd)
    assert (sys.argv is beforeArgv)


def test_Run_PreservesOutputWriteErrorAndRestoresState(
    hubContext: NamedDict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = hubContext
    error = PermissionError('synthetic output write failure')
    writeBytes = mock.create_autospec(Path.write_bytes, side_effect=error)
    monkeypatch.setattr(Path, 'write_bytes', writeBytes)
    beforeEnv = dict(os.environ)
    beforeCwd = Path.cwd()
    beforeArgv = sys.argv
    with pytest.raises(PermissionError) as raised:
        context.hub.Run(scnName='pod-scenarios')
    assert (raised.value is error)
    writeBytes.assert_called_once_with(
        context.scnFile,
        b'value: "root-scenario"\nliteral: "${UNCHANGED:-default}"\n',
    )
    assert not context.cfgFile.exists()
    assert (dict(os.environ) == beforeEnv)
    assert (Path.cwd() == beforeCwd)
    assert (sys.argv is beforeArgv)
