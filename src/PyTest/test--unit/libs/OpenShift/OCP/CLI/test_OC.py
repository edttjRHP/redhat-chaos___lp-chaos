from unittest import mock
import ast
import inspect
import json
import logging
import re
import subprocess
import textwrap

import pytest

import libs.OpenShift.OCP.CLI.OC as MUT
import Utils


@pytest.mark.parametrize(
    'name,parameters',
    [
        (
            'NewRes',
            [
                'resYAMLorSubCmd',
                'checkExist',
                'isSecret',
                'waitCond',
                'waitTime',
            ],
        ),
        ('GetResUID', ['resRef']),
        ('EnsureRes', [
            'resYAMLorSubCmd', 'owned', 'isSecret', 'existResDelTO',
        ]),
        ('CleanUpRes', ['ownRes']),
        ('CleanUpVMs', ['vmList', 'ownVMs']),
    ],
)
def test_ResourceAPI_DocstringsRetainContractsAnd79Columns(
    name: str, parameters: list[str]
) -> None:
    from libs.OpenShift.LP.Virt.VM.VM import VM
    method = getattr(VM if (name == 'CleanUpVMs') else MUT.OCcli, name)
    source = inspect.getsource(method)
    node = ast.parse(textwrap.dedent(source)).body[0]
    doc = inspect.getdoc(method)
    assert ([
        parameter for parameter in inspect.signature(method).parameters
        if (parameter != 'self')
    ] == parameters)
    for parameter in parameters: assert (f':param {parameter}:' in doc)
    if (inspect.signature(method).return_annotation is not None):
        assert (':return:' in doc)
    assert (':raises ' in doc)
    assert all(
        (len(line) <= 79)
        for line in source.splitlines()[
            node.body[0].lineno - 1 : node.body[0].end_lineno
        ]
    )
    if (name == 'CleanUpVMs'):
        assert ('wrappers for reused VMs' in doc)
        assert (('not\natomic' in doc) or ('not atomic' in doc))
        assert ('OCcli.CleanUpRes' in doc)
    for statement in ast.walk(node):
        if (isinstance(statement, (ast.If, ast.While)) and isinstance(
            statement.test, ast.UnaryOp
        )):
            line = textwrap.dedent(source).splitlines()[statement.lineno - 1]
            assert not line.lstrip().startswith(
                ('if (not ', 'elif (not ', 'while (not ')
            )


@pytest.mark.parametrize('primary', [False, True])
def test_CleanUpRes_LogsEachOutcomeBeforeNextResourceAndPreservesPrimary(
    caplog: pytest.LogCaptureFixture, primary: bool,
) -> None:
    cli = BuildCLI()
    observed = []
    names = [
        'lookup-failed',
        'delete-failed',
        'exception',
        'absent',
        'changed',
        'success',
    ]
    def Run(command: str, ref: str, **kwargs: object) -> MUT.CmdRes:
        observed.append(
            (command, ref, [record.getMessage() for record in caplog.records])
        )
        name = ref.split('/')[-1]
        if (command == 'get'):
            if (name == 'exception'):
                raise RuntimeError('metadata transport failed')
            uid = {'absent': '', 'changed': 'replacement'}.get(name, 'owned')
            return Result(
                (name != 'lookup-failed'), uid, 'metadata lookup denied'
            )
        return Result((name != 'delete-failed'), '', 'metadata delete denied')
    cli.Run.side_effect = Run
    records = [(cli, f'Pod/{name}', 'owned') for name in names]
    original = RuntimeError('original test failure')
    with caplog.at_level(logging.INFO, logger=MUT.__name__):
        if primary:
            try: raise original
            except RuntimeError as error:
                MUT.OCcli.CleanUpRes(records)
                assert (MUT.sys.exc_info()[1] is error is original)
        else:
            with pytest.raises(
                RuntimeError, match='Resource cleanup failed'
            ) as caught:
                MUT.OCcli.CleanUpRes(records)
            assert all((name in str(caught.value)) for name in names[:3])
    expected = [
        'verify ownership',
        'at `DELETED`',
        'Could not clean up resource',
        'already absent',
        'UID changed',
        'Cleaned up resource',
    ]
    assert ([record.levelname for record in caplog.records[:6]] == [
        'ERROR', 'ERROR', 'ERROR', 'INFO', 'WARNING', 'INFO',
    ])
    assert all(
        (part in record.getMessage())
        for (part, record) in zip(expected, caplog.records)
    )
    for (previous, current) in zip(names, names[1:]):
        messages = next(messages for (command, ref, messages) in observed
                        if ((command == 'get') and (ref == f'Pod/{current}')))
        assert any((f'Pod/{previous}' in message) for message in messages)
    assert (caplog.records[2].exc_info[1].args
        == ('metadata transport failed',))


