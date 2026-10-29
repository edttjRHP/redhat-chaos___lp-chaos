import dataclasses
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import fixtures.Krkn as MUT
from libs.CustomTypes import NamedDict
from libs.Utils.CmdExec import CmdRes
import Utils


def test_FxtKrkn_YieldsFrozenKrknHubBundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ocFactory = mock.MagicMock(name='ocFactory')
    virtCtlFactory = mock.MagicMock(name='virtCtlFactory')
    request = SimpleNamespace(param={
        'kCfg': '/custom/kubeconfig',
        'ns': 'custom-ns',
        'cliCfg': NamedDict(
            ocCfg={'context': 'oc-context'},
            vcCfg={'kubeconfig': '/virt-kubeconfig'},
        ),
        'krknPars': NamedDict(
            rootPath=NamedDict(
                krkn=Path('/tools/krkn'),
                krknHub=Path('/tools/krknHub'),
            ),
            python=Path('/tools/krkn/.venv/bin/python'),
        ),
        'testPars': NamedDict(vm='vm-one', results=[]),
    })

    generator = MUT.FxtKrkn.__wrapped__(
        request=request,
        fxtOCcli=ocFactory,
        fxtVirtCtlCLI=virtCtlFactory,
    )
    context = next(generator)

    assert isinstance(context.krknHub, MUT.KrknHub)
    assert (context.krknHub.rootPath == Path('/tools/krknHub'))
    assert (context.krknHub.krkn == Path('/tools/krkn/run_kraken.py'))
    assert (context.krknHub.kCfg == '/custom/kubeconfig')
    assert (context.krknHub.python == Path('/tools/krkn/.venv/bin/python'))
    assert (context.krknPars.rootPath.krkn == Path('/tools/krkn'))
    assert (context.testPars == NamedDict(vm='vm-one', results=[]))
    assert (context.ns == 'custom-ns')
    assert (context.cli.oc is ocFactory.return_value)
    assert (context.cli.virtCtl is virtCtlFactory.return_value)
    ocFactory.assert_called_once_with(ns='custom-ns', context='oc-context')
    virtCtlFactory.assert_called_once_with(
        ns='custom-ns',
        kubeconfig='/virt-kubeconfig',
    )

    for fld in ('krknHub', 'krknPars', 'testPars', 'cli', 'ns'):
        orig = getattr(context, fld)
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(context, fld, object())
        assert (getattr(context, fld) is orig)

    context.testPars.results.append('recovered')
    assert (context.testPars.results == ['recovered'])
    with pytest.raises(StopIteration):
        next(generator)


@pytest.mark.parametrize('hasRequestParam', [False, True])
def test_FxtKrkn_UsesDefaultConfigurationWithAbsentOrEmptyRequestParam(
    hasRequestParam: bool,
) -> None:
    request = (
        SimpleNamespace(param={}) if hasRequestParam else SimpleNamespace()
    )
    ocFactory = mock.MagicMock()
    virtCtlFactory = mock.MagicMock()

    generator = MUT.FxtKrkn.__wrapped__(request, ocFactory, virtCtlFactory)
    context = next(generator)
    with pytest.raises(StopIteration):
        next(generator)

    assert (context.ns == 'default')
    assert (context.krknHub.krkn == (
        Path(MUT._DEF__FXT_CFG.krknPars.rootPath.krkn) / 'run_kraken.py'
    ))
    assert (context.krknHub.rootPath == Path(
        MUT._DEF__FXT_CFG.krknPars.rootPath.krknHub
    ))
    assert (context.krknHub.python is None)
    ocFactory.assert_called_once_with(ns='default')
    virtCtlFactory.assert_called_once_with(ns='default')


