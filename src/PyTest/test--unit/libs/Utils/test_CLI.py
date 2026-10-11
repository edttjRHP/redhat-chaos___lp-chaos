from unittest import mock

import pytest

import libs.Utils.CLI as MUT
from libs.Utils.CmdExec import CmdExec, CmdRes
import Utils

optionsText = '''
  -n, --namespace string   Namespace
      --kubeconfig string  Config
ignored
'''


def CommandExecutor(result: CmdRes) -> mock.MagicMock:
    executor = mock.create_autospec(CmdExec, instance=True)
    executor.Run.return_value = result
    return executor


def test__DiscoverOptions_ParsesShortLongAndStdErr() -> None:
    executor = CommandExecutor(CmdRes(optionsText, '      --token string\n', 0, True))

    cli = MUT.GoCobraCLI('oc', cmdExec=executor, namespace='default')

    assert cli.cmnOpts == {
        'namespace': {'short': '-n', 'long': '--namespace'},
        'kubeconfig': {'short': None, 'long': '--kubeconfig'},
        'token': {'short': None, 'long': '--token'},
    }
    assert cli.shortMap == {'-n': 'namespace'}
    executor.Run.assert_called_once_with(['oc', 'options'])


def test__DiscoverOptions_FailureLeavesMapsEmpty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    executor = CommandExecutor(CmdRes('', 'not installed', 1, False))

    with caplog.at_level('WARNING', logger='libs.Utils.CLI'):
        cli = MUT.GoCobraCLI('missing tool', cmdExec=executor)

    assert cli.cmnOpts == {}
    assert cli.shortMap == {}
    assert 'Failed to discover' in caplog.text


def test_Init_DefaultExecutorIsCreated(monkeypatch: pytest.MonkeyPatch) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'CmdExec')
    MUT.CmdExec.return_value.Run.return_value = CmdRes('', '', 1, False)

    cli = MUT.GoCobraCLI('tool')

    assert cli.cmdExec is MUT.CmdExec.return_value
    MUT.CmdExec.assert_called_once_with()


def test__IsCommon_DetectsCommonOptions() -> None:
    cli = MUT.GoCobraCLI(
        'oc',
        cmdExec=CommandExecutor(CmdRes(optionsText, '', 0, True)),
    )

    assert cli._IsCommon('n')
    assert not cli._IsCommon('x')
    assert cli._IsCommon('namespace')
    assert cli._IsCommon('kubeconfig')
    assert not cli._IsCommon('not_common')


def test_Run_BuildsCobraCommandAndOverridesDefaults() -> None:
    executor = CommandExecutor(CmdRes(optionsText, '', 0, True))
    cli = MUT.GoCobraCLI(
        'oc',
        cmdExec=executor,
        namespace='default',
        kubeconfig='/old',
        ignored='remove-me',
    )
    executor.Run.reset_mock()
    executor.Run.return_value = CmdRes('done', '', 0, True)

    result = cli.Run(
        'get pods',
        'pod-one',
        2,
        3.5,
        input='stdin',
        spPars={'timeout': 2},
        namespace='other',
        kubeconfig=None,
        ignored=None,
        n='short',
        no_headers=True,
        enabled=False,
        output='json',
    )

    assert result.success
    executor.Run.assert_called_once_with(
        [
            'oc',
            '--namespace',
            'other',
            '-n',
            'short',
            'get',
            'pods',
            '--no-headers=true',
            '--enabled=false',
            '--output',
            'json',
            'pod-one',
            '2',
            '3.5',
        ],
        input='stdin',
        spPars={'timeout': 2},
    )


@pytest.mark.parametrize('invalid', [True, object()])
def test_Run_RejectsInvalidPositionals(invalid: object) -> None:
    cli = MUT.GoCobraCLI(
        'tool',
        cmdExec=CommandExecutor(CmdRes('', '', 1, False)),
    )

    with pytest.raises(TypeError, match='positional arg'):
        cli.Run('do', invalid)


def test_Run_RejectsInvalidOption() -> None:
    cli = MUT.GoCobraCLI(
        'tool',
        cmdExec=CommandExecutor(CmdRes('', '', 1, False)),
    )

    with pytest.raises(TypeError, match="Option 'bad'"):
        cli.Run('do', bad=[])