@pytest.mark.parametrize('success', [False, True])
def test_GetResUID_PreservesLookupContract(success: bool) -> None:
    cli = BuildCLI()
    cli.Run.return_value = MUT.CmdRes(' uid-1\n', ' warning\n', 0, success)
    assert (cli.GetResUID('Pod/example') == (success, 'uid-1', 'warning'))
    cli.Run.assert_called_once_with(
        'get',
        'Pod/example',
        o='jsonpath={.metadata.uid}',
        ignore_not_found=True,
    )


@pytest.mark.parametrize('primary', [False, True])
@pytest.mark.parametrize(
    'outcome',
    [
        'absent',
        'changed',
        'lookup-failed',
        'delete-failed',
        'exception',
        'success',
    ],
)
def test_CleanUpRes_ChecksOwnershipContinuesAndPreservesPrimary(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    primary: bool, outcome: str,
) -> None:
    cli = mock.create_autospec(MUT.OCcli, instance=True)
    later = mock.create_autospec(MUT.OCcli, instance=True)
    later.GetResUID.return_value = (True, '', '')
    cli.GetResUID.return_value = (
        (outcome != 'lookup-failed'),
        {'absent': '', 'changed': 'other'}.get(outcome, 'owned'),
        'synthetic-sensitive',
    )
    if (outcome == 'exception'):
        cli.GetResUID.side_effect = RuntimeError('synthetic-sensitive')
    cli.DelRes.return_value = (
        MUT.OCcli.ResPhase.WAITED,
        Result((outcome != 'delete-failed'), stderr='synthetic-sensitive'),
    )
    if primary:
        monkeypatch.setattr(
            MUT.sys,
            'exc_info',
            lambda: (RuntimeError, RuntimeError('primary'), None),
        )
    records = [
        (cli, 'secret/example', 'owned'),
        (later, 'secret/later', 'owned'),
    ]
    failed = outcome in ('lookup-failed', 'delete-failed', 'exception')
    if (failed and not primary):
        with pytest.raises(
            RuntimeError, match='Resource cleanup failed'
        ) as caught:
            MUT.OCcli.CleanUpRes(records)
        assert ('synthetic-sensitive' in str(caught.value))
    else:
        MUT.OCcli.CleanUpRes(records)
    later.GetResUID.assert_called_once_with('secret/later')
    if (outcome in ('success', 'delete-failed')):
        cli.DelRes.assert_called_once_with('secret/example')
    else:
        cli.DelRes.assert_not_called()
    if (failed and primary):
        assert ('preserving primary exception' in caplog.text)