def test_KrknHub_Run_SourcesEnvBuildsArgsAndInvokesVenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hubRoot = tmp_path / 'krknHub'
    krknRoot = tmp_path / 'krkn'
    hubRoot.mkdir()
    krknRoot.mkdir()
    envFile = hubRoot / 'scenario' / 'env.sh'
    envFile.parent.mkdir()
    envFile.write_text('export SCENARIO=demo\n')
    prepMod = hubRoot / 'scenario' / 'build_args.py'
    prepMod.write_text(
        'def BuildArgs(prefix):\n'
        '    return [prefix, "--config=demo.yaml"]\n'
    )
    runScript = krknRoot / 'run_kraken.py'
    runScript.write_text('print("krkn")\n')

    Utils.AutoPatch(monkeypatch, MUT, 'GetEnvDelta')
    MUT.GetEnvDelta.return_value = (
        ['OLD_KEY'],
        {'SCENARIO': 'demo', 'FROM_HUB': '1'},
    )
    Utils.AutoPatch(monkeypatch, MUT, 'RunInVenv')
    MUT.RunInVenv.return_value = CmdRes('', '', 0, True)
    monkeypatch.setenv('OLD_KEY', 'stale')
    monkeypatch.setenv('KEEP', 'yes')

    hub = MUT.KrknHub(
        hubRoot,
        runScript,
        '/tmp/kubeconfig',
        python=krknRoot / '.venv' / 'bin' / 'python',
    )
    hub.Run(
        Path('scenario/env.sh'),
        MUT.FuncParsKrknHub_Run_krknPrepMod(
            modPath=Path('scenario/build_args.py'),
            modFuncArgsPos=['--debug'],
        ),
        envVars={'EXTRA': 'value', 'DROP': None},
    )

    MUT.GetEnvDelta.assert_called_once()
    MUT.RunInVenv.assert_called_once()
    (root, argv) = MUT.RunInVenv.call_args.args
    assert (root == krknRoot)
    assert (argv == [
        str(runScript), '--debug', '--config=demo.yaml',
    ])
    assert (MUT.RunInVenv.call_args.kwargs['python'] == (
        krknRoot / '.venv' / 'bin' / 'python'
    ))
    envVars = MUT.RunInVenv.call_args.kwargs['envVars']
    assert (envVars['KUBECONFIG'] == '/tmp/kubeconfig')
    assert (envVars['EXTRA'] == 'value')
    assert (envVars['DROP'] is None)


def test_KrknHub_Run_RejectsEmptyPrepFunctionName(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'GetEnvDelta')
    MUT.GetEnvDelta.return_value = ([], {})
    hub = MUT.KrknHub(tmp_path, tmp_path / 'run_kraken.py', '/kcfg')
    with pytest.raises(ValueError, match='can not be empty'):
        hub.Run(
            'env.sh',
            MUT.FuncParsKrknHub_Run_krknPrepMod(
                modPath='prep.py', modFuncName='   ',
            ),
        )


def test_KrknHub_Run_RejectsMissingPrepFunction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prep = tmp_path / 'prep.py'
    prep.write_text('VALUE = 1\n')
    Utils.AutoPatch(monkeypatch, MUT, 'GetEnvDelta')
    MUT.GetEnvDelta.return_value = ([], {})

    hub = MUT.KrknHub(tmp_path, tmp_path / 'run_kraken.py', '/kcfg')
    with pytest.raises(AttributeError, match='has no function'):
        hub.Run(
            'env.sh',
            MUT.FuncParsKrknHub_Run_krknPrepMod(
                modPath='prep.py', modFuncName='BuildArgs',
            ),
        )


def test_KrknHub_Run_RejectsNonCallablePrepAttribute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prep = tmp_path / 'prep.py'
    prep.write_text('BuildArgs = 1\n')
    Utils.AutoPatch(monkeypatch, MUT, 'GetEnvDelta')
    MUT.GetEnvDelta.return_value = ([], {})

    hub = MUT.KrknHub(tmp_path, tmp_path / 'run_kraken.py', '/kcfg')
    with pytest.raises(TypeError, match='is not callable'):
        hub.Run(
            'env.sh',
            MUT.FuncParsKrknHub_Run_krknPrepMod(modPath='prep.py'),
        )


def test_KrknHub_Run_RejectsNonStringArgList(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prep = tmp_path / 'prep.py'
    prep.write_text('def BuildArgs():\n    return [1, "x"]\n')
    Utils.AutoPatch(monkeypatch, MUT, 'GetEnvDelta')
    MUT.GetEnvDelta.return_value = ([], {})

    hub = MUT.KrknHub(tmp_path, tmp_path / 'run_kraken.py', '/kcfg')
    with pytest.raises(TypeError, match='must produce'):
        hub.Run(
            'env.sh',
            MUT.FuncParsKrknHub_Run_krknPrepMod(modPath='prep.py'),
        )


def test_FxtKrkn_RegistrationUsesFunctionScope() -> None:
    assert (MUT.FxtKrkn.name == 'fxtKrkn')
    assert (MUT.FxtKrkn._fixture_function_marker.scope == 'function')
