import dataclasses
import importlib.util
import inspect
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

import fixtures.Krkn as MUT
from libs.CustomTypes import NamedDict


@pytest.fixture(name='hubContext')
def HubContext(monkeypatch: pytest.MonkeyPatch) -> NamedDict:
    delta = mock.Mock(return_value=(
        {'KRKN_TEST_REMOVE', 'KRKN_TEST_ABSENT'},
        {'KRKN_TEST_UPDATE': 42},
    ))
    prepFunc = mock.Mock(return_value=['--scenario', 'synthetic.yaml'])
    prepModule = SimpleNamespace(BuildArgs=prepFunc)
    importer = mock.Mock(return_value=prepModule)
    runner = mock.Mock(return_value={'result': 'synthetic'})
    monkeypatch.setattr(MUT, 'GetEnvDelta', delta)
    monkeypatch.setattr(MUT, 'ImportFromPath', importer)
    monkeypatch.setattr(MUT, 'RunFromPath', runner)
    monkeypatch.setenv('KRKN_TEST_REMOVE', 'before')
    monkeypatch.setenv('KRKN_TEST_UPDATE', 'before')
    monkeypatch.delenv('KRKN_TEST_ABSENT', raising=False)
    return NamedDict(
        hub=MUT.KrknHub('/synthetic/hub', '/synthetic/run.py', 'fake-config'),
        pars=MUT.FuncParsKrknHub_Run_krknPrepMod('prep.py'),
        delta=delta, prepFunc=prepFunc, prepModule=prepModule,
        importer=importer, runner=runner,
    )


@pytest.mark.parametrize('scenario', [
    'test_VMsurvivesVirtLauncherKill',
    'test_HostSideKernelScanOnVMnode',
])
def test_FxtKrkn_ExamplesRemainSkippedHubPlaceholders(
    pytestconfig: pytest.Config, scenario: str,
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
    'krknHub', 'krknPars', 'testPars', 'cli', 'ns',
])
def test_FxtKrkn_YieldsFrozenObjectWithMutableContents(field: str) -> None:
    krknPars = NamedDict(rootPath=NamedDict(
        krkn=Path('/synthetic/krkn'), krknHub=Path('/synthetic/hub'),
    ), targets=[])
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
            'krknPars': krknPars, 'testPars': testPars,
        }),
        fxtOCcli=ocFactory, fxtVirtCtlCLI=virtCtlFactory,
    )
    try:
        context = next(generator)
        assert (isinstance(context, MUT.FxtObjKrkn))
        assert ({field.name for field in dataclasses.fields(context)} == {
            'krknHub', 'krknPars', 'testPars', 'cli', 'ns',
        })
        assert (isinstance(context.krknHub, MUT.KrknHub))
        assert (context.krknHub.rootPath == Path('/synthetic/hub'))
        assert (context.krknHub.krkn == Path('/synthetic/krkn/run_krkn.py'))
        assert (context.krknHub.kCfg == '/synthetic/config')
        assert (context.krknPars == krknPars)
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
        context.krknPars.targets.append('node-one')
        context.testPars.results.append('recovered')
        context.cli.oc.Run.return_value = 'running'
        assert (context.krknPars.targets == ['node-one'])
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
        assert (context.krknHub.rootPath == defaults.krknPars.rootPath.krknHub)
        assert (context.krknHub.krkn == (
            defaults.krknPars.rootPath.krkn / 'run_krkn.py'
        ))
        assert (context.krknHub.kCfg == defaults.kCfg)
        assert (context.krknPars == defaults.krknPars)
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
    if (failingCLI == 'oc'):
        factories.virtCtl.assert_not_called()
    else:
        factories.virtCtl.assert_called_once_with(ns=MUT._DEF__FXT_CFG.ns)


def test_Init_NormalizesHubRootAndPreservesScriptAndConfigPaths() -> None:
    script = Path('/synthetic/run.py')
    config = Path('/synthetic/config')
    hub = MUT.KrknHub('/synthetic/hub', script, config)
    assert (hub.rootPath == Path('/synthetic/hub'))
    assert (hub.krkn is script)
    assert (hub.kCfg is config)


def test_Init_PreparationDefaultsAreIndependentAndBindingsFrozen() -> None:
    first = MUT.FuncParsKrknHub_Run_krknPrepMod('first.py')
    second = MUT.FuncParsKrknHub_Run_krknPrepMod(Path('second.py'))
    assert (first.modFuncName == 'BuildArgs')
    assert (second.modPath == Path('second.py'))
    first.modFuncArgsPos.append('value')
    first.modFuncArgsKW['key'] = 'value'
    assert (second.modFuncArgsPos == [])
    assert (second.modFuncArgsKW == {})
    for field in dataclasses.fields(first):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(first, field.name, object())