@pytest.mark.parametrize('isSecret', [False, True])
@pytest.mark.parametrize('asSubcommand', [False, True])
@pytest.mark.parametrize(
    'stop',
    [
        'dry-run',
        'lookup-failed',
        'existed',
        'absent',
        'apply',
        'wait',
        'skip-wait',
        'success',
    ],
)
def test_NewRes_UniformContractPreservesCommandsAndResourceAcrossPhases(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    isSecret: bool, asSubcommand: bool, stop: str,
) -> None:
    resource = {
        'kind': 'Secret',
        'metadata': {'name': 'synthetic-secret', 'uid': 'uid-1'},
    }
    generated = json.dumps(resource)
    calls = []
    checkExist = stop in ('lookup-failed', 'existed', 'absent')

    def Run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert (kwargs['text'] is True)
        assert (kwargs['capture_output'] is True)
        assert not any(('secret=' in part.lower()) for part in command)
        if (command[-1] == 'options'):
            return subprocess.CompletedProcess(command, 0, '', '')
        calls.append((command, kwargs))
        subcommand = command[1]
        failed = (subcommand == {'dry-run': 'create', 'lookup-failed': 'get',
                                'apply': 'apply', 'wait': 'wait'}.get(stop))
        stdout = generated
        if ((subcommand == 'get') and (stop == 'absent')): stdout = ''
        if (subcommand == 'wait'):
            assert (command == [
                'oc-unit',
                'wait',
                '--for=create',
                '--timeout=2m',
                'Secret/synthetic-secret',
            ])
            stdout = 'synthetic-wait-output'
        if (subcommand in ('get', 'apply')):
            assert (kwargs['input'] == generated)
        return subprocess.CompletedProcess(
            command, int(failed), stdout, 'synthetic-private-stderr'
        )

    monkeypatch.setattr(subprocess, 'run', Run)
    recorder = Utils.RecordingExec()
    cli = MUT.OCcli('oc-unit', cmdExec=recorder)
    recorder.inputs.clear()
    specification = {'subCmd': 'secret generic', 'args': ['synthetic-secret']}
    with caplog.at_level('DEBUG'):
        (phase, result, actual) = cli.NewRes(
            specification if asSubcommand else generated,
            isSecret=isSecret, checkExist=checkExist,
            waitCond=None if (stop == 'skip-wait') else 'create',
        )
    expected = {
        'dry-run': (MUT.OCcli.ResPhase.DRY_RUN, 1),
        'lookup-failed': (MUT.OCcli.ResPhase.EXISTED, 2),
        'existed': (MUT.OCcli.ResPhase.EXISTED, 2),
        'apply': (MUT.OCcli.ResPhase.APPLIED, 2),
        'skip-wait': (MUT.OCcli.ResPhase.APPLIED, 2),
        'absent': (MUT.OCcli.ResPhase.WAITED, 4),
    }.get(stop, (MUT.OCcli.ResPhase.WAITED, 3))
    assert ((phase, len(calls)) == expected)
    assert (result.success == (
        stop not in ('dry-run', 'lookup-failed', 'apply', 'wait')
    ))
    assert (result.exitCode == (0 if result.success else 1))
    assert (result.stderr == 'synthetic-private-stderr')
    assert (actual == (
        None if (stop in ('dry-run', 'lookup-failed', 'apply')) else resource
    ))
    assert (any((command[1] == 'get') for (command, _) in calls)
        is (checkExist))
    if (stop in ('lookup-failed', 'existed')):
        assert not any((command[1] == 'apply') for (command, _) in calls)
    if isSecret:
        assert (recorder.inputs == [])
        for value in (
            'synthetic-secret',
            'synthetic-private-stderr',
            'synthetic-wait-output',
        ):
            assert (value not in caplog.text)
        if result.success: assert (caplog.text == '')
        else: assert ('Command failed with exit code 1 (Try 1/1)'
            in caplog.text)
    else:
        assert (len(recorder.inputs) == len(calls))


@pytest.mark.parametrize('lookup', [False, True])
@pytest.mark.parametrize('payload', ['not-json', 'null', '[]', '{}'])
def test_NewRes_InvalidResourceJSONRaisesWithoutResponseData(
    lookup: bool, payload: str
) -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [Result(True, 'normalized'), Result(True, payload)]
    with pytest.raises(ValueError) as caught:
        cli.NewRes('manifest', checkExist=lookup)
    assert (str(caught.value) == ((
        '`NewRes()`: Invalid resource Live Object State at '
    ) + (
        'EXISTED.' if lookup else 'APPLIED.'
    )))
    assert (cli.Run.call_count == 2)


