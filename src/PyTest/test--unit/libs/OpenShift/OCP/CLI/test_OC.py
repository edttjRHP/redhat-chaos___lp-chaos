from unittest import mock
import subprocess

import pytest

import libs.OpenShift.OCP.CLI.OC as MUT
import Utils


@pytest.mark.parametrize('isSecret', [False, True])
@pytest.mark.parametrize('asSubcommand', [False, True])
@pytest.mark.parametrize('stop', [
    'dry-run', 'apply', 'wait', 'skip-wait', 'empty-reference', 'success',
])
def test_NewRes_CurrentKeywordPreservesRawProducerConsumerPhases(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    isSecret: bool, asSubcommand: bool, stop: str,
) -> None:
    generated = 'kind: Secret\nmetadata:\n  name: synthetic-secret\n'
    calls = []

    def Run(
        command: list[str], **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        assert kwargs['text'] is True
        assert kwargs['capture_output'] is True
        assert not any('secret=' in part.lower() for part in command)
        if (command[-1] == 'options'):
            return subprocess.CompletedProcess(command, 0, '', '')
        calls.append((command, kwargs))
        phase = command[1]
        failed = phase == {
            'dry-run': 'create', 'apply': 'apply', 'wait': 'wait',
        }.get(stop)
        if (phase == 'create'): stdout = generated
        elif (phase == 'apply'):
            assert kwargs['input'] == generated
            stdout = '' if (stop == 'empty-reference') else (
                'Secret/synthetic-secret configured\n'
            )
        else:
            assert command == [
                'oc-unit', 'wait', '--for', 'create', '--timeout', '2m',
                'Secret/synthetic-secret',
            ]
            stdout = 'synthetic-wait-output'
        return subprocess.CompletedProcess(
            command, int(failed), stdout, 'synthetic-private-stderr',
        )

    monkeypatch.setattr(subprocess, 'run', Run)
    recorder = Utils.RecordingExec()
    cli = MUT.OCcli('oc-unit', cmdExec=recorder)
    recorder.inputs.clear()
    resource = {'subCmd': 'secret generic', 'args': ['synthetic-secret']}
    with caplog.at_level('DEBUG'):
        (phase, result) = cli.NewRes(
            resYAMLorSubCmd=resource if (asSubcommand) else generated,
            isSecret=isSecret,
            waitCond=None if (stop == 'skip-wait') else 'create',
        )
    expected = {
        'dry-run': (MUT.OCcli.ResPhase.DRY_RUN, 1),
        'apply': (MUT.OCcli.ResPhase.APPLIED, 2),
        'skip-wait': (MUT.OCcli.ResPhase.APPLIED, 2),
        'empty-reference': (MUT.OCcli.ResPhase.APPLIED, 2),
    }.get(stop, (MUT.OCcli.ResPhase.WAITED, 3))
    assert (phase, len(calls)) == expected
    assert result.success == (stop not in ('dry-run', 'apply', 'wait'))
    assert result.stderr == 'synthetic-private-stderr'
    assert type(result.stdout) is str
    if (len(calls) > 1): assert calls[1][1]['input'] == generated
    assert calls[0][1]['input'] == (None if (asSubcommand) else generated)
    if (isSecret):
        assert recorder.inputs == []
        for value in ('synthetic-secret', 'synthetic-private-stderr',
                      'synthetic-wait-output'):
            assert value not in caplog.text
        if (result.success): assert caplog.text == ''
        else:
            assert 'Command failed with exit code 1 (Try 1/1)' in caplog.text
    else:
        assert len(recorder.inputs) == len(calls)


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


def test_NewRes_SecretFlagReachesProducerConsumerAndWait() -> None:
    for manifest in ('kind: Secret', {'subCmd': 'secret generic'}):
        cli = BuildCLI()
        cli.Run.side_effect = [
            Result(True, 'synthetic-produced-yaml'),
            Result(True, 'Secret/name configured'),
            Result(True, 'raw-wait-result'),
        ]
        (phase, final) = cli.NewRes(manifest, True, 'create', '5m')
        assert phase == MUT.OCcli.ResPhase.WAITED
        assert final.stdout == 'raw-wait-result'
        assert len(cli.Run.call_args_list) == 3
        for call in cli.Run.call_args_list:
            assert call.kwargs['isSecret'] is True
        assert cli.Run.call_args_list[1].kwargs['input'] == (
            'synthetic-produced-yaml'
        )
        assert cli.Run.call_args_list[2].kwargs['timeout'] == '5m'


def test_NewRes_FromYAMLrunsAllPhases() -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [
        Result(True, 'generated yaml'),
        Result(True, 'VirtualMachine/vm configured\n'),
        Result(True, 'ready'),
    ]
    (phase, final) = cli.NewRes('kind: VirtualMachine', waitTime='5m')

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
            save_config=True, isSecret=False,
        ),
        mock.call('apply', '-f', '-', input='generated yaml',
                  isSecret=False),
        mock.call(
            'wait',
            'VirtualMachine/vm',
            **{'for': 'create'},
            timeout='5m', isSecret=False,
        ),
    ]


def test_NewRes_FromSubcommandMergesOptions() -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [
        Result(True, 'yaml'),
        Result(True, 'secret/name created'),
    ]

    (phase, final) = cli.NewRes(
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
            save_config=True, isSecret=False,
        ),
        mock.call('apply', '-f', '-', input='yaml', isSecret=False),
    ]


def test_NewRes_SubcommandDefaultsOptionalCollections() -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [Result(False, stderr='invalid')]

    (phase, final) = cli.NewRes({'subCmd': 'namespace'})

    assert phase == MUT.OCcli.ResPhase.DRY_RUN
    assert final.stderr == 'invalid'
    cli.Run.assert_called_once_with(
        'create namespace',
        dry_run='client',
        o='yaml',
        save_config=True, isSecret=False,
    )


def test_NewRes_StopsOnApplyFailure() -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [
        Result(True, 'yaml'),
        Result(False, stderr='apply failed'),
    ]

    (phase, final) = cli.NewRes('yaml')

    assert phase == MUT.OCcli.ResPhase.APPLIED
    assert not final.success


def test_NewRes_StopsWhenApplyHasNoResourceReference() -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [Result(True, 'yaml'), Result(True, '   ')]

    (phase, final) = cli.NewRes('yaml')

    assert phase == MUT.OCcli.ResPhase.APPLIED
    assert final.success
    assert cli.Run.call_count == 2


def test_DelRes_FailureAndSuccess() -> None:
    cli = BuildCLI()
    failure = Result(False, stderr='denied')
    cli.Run.return_value = failure

    (phase, final) = cli.DelRes('Pod/name')
    assert (phase, final) == (MUT.OCcli.ResPhase.DELETED, failure)

    cli.Run.reset_mock()
    cli.Run.side_effect = [Result(True), Result(True, 'gone')]
    (phase, final) = cli.DelRes('Pod/name', waitTime='3m')
    assert phase == MUT.OCcli.ResPhase.WAITED
    assert final.stdout == 'gone'
    assert cli.Run.call_args_list == [
        mock.call('delete', 'Pod/name', ignore_not_found=True),
        mock.call('wait', 'Pod/name', **{'for': 'delete'}, timeout='3m'),
    ]
