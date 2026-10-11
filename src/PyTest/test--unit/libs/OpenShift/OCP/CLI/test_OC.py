from unittest import mock

import pytest

import libs.OpenShift.OCP.CLI.OC as MUT
import Utils


def Result(success: bool, stdout: str = '', stderr: str = '') -> MUT.CmdRes:
    return MUT.CmdRes(stdout, stderr, 0 if success else 1, success)


def BuildCLI() -> MUT.OCcli:
    cli = object.__new__(MUT.OCcli)
    cli.Run = mock.create_autospec(cli.Run)
    return cli


def test_Init_DelegatesToBase(monkeypatch: pytest.MonkeyPatch) -> None:
    Utils.AutoPatch(monkeypatch, MUT.GoCobraCLI, '__init__')
    MUT.GoCobraCLI.__init__.return_value = None
    executor = mock.MagicMock()

    MUT.OCcli(binExec='custom-oc', cmdExec=executor, namespace='ns')

    MUT.GoCobraCLI.__init__.assert_called_once_with(
        mock.ANY,
        'custom-oc',
        cmdExec=executor,
        namespace='ns',
    )


def test_NewRes_FromYAMLrunsAllPhases() -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [
        Result(True, 'generated yaml'),
        Result(True, 'VirtualMachine/vm configured\n'),
        Result(True, 'ready'),
    ]

    phase, final = cli.NewRes('kind: VirtualMachine', waitTime='5m')

    assert phase == MUT.OCcli.ResPhase.WAITED
    assert final.stdout == 'ready'
    assert cli.Run.call_args_list == [
        mock.call(
            'create',
            '-f',
            '-',
            input='kind: VirtualMachine',
            dry_run='client',
            o='yaml',
            save_config=True,
        ),
        mock.call('apply', '-f', '-', input='generated yaml'),
        mock.call(
            'wait',
            'VirtualMachine/vm',
            **{'for': 'create'},
            timeout='5m',
        ),
    ]


def test_NewRes_FromSubcommandMergesOptions() -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [
        Result(True, 'yaml'),
        Result(True, 'secret/name created'),
    ]

    phase, final = cli.NewRes(
        {
            'subCmd': 'secret generic',
            'args': ['name'],
            'opts': {'from_literal': 'key=value'},
        },
        waitCond=None,
    )

    assert phase == MUT.OCcli.ResPhase.APPLIED
    assert final.success
    assert cli.Run.call_args_list == [
        mock.call(
            'create secret generic',
            'name',
            from_literal='key=value',
            dry_run='client',
            o='yaml',
            save_config=True,
        ),
        mock.call('apply', '-f', '-', input='yaml'),
    ]


def test_NewRes_SubcommandDefaultsOptionalCollections() -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [Result(False, stderr='invalid')]

    phase, final = cli.NewRes({'subCmd': 'namespace'})

    assert phase == MUT.OCcli.ResPhase.DRY_RUN
    assert final.stderr == 'invalid'
    cli.Run.assert_called_once_with(
        'create namespace',
        dry_run='client',
        o='yaml',
        save_config=True,
    )


def test_NewRes_StopsOnApplyFailure() -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [
        Result(True, 'yaml'),
        Result(False, stderr='apply failed'),
    ]

    phase, final = cli.NewRes('yaml')

    assert phase == MUT.OCcli.ResPhase.APPLIED
    assert not final.success


def test_NewRes_StopsWhenApplyHasNoResourceReference() -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [Result(True, 'yaml'), Result(True, '   ')]

    phase, final = cli.NewRes('yaml')

    assert phase == MUT.OCcli.ResPhase.APPLIED
    assert final.success
    assert cli.Run.call_count == 2


def test_DelRes_FailureAndSuccess() -> None:
    cli = BuildCLI()
    failure = Result(False, stderr='denied')
    cli.Run.return_value = failure

    phase, final = cli.DelRes('Pod/name')
    assert (phase, final) == (MUT.OCcli.ResPhase.DELETED, failure)

    cli.Run.reset_mock()
    cli.Run.side_effect = [Result(True), Result(True, 'gone')]
    phase, final = cli.DelRes('Pod/name', waitTime='3m')
    assert phase == MUT.OCcli.ResPhase.WAITED
    assert final.stdout == 'gone'
    assert cli.Run.call_args_list == [
        mock.call('delete', 'Pod/name', ignore_not_found=True),
        mock.call('wait', 'Pod/name', **{'for': 'delete'}, timeout='3m'),
    ]