def test_ResPhase_ExistedFollowsDryRun() -> None:
    assert ([(phase.name, phase.value) for phase in MUT.OCcli.ResPhase] == [
        ('DRY_RUN', 0),
        ('EXISTED', 1),
        ('APPLIED', 2),
        ('DELETED', 3),
        ('WAITED', 4),
    ])


@pytest.mark.parametrize('kind', ['ConfigMap', 'ServiceAccount'])
@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('isSecret', [False, True])
def test_EnsureRes_ActualNewResReturnsObjectAndTracksOnlyCreation(
    kind: str, existing: bool, isSecret: bool,
) -> None:
    cli = BuildCLI()
    resource = {
        'kind': kind, 'metadata': {'name': 'example', 'uid': 'created-uid'},
    }
    manifest = json.dumps(resource)
    cli.Run = mock.Mock(side_effect=[
        Result(True, manifest),
        Result(True, manifest if existing else ''),
        Result(True, manifest),
    ])
    owned = []
    actual = cli.EnsureRes(manifest, owned, isSecret=isSecret)
    assert (actual == resource)
    assert (owned == (
        [] if existing else [(cli, f'{kind}/example', 'created-uid')]
    ))
    assert ([call.args[0] for call in cli.Run.call_args_list] == (
        ['create', 'get'] if existing else ['create', 'get', 'apply']
    ))
    assert all((call.kwargs['isSecret'] == isSecret)
               for call in cli.Run.call_args_list)


@pytest.mark.parametrize('phase', ['DRY_RUN', 'EXISTED', 'APPLIED'])
def test_EnsureRes_FailurePrecedesResourceAccess(phase: str) -> None:
    cli = BuildCLI()
    resource = mock.MagicMock()
    cli.NewRes = mock.Mock(return_value=(
        cli.ResPhase[phase], Result(False, 'private-output'), resource,
    ))
    owned = []
    specification = {'subCmd': 'configmap', 'args': ['example']}
    message = f'`EnsureRes()`: Failed at `{phase}`.'
    with pytest.raises(RuntimeError, match=re.escape(message)):
        cli.EnsureRes(specification, owned)
    cli.NewRes.assert_called_once_with(
        specification, checkExist=True, waitCond=None, isSecret=False,
    )
    assert (not resource.mock_calls and not owned)


@pytest.mark.parametrize('lookup', [False, True])
def test_EnsureRes_InvalidJSONRetainsSanitizedPhase(lookup: bool) -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [
        Result(True, 'normalized'),
        Result(True, 'private-invalid-json' if lookup else ''),
        Result(True, 'private-invalid-json'),
    ]
    owned = []
    with pytest.raises(ValueError) as caught:
        cli.EnsureRes('manifest', owned, isSecret=True)
    phase = cli.ResPhase.EXISTED if lookup else cli.ResPhase.APPLIED
    assert (str(caught.value) == (
        f'`NewRes()`: Invalid resource Live Object State at {phase.name}.'
    ))
    assert not owned


@pytest.mark.parametrize('resource', [
    None, [], {}, {'kind': 'ConfigMap'},
    {'kind': 'ConfigMap', 'metadata': None},
    {'kind': 'ConfigMap', 'metadata': {'name': 'example', 'uid': ''}},
    {'kind': 1, 'metadata': {'name': 'example', 'uid': 'uid'}},
])
def test_EnsureRes_RejectsMalformedCreatedResource(resource: object) -> None:
    cli = BuildCLI()
    cli.NewRes = mock.Mock(return_value=(
        cli.ResPhase.APPLIED, Result(True), resource,
    ))
    owned = []
    detail = 'identity' if resource else 'resource'
    message = f'`EnsureRes()`: Invalid {detail} at `APPLIED`.'
    with pytest.raises(RuntimeError, match=re.escape(message)):
        cli.EnsureRes('manifest', owned)
    assert not owned