@pytest.mark.parametrize('overrides', [None, {}, {
    'KUBECONFIG': 'override-config', 'KRKN_TEST_EXTRA': 7,
}])
@pytest.mark.parametrize('args', [[], ['--scenario', 'synthetic.yaml']])
def test_Run_PreparesArgumentsDispatchesScriptAndRestoresEnvironment(
    hubContext: NamedDict, overrides: Any, args: list[str],
) -> None:
    context = hubContext
    context.prepFunc.return_value = args
    pars = MUT.FuncParsKrknHub_Run_krknPrepMod(
        'custom.py', 'Prepare', ['positional'], {'option': 'value'},
    )
    context.prepModule.Prepare = context.prepFunc
    initialGlobals = {'injected': 'synthetic'}
    before = dict(os.environ)

    def Prepare(*args: Any, **kwargs: Any) -> list[str]:
        assert ('KRKN_TEST_REMOVE' not in os.environ)
        assert ('KRKN_TEST_ABSENT' not in os.environ)
        assert (os.environ['KRKN_TEST_UPDATE'] == '42')
        os.environ['KRKN_TEST_ADDED'] = 'temporary'
        return context.prepFunc.return_value

    context.prepFunc.side_effect = Prepare
    result = context.hub.Run(
        'env.sh', pars, overrides, initialGlobals, 'synthetic_run',
    )
    assert (result is context.runner.return_value)
    assert (dict(os.environ) == before)
    context.delta.assert_called_once_with(
        MUT.BashEnv, Path('/synthetic/hub/env.sh'),
    )
    context.importer.assert_called_once_with(Path('/synthetic/hub/custom.py'))
    context.prepFunc.assert_called_once_with('positional', option='value')
    context.runner.assert_called_once_with(
        '/synthetic/run.py', args,
        {'KUBECONFIG': 'fake-config', **(overrides or {})},
        initialGlobals, 'synthetic_run',
    )


def test_Run_UsesDefaultDispatchOptions(hubContext: NamedDict) -> None:
    context = hubContext
    context.hub.Run('env.sh', context.pars)
    context.prepFunc.assert_called_once_with()
    context.runner.assert_called_once_with(
        '/synthetic/run.py', context.prepFunc.return_value,
        {'KUBECONFIG': 'fake-config'}, None, '__main__',
    )


@pytest.mark.parametrize('name', ['', ' \t\n'])
def test_Run_RejectsEmptyPreparationName(
    hubContext: NamedDict, name: str,
) -> None:
    before = dict(os.environ)
    with pytest.raises(ValueError, match='modFuncName.*can not be empty'):
        hubContext.hub.Run(
            'env.sh', MUT.FuncParsKrknHub_Run_krknPrepMod('prep.py', name),
        )
    assert (dict(os.environ) == before)
    hubContext.importer.assert_not_called()
    hubContext.runner.assert_not_called()


def test_Run_ReportsMissingPreparationCallable(hubContext: NamedDict) -> None:
    context = hubContext
    context.importer.return_value = SimpleNamespace()
    before = dict(os.environ)
    with pytest.raises(AttributeError, match='prep.py.*BuildArgs') as raised:
        context.hub.Run('env.sh', context.pars)
    assert (isinstance(raised.value.__cause__, AttributeError))
    assert (dict(os.environ) == before)
    context.prepFunc.assert_not_called()
    context.runner.assert_not_called()


@pytest.mark.parametrize('value', [None, 'BuildArgs', 42])
def test_Run_RejectsNonCallablePreparation(
    hubContext: NamedDict, value: Any,
) -> None:
    context = hubContext
    context.prepModule.BuildArgs = value
    before = dict(os.environ)
    with pytest.raises(TypeError, match='BuildArgs.*prep.py.*not callable'):
        context.hub.Run('env.sh', context.pars)
    assert (dict(os.environ) == before)
    context.prepFunc.assert_not_called()
    context.runner.assert_not_called()


@pytest.mark.parametrize('value', [None, 'arg', ('arg',), {}, [1], ['arg', 1]])
def test_Run_RejectsPreparationResultsOtherThanStringLists(
    hubContext: NamedDict, value: Any,
) -> None:
    context = hubContext
    context.prepFunc.return_value = value
    before = dict(os.environ)
    with pytest.raises(TypeError, match=r'must produce `list\[str\]`'):
        context.hub.Run('env.sh', context.pars)
    assert (dict(os.environ) == before)
    context.prepFunc.assert_called_once_with()
    context.runner.assert_not_called()


@pytest.mark.parametrize('stage', ['delta', 'importer', 'prepFunc', 'runner'])
def test_Run_PropagatesBoundaryErrorsAndRestoresEnvironment(
    hubContext: NamedDict, stage: str,
) -> None:
    context = hubContext
    before = dict(os.environ)
    error = RuntimeError('synthetic boundary failure')

    def Fail(*args: Any, **kwargs: Any) -> None:
        os.environ['KRKN_TEST_ADDED'] = 'temporary'
        raise error

    context[stage].side_effect = Fail
    with pytest.raises(RuntimeError) as raised:
        context.hub.Run('env.sh', context.pars)
    assert (raised.value is error)
    assert (dict(os.environ) == before)
    stages = ['delta', 'importer', 'prepFunc', 'runner']
    for untouched in stages[(stages.index(stage) + 1):]:
        context[untouched].assert_not_called()