@pytest.mark.parametrize('timeout', [None, '17s'])
@pytest.mark.parametrize('outcome', [
    'gone', 'failed', 'exception', 'retry', 'still-deleting',
])
def test_EnsureRes_DeletionWaitThenReensure(
    timeout: str | None, outcome: str,
) -> None:
    cli = BuildCLI()
    deleting = {
        'kind': 'ConfigMap',
        'metadata': {'name': 'example', 'uid': 'old',
                     'deletionTimestamp': 'now'},
    }
    created = {'kind': 'ConfigMap',
               'metadata': {'name': 'example', 'uid': 'new'}}
    manifest = json.dumps(created)
    responses = [Result(True, manifest), Result(True, json.dumps(deleting))]
    if (outcome == 'exception'):
        responses.append(subprocess.TimeoutExpired('synthetic', 17))
    else:
        responses.append(Result((outcome != 'failed')))
    responses.extend([
        Result(True, manifest),
        Result((outcome != 'retry'), json.dumps(deleting)
               if (outcome == 'still-deleting') else ''),
        Result(True, manifest),
    ])
    cli.Run.side_effect = responses
    owned = []
    options = {} if (timeout is None) else {'existResDelTO': timeout}
    if (outcome == 'gone'):
        assert (cli.EnsureRes(manifest, owned, **options) == created)
        assert (owned == [(cli, 'ConfigMap/example', 'new')])
        assert ([call.args[0] for call in cli.Run.call_args_list] == [
            'create', 'get', 'wait', 'create', 'get', 'apply',
        ])
    else:
        match = 'EXISTED' if (outcome == 'retry') else (
            'ConfigMap/example.*' + (timeout or '5m'))
        with pytest.raises(RuntimeError, match=match):
            cli.EnsureRes(manifest, owned, **options)
        assert (owned == [])
    assert (cli.Run.call_args_list[2] == mock.call(
        'wait', 'ConfigMap/example', **{'for': 'delete'},
        timeout=(timeout or '5m'), isSecret=False,
    ))
    assert not any((call.args[0] == 'delete')
                   for call in cli.Run.call_args_list)
    if (outcome == 'still-deleting'):
        assert ([call.args[0] for call in cli.Run.call_args_list] == [
            'create', 'get', 'wait', 'create', 'get',
        ])


@pytest.mark.parametrize('timestamp', [None, 'now'])
def test_EnsureRes_NullDeletionTimestampAndInvalidDeletingIdentity(
    timestamp: str | None,
) -> None:
    cli = BuildCLI()
    resource = {'metadata': {'deletionTimestamp': timestamp}}
    cli.NewRes = mock.Mock(return_value=(
        cli.ResPhase.EXISTED, Result(True), resource,
    ))
    owned = []
    if (timestamp is None):
        assert (cli.EnsureRes('manifest', owned) is resource)
    else:
        message = '`EnsureRes()`: Invalid identity at `EXISTED`.'
        with pytest.raises(RuntimeError, match=re.escape(message)):
            cli.EnsureRes('manifest', owned)
    assert not owned
    cli.Run.assert_not_called()


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
            Result(
                True,
                json.dumps(
                    {
                        'kind': 'Secret',
                        'metadata': {'name': 'name', 'uid': 'uid'},
                    }
                ),
            ),
            Result(True, 'raw-wait-result'),
        ]
        (phase, final, resource) = cli.NewRes(
            manifest, isSecret=True, waitCond='create', waitTime='5m',
        )
        assert (phase == MUT.OCcli.ResPhase.WAITED)
        assert (final.stdout == 'raw-wait-result')
        assert (len(cli.Run.call_args_list) == 3)
        for call in cli.Run.call_args_list:
            assert (call.kwargs['isSecret'] is True)
        assert (cli.Run.call_args_list[1].kwargs['input'] == (
            'synthetic-produced-yaml'
        ))
        assert (cli.Run.call_args_list[2].kwargs['timeout'] == '5m')


def test_NewRes_FromYAMLrunsAllPhases() -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [
        Result(True, 'generated yaml'),
        Result(
            True,
            '{"kind":"VirtualMachine","metadata":{"name":"vm","uid":"uid"}}',
        ),
        Result(True, 'ready'),
    ]
    (phase, final, resource) = cli.NewRes(
        'kind: VirtualMachine', waitTime='5m'
    )

    assert (phase == MUT.OCcli.ResPhase.WAITED)
    assert (final.stdout == 'ready')
    assert (cli.Run.call_args_list == [
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
                  o='json', isSecret=False),
        mock.call(
            'wait',
            'VirtualMachine/vm',
            **{'for': 'create'},
            timeout='5m', isSecret=False,
        ),
    ])


def test_NewRes_FromSubcommandMergesOptions() -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [
        Result(True, 'yaml'),
        Result(
            True, '{"kind":"Secret","metadata":{"name":"name","uid":"uid"}}'
        ),
    ]

    (phase, final, resource) = cli.NewRes(
        {
            'subCmd': 'secret generic',
            'args': ['name'],
            'opts': {'from_literal': 'key=value'},
        },
        waitCond=None,
    )

    assert (phase == MUT.OCcli.ResPhase.APPLIED)
    assert final.success
    assert (cli.Run.call_args_list == [
        mock.call(
            'create secret generic',
            'name',
            from_literal='key=value',
            dry_run='client',
            o='yaml',
            save_config=True, isSecret=False,
        ),
        mock.call('apply', '-f', '-', input='yaml', o='json', isSecret=False),
    ])


def test_NewRes_SubcommandDefaultsOptionalCollections() -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [Result(False, stderr='invalid')]

    (phase, final, resource) = cli.NewRes({'subCmd': 'namespace'})

    assert (phase == MUT.OCcli.ResPhase.DRY_RUN)
    assert (final.stderr == 'invalid')
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

    (phase, final, resource) = cli.NewRes('yaml')

    assert (phase == MUT.OCcli.ResPhase.APPLIED)
    assert not final.success


def test_NewRes_MissingWaitIdentityRaisesExplicitError() -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [
        Result(True, 'normalized'),
        Result(True, '{"metadata":{"uid":"uid"}}'),
    ]
    with pytest.raises(ValueError, match='no wait resource identity'):
        cli.NewRes('manifest')
    assert (cli.Run.call_count == 2)
    cli.Run.reset_mock()
    cli.Run.side_effect = [
        Result(True, 'normalized'),
        Result(True, '{"metadata":{"uid":"uid"}}'),
    ]
    (phase, result, resource) = cli.NewRes('manifest', waitCond=None)
    assert ((phase == MUT.OCcli.ResPhase.APPLIED) and result.success)
    assert (resource == {'metadata': {'uid': 'uid'}})
    assert (cli.Run.call_count == 2)


def test_DelRes_FailureAndSuccess() -> None:
    cli = BuildCLI()
    failure = Result(False, stderr='denied')
    cli.Run.return_value = failure

    (phase, final) = cli.DelRes('Pod/name')
    assert ((phase, final) == (MUT.OCcli.ResPhase.DELETED, failure))

    cli.Run.reset_mock()
    cli.Run.side_effect = [Result(True), Result(True, 'gone')]
    (phase, final) = cli.DelRes('Pod/name', waitTime='3m')
    assert (phase == MUT.OCcli.ResPhase.WAITED)
    assert (final.stdout == 'gone')
    assert (cli.Run.call_args_list == [
        mock.call('delete', 'Pod/name', ignore_not_found=True),
        mock.call('wait', 'Pod/name', **{'for': 'delete'}, timeout='3m'),
    ])
