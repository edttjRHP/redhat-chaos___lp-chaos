from types import SimpleNamespace
from unittest import mock

import inspect
import json
import logging
import re
import subprocess

import pytest

import libs.OpenShift.OCP.CLI.OC as MUT


@pytest.fixture(name='jsonLoads', autouse=True)
def FxtJSONLoads(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    jsonLoads = mock.Mock(return_value=None)
    monkeypatch.setattr(MUT, 'json', mock.Mock(loads=jsonLoads))
    return jsonLoads


@pytest.fixture(autouse=True)
def PatchRuntimeState(monkeypatch: pytest.MonkeyPatch) -> None:
    '''Stub OC cleanup's exception-state query at its module boundary.'''
    excInfo = mock.Mock(return_value=(None, None, None))
    monkeypatch.setattr(MUT, 'sys', SimpleNamespace(exc_info=excInfo))


@pytest.mark.parametrize('outer,inner', [
    (True, False), (False, True), (True, True), (False, False),
])
def test_NewRes_SecretCannotBeDowngraded_ReviewRegression(
    outer: bool,
    inner: bool,
) -> None:
    cli = BuildCLI()
    response = '{"kind":"Secret","metadata":{"name":"synthetic"}}'
    MUT.json.loads.return_value = {
        'kind': 'Secret', 'metadata': {'name': 'synthetic'},
    }
    cli.Run.side_effect = [
        Result(True, '{}'), Result(True, ''), Result(True, response),
    ]
    cli.NewRes({
        'subCmd': 'secret generic',
        'args': ['synthetic'],
        'opts': {'isSecret': inner},
    }, isSecret=outer, checkExist=True, waitCond=None)
    assert (len(cli.Run.call_args_list) == 3)
    assert all(
        (call.kwargs['isSecret'] is outer)
        for call in cli.Run.call_args_list
    )
    MUT.json.loads.assert_called_once_with(response)


@pytest.mark.parametrize('phase', ['APPLIED', 'WAITED'])
@pytest.mark.parametrize('cleanup', ['absent', 'failed', 'exception'])
@pytest.mark.parametrize('native', [False, True])
def test_EnsureRes_FailedCreationCleansValidatedTargetAndRaisesOriginal(
    phase: str,
    cleanup: str,
    native: bool,
) -> None:
    cli = BuildCLI()
    resource = {
        'kind': 'ConfigMap', 'metadata': {'name': 'example', 'uid': 'owned'},
    }
    manifest = json.dumps(resource)
    MUT.json.loads.return_value = resource
    specification = (
        {'subCmd': 'configmap', 'args': ['example']} if native else manifest
    )
    responses = [Result(True, manifest), Result(True)]
    if (phase == 'WAITED'): responses.append(Result(True, manifest))
    responses.append(Result(False, 'original-private-failure'))
    if (phase == 'WAITED'):
        responses.append(Result(
            True, '' if (cleanup == 'absent') else 'owned',
        ))
        if (cleanup != 'absent'):
            responses.append(
                RuntimeError('cleanup-private-error')
                if (cleanup == 'exception') else Result(False)
            )
    cli.Run.side_effect = responses
    owned = []
    message = f'`EnsureRes()`: Failed at `{phase}`.'
    with pytest.raises(RuntimeError, match=re.escape(message)) as caught:
        cli.EnsureRes(specification, owned, isSecret=True)
    assert (str(caught.value) == message)
    assert (owned == [])
    if ((phase == 'WAITED') and (cleanup != 'absent')):
        assert (cli.Run.call_args_list[-1] == mock.call(
            'delete', 'ConfigMap/example', isSecret=True,
            spPars={'timeout': 30}, force=True, grace_period=0,
            ignore_not_found=True, wait=False,
        ))
    commands = [call.args[0] for call in cli.Run.call_args_list]
    assert (commands.count('apply') == 1)
    assert (commands.count('delete') == int(
        ((phase == 'WAITED') and (cleanup != 'absent'))
    ))
    assert all(call.kwargs['isSecret'] for call in cli.Run.call_args_list)
    assert (commands.count('wait') == (1 if (phase == 'WAITED') else 0))


@pytest.mark.parametrize('phase', ['apply', 'wait'])
@pytest.mark.parametrize('cleanupRaises', [False, True])
def test_NewRes_CleanupNeverReplacesCreationException(
    phase: str,
    cleanupRaises: bool,
) -> None:
    cli = BuildCLI()
    resource = {
        'kind': 'ConfigMap', 'metadata': {'name': 'example', 'uid': 'owned'},
    }
    manifest = json.dumps(resource)
    MUT.json.loads.return_value = resource
    primary = RuntimeError('original creation error')
    responses = [Result(True, manifest), Result(True)]
    if (phase == 'wait'): responses.append(Result(True, manifest))
    responses.append(primary)
    if (phase == 'wait'):
        responses.extend([
            Result(True, 'owned'),
            RuntimeError('cleanup error') if cleanupRaises else Result(False),
        ])
    cli.Run.side_effect = responses
    with pytest.raises(RuntimeError) as caught:
        cli.NewRes(manifest, checkExist=True)
    assert (caught.value is primary)
    assert (cli.Run.call_args.args == (
        ('delete', 'ConfigMap/example') if (phase == 'wait')
        else ('apply', '-f', '-')
    ))


@pytest.mark.parametrize('phase', ['DRY_RUN', 'EXISTED'])
def test_NewRes_LookupFailuresNeverDelete(phase: str) -> None:
    cli = BuildCLI()
    responses = []
    if (phase == 'EXISTED'): responses.append(Result(True, 'validated'))
    responses.append(Result(False))
    cli.Run.side_effect = responses
    (actualPhase, result, resource) = cli.NewRes('input', checkExist=True)
    assert (actualPhase == cli.ResPhase[phase])
    assert not result.success
    assert (resource is None)
    assert not any(
        (call.args[0] == 'delete') for call in cli.Run.call_args_list
    )


@pytest.mark.parametrize('options', [
    None, {}, {'waitCond': None},
    {'waitCond': 'condition=Ready', 'waitTime': '17s'},
])
def test_EnsureRes_DefaultWaitAndCallerOptionsAreForwarded(
    options: dict | None,
) -> None:
    cli = BuildCLI()
    resource = {
        'kind': 'ConfigMap', 'metadata': {'name': 'example', 'uid': 'uid'},
    }
    manifest = json.dumps(resource)
    MUT.json.loads.return_value = resource
    cli.Run.side_effect = [
        Result(True, manifest), Result(True), Result(True, manifest),
        Result(True),
    ]
    supplied = dict(options or {}) | {
        'resYAMLorSubCmd': 'wrong', 'checkExist': False, 'isSecret': True,
    }
    snapshot = dict(supplied)
    assert (cli.EnsureRes(manifest, [], optsNewRes=supplied) == resource)
    assert (supplied == snapshot)
    assert (cli.Run.call_args_list[1].args[0] == 'get')
    if ((options is not None) and (options.get('waitCond', '') is None)):
        assert (cli.Run.call_args.args[0] == 'apply')
    else:
        assert (cli.Run.call_args == mock.call(
            'wait', 'ConfigMap/example', isSecret=False,
            **{'for': (options or {}).get('waitCond', 'create')},
            timeout=(options or {}).get('waitTime', '2m'),
        ))


@pytest.mark.parametrize(
    'name,parameters',
    [
        ('NewRes', [
            'resYAMLorSubCmd', 'checkExist', 'isSecret', 'waitCond',
            'waitTime',
        ]),
        ('GetResUID', ['resRef']),
        ('EnsureRes', [
            'resYAMLorSubCmd', 'owned', 'isSecret', 'existResDelTO',
            'optsNewRes',
        ]),
        ('CleanUpRes', ['ownRes', 'optsDelRes']),
        ('DelRes', ['resRef', 'waitTime']),
    ],
)
def test_OCcli_ResourceMethodsDocumentContracts(
    name: str,
    parameters: list[str],
) -> None:
    method = getattr(MUT.OCcli, name)
    doc = inspect.getdoc(method)
    assert ([
        parameter for parameter in inspect.signature(method).parameters
        if (parameter != 'self')
    ] == parameters)
    for parameter in parameters: assert (f':param {parameter}:' in doc)
    if (inspect.signature(method).return_annotation is not None):
        assert (':return:' in doc)
    assert all((len(line) <= 79) for line in method.__doc__.splitlines())


@pytest.mark.parametrize('primary', [False, True])
def test_CleanUpRes_LogsEachOutcomeBeforeNextResourceAndPreservesPrimary(
    caplog: pytest.LogCaptureFixture,
    primary: bool,
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
    def Run(command: str, ref: str, **kwargs: object) -> mock.Mock:
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
    if primary: MUT.sys.exc_info.return_value = (RuntimeError, original, None)
    with caplog.at_level(logging.INFO, logger=MUT.__name__):
        if primary:
            try: raise original
            except RuntimeError as error:
                MUT.OCcli.CleanUpRes(records)
                assert (error is original)
        else:
            with pytest.raises(
                RuntimeError, match='Resource cleanup failed'
            ) as caught:
                MUT.OCcli.CleanUpRes(records)
            assert all((name in str(caught.value)) for name in names[:3])
    MUT.sys.exc_info.assert_called_once_with()
    expected = [
        'verify ownership',
        'at `WAITED`',
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
    assert (
        caplog.records[2].exc_info[1].args == ('metadata transport failed',)
    )


@pytest.mark.parametrize('success', [False, True])
def test_GetResUID_PreservesLookupContract(success: bool) -> None:
    cli = BuildCLI()
    cli.Run.return_value = Result(success, ' uid-1\n', ' warning\n')
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
@pytest.mark.parametrize('options', [None, {'waitTime': '5m'}])
def test_CleanUpRes_ChecksOwnershipContinuesAndPreservesPrimary(
    caplog: pytest.LogCaptureFixture,
    primary: bool,
    outcome: str,
    options: dict | None,
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
        MUT.sys.exc_info.return_value = (
            RuntimeError, RuntimeError('primary'), None,
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
            MUT.OCcli.CleanUpRes(records, optsDelRes=options)
        assert ('synthetic-sensitive' in str(caught.value))
    else: MUT.OCcli.CleanUpRes(records, optsDelRes=options)
    later.GetResUID.assert_called_once_with('secret/later')
    if (outcome in ('success', 'delete-failed')):
        cli.DelRes.assert_called_once_with('secret/example', **(options or {}))
    else: cli.DelRes.assert_not_called()
    if (failed and primary):
        assert ('preserving primary exception' in caplog.text)


@pytest.mark.parametrize('isSecret', [False, True])
@pytest.mark.parametrize('asSubcommand', [False, True])
@pytest.mark.parametrize('stop', [
    'dry-run', 'lookup-failed', 'existed', 'absent', 'apply', 'wait',
    'skip-wait', 'success',
])
def test_NewRes_UniformContractUsesMockedOCBoundary(
    isSecret: bool,
    asSubcommand: bool,
    stop: str,
) -> None:
    cli = BuildCLI()
    resource = {
        'kind': 'Secret',
        'metadata': {'name': 'synthetic-secret', 'uid': 'uid-1'},
    }
    MUT.json.loads.return_value = resource
    specification = (
        {
            'subCmd': 'secret generic',
            'args': ['synthetic-secret'],
            'opts': {},
        }
        if asSubcommand else 'kind: Secret'
    )
    dryRun = Result((stop != 'dry-run'), 'normalized')
    checkExist = stop in ('lookup-failed', 'existed', 'absent')
    lookupResult = {
        'lookup-failed': Result(False, stderr='synthetic lookup failure'),
        'existed': Result(True, 'existing-json'),
        'absent': Result(True, ''),
    }.get(stop)
    applyResult = Result(
        (stop != 'apply'), 'apply-json', 'synthetic-private-stderr',
    )
    waitResult = Result((stop != 'wait'), 'wait-output')
    responses = [dryRun]
    if checkExist: responses.append(lookupResult)
    if (stop in ('absent', 'apply', 'wait', 'skip-wait', 'success')):
        responses.append(applyResult)
    if (stop in ('absent', 'wait', 'success')):
        responses.append(waitResult)
    cli.Run.side_effect = responses
    waitCond = None if (stop == 'skip-wait') else 'create'

    (phase, result, actual) = cli.NewRes(
        specification,
        checkExist=checkExist,
        isSecret=isSecret,
        waitCond=waitCond,
    )

    expectedCalls = (
        [mock.call(
            'create secret generic',
            'synthetic-secret',
            dry_run='client',
            o='yaml',
            save_config=True,
            isSecret=isSecret,
        )]
        if asSubcommand else
        [mock.call(
            'create', '-f', '-', input='kind: Secret',
            isSecret=isSecret, dry_run='client', o='yaml', save_config=True,
        )]
    )
    if checkExist:
        expectedCalls.append(mock.call(
            'get', input='normalized', isSecret=isSecret,
            o='json', f='-', ignore_not_found=True,
        ))
    if (stop in ('absent', 'apply', 'wait', 'skip-wait', 'success')):
        expectedCalls.append(mock.call(
            'apply', '-f', '-', input='normalized',
            isSecret=isSecret, o='json',
        ))
    if (stop in ('absent', 'wait', 'success')):
        expectedCalls.append(mock.call(
            'wait', 'Secret/synthetic-secret', isSecret=isSecret,
            **{'for': 'create'}, timeout='2m',
        ))
    expected = {
        'dry-run': (cli.ResPhase.DRY_RUN, dryRun, None, False),
        'lookup-failed': (
            cli.ResPhase.EXISTED, lookupResult, None, False,
        ),
        'existed': (
            cli.ResPhase.EXISTED, lookupResult, resource, True,
        ),
        'absent': (cli.ResPhase.WAITED, waitResult, resource, True),
        'apply': (cli.ResPhase.APPLIED, applyResult, None, False),
        'wait': (cli.ResPhase.WAITED, waitResult, resource, False),
        'skip-wait': (
            cli.ResPhase.APPLIED, applyResult, resource, True,
        ),
        'success': (cli.ResPhase.WAITED, waitResult, resource, True),
    }
    (expectedPhase, expectedResult, expectedResource, expectedSuccess) = (
        expected[stop]
    )
    assert (phase is expectedPhase)
    assert (result is expectedResult)
    expectedStderr = {
        'lookup-failed': 'synthetic lookup failure',
        'apply': 'synthetic-private-stderr',
        'skip-wait': 'synthetic-private-stderr',
    }.get(stop, '')
    assert (result.stderr == expectedStderr)
    assert (result.success is expectedSuccess)
    assert (actual is expectedResource)
    assert (cli.Run.call_args_list == expectedCalls)
    if (stop == 'existed'):
        MUT.json.loads.assert_called_once_with('existing-json')
    elif (stop in ('absent', 'wait', 'skip-wait', 'success')):
        MUT.json.loads.assert_called_once_with('apply-json')
    else: MUT.json.loads.assert_not_called()


@pytest.mark.parametrize('lookup', [False, True])
@pytest.mark.parametrize(('payload', 'parsed', 'parserError'), [
    ('not-json', None, ValueError('synthetic parser failure')),
    ('null', None, None),
    ('[]', [], None),
    ('{}', {}, None),
    ('"synthetic non-object"', 'synthetic non-object', None),
])
def test_NewRes_InvalidResourceJSONRaisesWithoutResponseData(
    lookup: bool,
    payload: str,
    parsed: object,
    parserError: Exception | None,
) -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [
        Result(True, 'normalized'), Result(True, payload),
    ]
    MUT.json.loads.return_value = parsed
    MUT.json.loads.side_effect = parserError

    with pytest.raises(ValueError) as caught:
        cli.NewRes('manifest', checkExist=lookup)

    expectedPhase = 'EXISTED' if lookup else 'APPLIED'
    assert (str(caught.value) == (
        '`NewRes()`: Invalid resource Live Object State at '
        f'{expectedPhase}.'
    ))
    assert (caught.value.__suppress_context__ is True)
    assert (cli.Run.call_count == 2)
    MUT.json.loads.assert_called_once_with(payload)


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
    kind: str,
    existing: bool,
    isSecret: bool,
) -> None:
    cli = BuildCLI()
    resource = {
        'kind': kind, 'metadata': {'name': 'example', 'uid': 'created-uid'},
    }
    manifest = json.dumps(resource)
    MUT.json.loads.return_value = resource
    cli.Run = mock.Mock(side_effect=[
        Result(True, manifest),
        Result(True, manifest if existing else ''),
        Result(True, manifest),
        Result(True),
    ])
    owned = []
    actual = cli.EnsureRes(manifest, owned, isSecret=isSecret)
    assert (actual == resource)
    assert (owned == (
        [] if existing else [(cli, f'{kind}/example', 'created-uid')]
    ))
    assert ([call.args[0] for call in cli.Run.call_args_list] == (
        ['create', 'get'] if existing else ['create', 'get', 'apply', 'wait']
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
        specification, checkExist=True, isSecret=False,
    )
    assert (not resource.mock_calls and not owned)


@pytest.mark.parametrize('lookup', [False, True])
def test_EnsureRes_InvalidJSONRetainsSanitizedPhase(lookup: bool) -> None:
    cli = BuildCLI()
    payload = 'synthetic-private-invalid-json'
    cli.Run.side_effect = [
        Result(True, 'normalized'),
        Result(True, payload if lookup else ''),
        Result(True, payload),
    ]
    MUT.json.loads.side_effect = ValueError('private parser error')
    owned = []

    with pytest.raises(ValueError) as caught:
        cli.EnsureRes('manifest', owned, isSecret=True)

    expectedPhase = 'EXISTED' if lookup else 'APPLIED'
    assert (str(caught.value) == (
        f'`NewRes()`: Invalid resource Live Object State at {expectedPhase}.'
    ))
    assert not owned
    assert (cli.Run.call_count == (2 if lookup else 3))
    MUT.json.loads.assert_called_once_with(payload)


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
    timeout: str | None,
    outcome: str,
) -> None:
    cli = BuildCLI()
    metadata = {'name': 'example', 'namespace': 'resource-ns'}
    deleting = {
        'kind': 'ConfigMap',
        'metadata': {
            **metadata, 'uid': 'old', 'deletionTimestamp': 'now',
        },
    }
    created = {'kind': 'ConfigMap', 'metadata': {**metadata, 'uid': 'new'}}
    manifest = json.dumps({'kind': 'ConfigMap', 'metadata': metadata})
    deletionOutput = 'synthetic-deleting-object'
    createdOutput = 'synthetic-created-object'
    responses = [
        Result(True, 'normalized'),
        Result(True, deletionOutput),
    ]
    if (outcome == 'exception'):
        responses.append(subprocess.TimeoutExpired('synthetic', 17))
    else: responses.append(Result((outcome != 'failed')))
    if (outcome not in ('failed', 'exception')):
        responses.extend([
            Result(True, 'normalized'),
            Result((outcome != 'retry'), (
                deletionOutput if (outcome == 'still-deleting') else ''
            )),
        ])
        if (outcome == 'gone'):
            responses.extend([
                Result(True, createdOutput), Result(True),
            ])
    cli.Run.side_effect = responses
    parseResults = [deleting]
    if (outcome == 'gone'): parseResults.append(created)
    elif (outcome == 'still-deleting'): parseResults.append(deleting)
    MUT.json.loads.side_effect = parseResults
    owned = []
    options = {} if (timeout is None) else {'existResDelTO': timeout}
    resRef = 'ConfigMap/example'

    if (outcome == 'gone'):
        actual = cli.EnsureRes(manifest, owned, **options)
        assert (actual is created)
        assert (len(owned) == 1)
        (ownedOC, actualRef, uid) = owned[0]
        assert (actualRef == resRef)
        assert (uid == 'new')
        assert (ownedOC.defOpts == {'n': 'resource-ns'})
    else:
        match = (
            'EXISTED' if (outcome == 'retry') else
            f'{resRef}.*{timeout or "5m"}'
        )
        with pytest.raises(RuntimeError, match=match):
            cli.EnsureRes(manifest, owned, **options)
        assert not owned

    expectedCalls = [
        mock.call(
            'create', '-f', '-', input=manifest, isSecret=False,
            dry_run='client', o='yaml', save_config=True,
        ),
        mock.call(
            'get', input='normalized', isSecret=False,
            o='json', f='-', ignore_not_found=True,
        ),
        mock.call(
            'wait', resRef, isSecret=False, **{'for': 'delete'},
            timeout=(timeout or '5m'),
        ),
    ]
    if (outcome not in ('failed', 'exception')):
        expectedCalls.extend([
            mock.call(
                'create', '-f', '-', input=manifest, isSecret=False,
                dry_run='client', o='yaml', save_config=True,
            ),
            mock.call(
                'get', input='normalized', isSecret=False,
                o='json', f='-', ignore_not_found=True,
            ),
        ])
        if (outcome == 'gone'):
            expectedCalls.extend([
                mock.call(
                    'apply', '-f', '-', input='normalized',
                    isSecret=False, o='json',
                ),
                mock.call(
                    'wait', resRef, isSecret=False,
                    **{'for': 'create'}, timeout='2m',
                ),
            ])
    assert (cli.Run.call_args_list == expectedCalls)
    expectedParses = [mock.call(deletionOutput)]
    if (outcome == 'gone'): expectedParses.append(mock.call(createdOutput))
    elif (outcome == 'still-deleting'):
        expectedParses.append(mock.call(deletionOutput))
    assert (MUT.json.loads.call_args_list == expectedParses)


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


def Result(success: bool, stdout: str = '', stderr: str = '') -> mock.Mock:
    return mock.Mock(stdout=stdout, stderr=stderr, success=success)


def BuildCLI() -> MUT.OCcli:
    cli = object.__new__(MUT.OCcli)
    cli.Run = mock.Mock()
    cli.defOpts = {}
    cli.cmdExec = mock.sentinel.cmdExec
    cli.cmnOpts = mock.sentinel.cmnOpts
    cli.shortMap = mock.sentinel.shortMap
    return cli


def test_Init_DelegatesToBase(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def RecordBaseInit(
        instance: MUT.GoCobraCLI,
        *args: object,
        **kwargs: object,
    ) -> None:
        calls.append((instance, args, kwargs))

    monkeypatch.setattr(MUT.GoCobraCLI, '__init__', RecordBaseInit)
    executor = mock.Mock()

    cli = MUT.OCcli(binExec='custom-oc', cmdExec=executor, namespace='ns')

    assert (calls == [(
        cli,
        ('custom-oc',),
        {'cmdExec': executor, 'namespace': 'ns'},
    )])


def test_NewRes_SecretFlagReachesProducerConsumerAndWait() -> None:
    expectedResource = {
        'kind': 'Secret', 'metadata': {'name': 'name', 'uid': 'uid'},
    }
    MUT.json.loads.return_value = expectedResource
    for manifest in ('kind: Secret', {'subCmd': 'secret generic'}):
        cli = BuildCLI()
        cli.Run.side_effect = [
            Result(True, 'synthetic-produced-yaml'),
            Result(True, (
                '{"kind":"Secret","metadata":{"name":"name","uid":"uid"}}'
            )),
            Result(True, 'raw-wait-result'),
        ]
        (phase, final, actual) = cli.NewRes(
            manifest, isSecret=True, waitCond='create', waitTime='5m',
        )
        assert (phase == MUT.OCcli.ResPhase.WAITED)
        assert (final.stdout == 'raw-wait-result')
        assert (actual is expectedResource)
        assert (len(cli.Run.call_args_list) == 3)
        for call in cli.Run.call_args_list:
            assert (call.kwargs['isSecret'] is True)
        assert (cli.Run.call_args_list[1].kwargs['input'] == (
            'synthetic-produced-yaml'
        ))
        assert (cli.Run.call_args_list[2].kwargs['timeout'] == '5m')


def test_NewRes_FromYAMLrunsAllPhases() -> None:
    expectedResource = {
        'kind': 'VirtualMachine',
        'metadata': {'name': 'vm', 'uid': 'uid'},
    }
    MUT.json.loads.return_value = expectedResource
    cli = BuildCLI()
    cli.Run.side_effect = [
        Result(True, 'generated yaml'),
        Result(True, 'applied-json'),
        Result(True, 'ready'),
    ]
    (phase, final, actual) = cli.NewRes(
        'kind: VirtualMachine', waitTime='5m'
    )

    assert (phase == MUT.OCcli.ResPhase.WAITED)
    assert (final.stdout == 'ready')
    assert (actual is expectedResource)
    assert (cli.Run.call_args_list == [
        mock.call(
            'create', '-f', '-', input='kind: VirtualMachine',
            dry_run='client', o='yaml', save_config=True, isSecret=False,
        ),
        mock.call(
            'apply', '-f', '-', input='generated yaml',
            o='json', isSecret=False,
        ),
        mock.call(
            'wait', 'VirtualMachine/vm', **{'for': 'create'},
            timeout='5m', isSecret=False,
        ),
    ])


def test_NewRes_FromSubcommandMergesOptions() -> None:
    expectedResource = {
        'kind': 'Secret', 'metadata': {'name': 'name', 'uid': 'uid'},
    }
    MUT.json.loads.return_value = expectedResource
    cli = BuildCLI()
    cli.Run.side_effect = [
        Result(True, 'yaml'),
        Result(True, 'applied-json'),
    ]

    (phase, final, actual) = cli.NewRes(
        {
            'subCmd': 'secret generic',
            'args': ['name'],
            'opts': {'from_literal': 'key=value'},
        },
        waitCond=None,
    )

    assert (phase == MUT.OCcli.ResPhase.APPLIED)
    assert final.success
    assert (actual is expectedResource)
    assert (cli.Run.call_args_list == [
        mock.call(
            'create secret generic', 'name',
            from_literal='key=value', dry_run='client', o='yaml',
            save_config=True, isSecret=False,
        ),
        mock.call(
            'apply', '-f', '-', input='yaml', o='json', isSecret=False,
        ),
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
    expectedResource = {'metadata': {'uid': 'uid'}}
    MUT.json.loads.return_value = expectedResource
    cli = BuildCLI()
    cli.Run.side_effect = [
        Result(True, 'normalized'),
        Result(True, '{"metadata":{"uid":"uid"}}'),
    ]
    with pytest.raises(ValueError, match='no wait resource identity'):
        cli.NewRes('manifest')
    assert (cli.Run.call_count == 2)
    MUT.json.loads.assert_called_once_with('{"metadata":{"uid":"uid"}}')

    cli.Run.reset_mock()
    cli.Run.side_effect = [
        Result(True, 'normalized'),
        Result(True, '{"metadata":{"uid":"uid"}}'),
    ]
    (phase, result, actual) = cli.NewRes('manifest', waitCond=None)
    assert ((phase == MUT.OCcli.ResPhase.APPLIED) and result.success)
    assert (actual is expectedResource)
    assert (cli.Run.call_count == 2)
    assert (MUT.json.loads.call_args_list == [
        mock.call('{"metadata":{"uid":"uid"}}'),
        mock.call('{"metadata":{"uid":"uid"}}'),
    ])


@pytest.mark.parametrize('options', [
    None, {}, {'waitTime': '5m'},
    {'resRef': 'VirtualMachine/unowned', 'waitTime': '7m'},
])
def test_CleanUpRes_ForwardsOptionsWithoutMutatingOrReplacingOwnership(
    options: dict | None,
) -> None:
    cli = BuildCLI()
    cli.Run.side_effect = [Result(True, 'owned'), Result(True)]
    before = None if (options is None) else dict(options)

    MUT.OCcli.CleanUpRes(
        [(cli, 'VirtualMachine/example', 'owned')], optsDelRes=options,
    )

    assert (cli.Run.call_count == 2)
    assert (cli.Run.call_args_list[-1] == mock.call(
        'delete', 'VirtualMachine/example',
        ignore_not_found=True,
        timeout=(options or {}).get('waitTime', '2m'), wait='true',
    ))
    assert (options == before)


@pytest.mark.parametrize('success', [False, True])
@pytest.mark.parametrize('options', [
    {}, {'waitTime': '3m'}, {'waitTime': '4m'},
])
def test_DelRes_UsesSingleCommandAndPreservesResult(
    success: bool,
    options: dict,
) -> None:
    cli = BuildCLI()
    deletion = Result(success, 'delete output', 'delete diagnostic')
    cli.Run.return_value = deletion

    (phase, actual) = cli.DelRes('Pod/name', **options)

    assert (phase is cli.ResPhase.WAITED)
    assert (actual is deletion)
    assert (actual.success is success)
    assert (actual.stdout == 'delete output')
    assert (actual.stderr == 'delete diagnostic')
    cli.Run.assert_called_once_with(
        'delete',
        'Pod/name',
        ignore_not_found=True,
        timeout=options.get('waitTime', '2m'),
        wait='true',
    )


@pytest.fixture(name='commandCLI')
def FxtCommandCLI() -> tuple[MUT.OCcli, mock.Mock]:
    '''Provide an OC client and its mocked command boundary.'''
    cli = BuildCLI()
    cli.defOpts.update({
        'kubeconfig': '/selected/config',
        'context': 'selected-context',
    })
    return (cli, cli.Run)


@pytest.mark.parametrize('namespace', [None, '', 'resource-ns'])
@pytest.mark.parametrize('defaults', [
    {}, {'n': 'short-ns'}, {'namespace': 'long-ns'},
    {'n': 'short-ns', 'namespace': 'long-ns'},
])
def test__InNS_IsolatesDefaultsAndReusesExecutorAndDiscovery(
    commandCLI: tuple[MUT.OCcli, mock.Mock],
    namespace: str | None,
    defaults: dict,
) -> None:
    (cli, run) = commandCLI
    cli.defOpts.update(defaults)
    original = dict(cli.defOpts)

    scoped = cli._InNS(namespace)

    assert (cli.defOpts == original)
    if namespace:
        assert (scoped is not cli)
        assert (type(scoped) is type(cli))
        assert (scoped.defOpts is not cli.defOpts)
        expectedDefaults = {
            key: value for (key, value) in original.items()
            if (key not in {'n', 'namespace'})
        } | {'n': namespace}
        assert (scoped.defOpts == expectedDefaults)
        assert (scoped.Run is run)
        assert (scoped.cmdExec is cli.cmdExec)
        assert (scoped.cmnOpts is cli.cmnOpts)
        assert (scoped.shortMap is cli.shortMap)
        run.return_value = Result(True, 'selected-uid')
        assert (scoped.GetResUID('ConfigMap/example') == (
            True, 'selected-uid', '',
        ))
        run.assert_called_once_with(
            'get',
            'ConfigMap/example',
            o='jsonpath={.metadata.uid}',
            ignore_not_found=True,
        )
    else:
        assert (scoped is cli)
        run.assert_not_called()


@pytest.mark.parametrize('namespace', [None, 'resource-ns'])
@pytest.mark.parametrize('waitCond', [None, 'create'])
@pytest.mark.parametrize('native', [False, True])
def test_EnsureRes_WaitAndOwnershipCleanupUseReturnedNamespace(
    commandCLI: tuple[MUT.OCcli, mock.Mock],
    namespace: str | None,
    waitCond: str | None,
    native: bool,
) -> None:
    (cli, run) = commandCLI
    original = dict(cli.defOpts)
    kind = 'ConfigMap' if namespace else 'Namespace'
    metadata = {'name': 'example'}
    if namespace: metadata['namespace'] = namespace
    manifest = json.dumps({'kind': kind, 'metadata': metadata})
    resource = {
        'kind': kind,
        'metadata': {**metadata, 'uid': 'created-uid'},
    }
    MUT.json.loads.return_value = resource
    specification = (
        {
            'subCmd': 'configmap' if namespace else 'namespace',
            'args': ['example'],
            'opts': {'n': namespace} if namespace else {},
        }
        if native else manifest
    )
    responses = [
        Result(True, 'generated-yaml'),
        Result(True, ''),
        Result(True, 'applied-json'),
    ]
    if (waitCond is not None): responses.append(Result(True))
    responses.extend([Result(True, 'created-uid'), Result(True)])
    run.side_effect = responses
    owned = []

    actual = cli.EnsureRes(
        specification,
        owned,
        isSecret=True,
        optsNewRes={'waitCond': waitCond},
    )

    assert (actual is resource)
    assert (len(owned) == 1)
    (ownedOC, resRef, uid) = owned[0]
    assert (resRef == f'{kind}/example')
    assert (uid == 'created-uid')
    assert (ownedOC is not cli if namespace else ownedOC is cli)
    assert (ownedOC.Run is run)
    expectedDefaults = original | ({'n': namespace} if namespace else {})
    assert (ownedOC.defOpts == expectedDefaults)
    MUT.json.loads.assert_called_once_with('applied-json')
    MUT.OCcli.CleanUpRes(owned)

    expectedCalls = (
        [mock.call(
            'create configmap', 'example', n=namespace,
            dry_run='client', o='yaml', save_config=True, isSecret=True,
        )]
        if (native and namespace) else
        [mock.call(
            'create namespace', 'example',
            dry_run='client', o='yaml', save_config=True, isSecret=True,
        )]
        if native else
        [mock.call(
            'create', '-f', '-', input=manifest,
            isSecret=True, dry_run='client', o='yaml', save_config=True,
        )]
    )
    expectedCalls.extend([
        mock.call(
            'get', input='generated-yaml', isSecret=True,
            o='json', f='-', ignore_not_found=True,
        ),
        mock.call(
            'apply', '-f', '-', input='generated-yaml',
            isSecret=True, o='json',
        ),
    ])
    if (waitCond is not None):
        expectedCalls.append(mock.call(
            'wait', resRef, isSecret=True,
            **{'for': 'create'}, timeout='2m',
        ))
    expectedCalls.extend([
        mock.call(
            'get', resRef, o='jsonpath={.metadata.uid}',
            ignore_not_found=True,
        ),
        mock.call(
            'delete', resRef, ignore_not_found=True,
            timeout='2m', wait='true',
        ),
    ])
    assert (run.call_args_list == expectedCalls)
    assert (cli.defOpts == original)


@pytest.mark.parametrize('waited', [False, True])
def test_EnsureRes_PendingDeletionWaitUsesReturnedNamespace(
    commandCLI: tuple[MUT.OCcli, mock.Mock],
    waited: bool,
) -> None:
    (cli, run) = commandCLI
    original = dict(cli.defOpts)
    metadata = {'name': 'example', 'namespace': 'resource-ns'}
    manifest = json.dumps({'kind': 'ConfigMap', 'metadata': metadata})
    deleting = {
        'kind': 'ConfigMap',
        'metadata': {
            **metadata,
            'uid': 'old',
            'deletionTimestamp': '2026-01-01T00:00:00Z',
        },
    }
    created = {
        'kind': 'ConfigMap', 'metadata': {**metadata, 'uid': 'new'},
    }
    responses = [
        Result(True, 'normalized'),
        Result(True, 'deleting-json'),
        Result(waited),
    ]
    if waited:
        responses.extend([
            Result(True, 'normalized'),
            Result(True, ''),
            Result(True, 'created-json'),
            Result(True),
        ])
    run.side_effect = responses
    MUT.json.loads.side_effect = [deleting, created] if waited else [deleting]
    owned = []
    resRef = 'ConfigMap/example'

    if waited:
        actual = cli.EnsureRes(manifest, owned)
        assert (actual is created)
        assert (len(owned) == 1)
        (ownedOC, actualRef, uid) = owned[0]
        assert (ownedOC.defOpts == (original | {'n': 'resource-ns'}))
        assert (actualRef == resRef)
        assert (uid == 'new')
    else:
        with pytest.raises(RuntimeError, match='Waiting for deletion'):
            cli.EnsureRes(manifest, owned)
        assert not owned
    assert (cli.defOpts == original)
    expectedCalls = [
        mock.call(
            'create', '-f', '-', input=manifest, isSecret=False,
            dry_run='client', o='yaml', save_config=True,
        ),
        mock.call(
            'get', input='normalized', isSecret=False,
            o='json', f='-', ignore_not_found=True,
        ),
        mock.call(
            'wait', resRef, isSecret=False, **{'for': 'delete'},
            timeout='5m',
        ),
    ]
    if waited:
        expectedCalls.extend([
            mock.call(
                'create', '-f', '-', input=manifest, isSecret=False,
                dry_run='client', o='yaml', save_config=True,
            ),
            mock.call(
                'get', input='normalized', isSecret=False,
                o='json', f='-', ignore_not_found=True,
            ),
            mock.call(
                'apply', '-f', '-', input='normalized',
                isSecret=False, o='json',
            ),
            mock.call(
                'wait', resRef, isSecret=False,
                **{'for': 'create'}, timeout='2m',
            ),
        ])
    assert (run.call_args_list == expectedCalls)
    assert (MUT.json.loads.call_args_list == (
        [mock.call('deleting-json'), mock.call('created-json')]
        if waited else [mock.call('deleting-json')]
    ))


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('applyFails', [False, True])
def test_NewRes_UncheckedApplyFailureNeverDeletesExistingResource(
    native: bool,
    applyFails: bool,
) -> None:
    cli = BuildCLI()
    resource = {
        'kind': 'Namespace', 'metadata': {'name': 'shared', 'uid': 'existing'},
    }
    manifest = json.dumps(resource)
    MUT.json.loads.return_value = resource
    cli.Run.side_effect = [
        Result(True, manifest), Result(not applyFails, manifest),
        Result(False),
    ]
    specification = (
        {'subCmd': 'namespace', 'args': ['shared']} if native else manifest
    )

    (phase, result, _) = cli.NewRes(specification)

    assert (phase == cli.ResPhase['APPLIED' if applyFails else 'WAITED'])
    assert not result.success
    assert ([call.args[0] for call in cli.Run.call_args_list] == (
        ['create namespace' if native else 'create', 'apply'] +
        ([] if applyFails else ['wait'])
    ))


@pytest.mark.parametrize('outcome', [
    'owned', 'changed', 'absent', 'failed', 'exception',
])
@pytest.mark.parametrize('isSecret', [False, True])
def test_NewRes_FailedWaitDeletesOnlyConfirmedCreatedUID(
    outcome: str,
    isSecret: bool,
) -> None:
    cli = BuildCLI()
    resource = {
        'kind': 'Secret', 'metadata': {'name': 'example', 'uid': 'created'},
    }
    manifest = json.dumps(resource)
    MUT.json.loads.return_value = resource
    responses = [
        Result(True, manifest), Result(True), Result(True, manifest),
        Result(False, 'original wait result'),
        RuntimeError('lookup failed') if (outcome == 'exception') else Result(
            (outcome != 'failed'),
            {'absent': '', 'changed': 'replacement'}.get(outcome, 'created'),
        ),
    ]
    if (outcome == 'owned'): responses.append(Result(True))
    cli.Run.side_effect = responses

    (phase, result, actual) = cli.NewRes(
        manifest, checkExist=True, isSecret=isSecret,
    )

    assert (phase is cli.ResPhase.WAITED)
    assert not result.success
    assert (result.stdout == 'original wait result')
    assert (actual == resource)
    assert (cli.Run.call_args_list[4] == mock.call(
        'get', 'Secret/example', isSecret=isSecret, spPars={'timeout': 30},
        o='jsonpath={.metadata.uid}', ignore_not_found=True,
    ))
    if (outcome == 'owned'):
        assert (cli.Run.call_args_list[5] == mock.call(
            'delete', 'Secret/example', isSecret=isSecret,
            spPars={'timeout': 30}, force=True, grace_period=0,
            ignore_not_found=True, wait=False,
        ))
    else: assert (cli.Run.call_count == 5)
    assert all(
        (call.kwargs['isSecret'] is isSecret)
        for call in cli.Run.call_args_list
    )


@pytest.mark.parametrize('uid', [None, '', 17])
def test_NewRes_FailedWaitWithoutCreatedUIDNeverDeletes(uid: object) -> None:
    cli = BuildCLI()
    resource = {
        'kind': 'ConfigMap', 'metadata': {'name': 'example', 'uid': uid},
    }
    manifest = json.dumps(resource)
    MUT.json.loads.return_value = resource
    cli.Run.side_effect = [
        Result(True, manifest), Result(True), Result(True, manifest),
        Result(False),
    ]

    (phase, result, _) = cli.NewRes(manifest, checkExist=True)

    assert (phase is cli.ResPhase.WAITED)
    assert not result.success
    assert (cli.Run.call_count == 4)
    assert not any(
        (call.args[0] == 'delete') for call in cli.Run.call_args_list
    )
    MUT.json.loads.assert_called_once_with(manifest)


@pytest.mark.parametrize('isSecret', [False, True])
def test_NewRes_FailedWaitCleanupUsesCreatedNamespaceAndBoundedCommands(
    monkeypatch: pytest.MonkeyPatch,
    isSecret: bool,
) -> None:
    cli = BuildCLI()
    cli.defOpts.update({'n': 'wrong-short', 'namespace': 'wrong-long'})
    defaults = dict(cli.defOpts)
    resource = {
        'kind': 'Secret',
        'metadata': {
            'name': 'example', 'namespace': 'created-ns', 'uid': 'created',
        },
    }
    manifest = json.dumps(resource)
    MUT.json.loads.return_value = resource
    waitFailure = Result(False, 'original wait result')
    responses = [
        Result(True, 'normalized'),
        Result(True, ''),
        Result(True, 'applied-json'),
        waitFailure,
        Result(True, 'created'),
        Result(True),
    ]
    calls = []

    def Run(
        client: MUT.GoCobraCLI,
        command: str,
        *args: object,
        **kwargs: object,
    ) -> mock.Mock:
        calls.append((client, command, args, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(MUT.GoCobraCLI, 'Run', Run)
    del cli.Run

    (phase, result, actual) = cli.NewRes(
        manifest, checkExist=True, isSecret=isSecret,
    )

    assert (phase is cli.ResPhase.WAITED)
    assert (result is waitFailure)
    assert (not result.success)
    assert (actual is resource)
    assert (cli.defOpts == defaults)
    assert (len(calls) == 6)
    scoped = calls[3][0]
    assert (calls[0][0] is cli)
    assert (calls[1][0] is cli)
    assert (calls[2][0] is cli)
    assert (scoped is not cli)
    assert (calls[4][0] is scoped)
    assert (calls[5][0] is scoped)
    assert (scoped.defOpts == ({
        key: value for (key, value) in defaults.items()
        if (key not in {'n', 'namespace'})
    } | {'n': 'created-ns'}))
    expectedCalls = [
        (
            'create', ('-f', '-'), {
                'input': manifest, 'isSecret': isSecret,
                'dry_run': 'client', 'o': 'yaml', 'save_config': True,
            },
        ),
        (
            'get', (), {
                'input': 'normalized', 'isSecret': isSecret,
                'o': 'json', 'f': '-', 'ignore_not_found': True,
            },
        ),
        (
            'apply', ('-f', '-'), {
                'input': 'normalized', 'isSecret': isSecret, 'o': 'json',
            },
        ),
        (
            'wait', ('Secret/example',), {
                'isSecret': isSecret, 'for': 'create', 'timeout': '2m',
            },
        ),
        (
            'get', ('Secret/example',), {
                'isSecret': isSecret, 'spPars': {'timeout': 30},
                'o': 'jsonpath={.metadata.uid}', 'ignore_not_found': True,
            },
        ),
        (
            'delete', ('Secret/example',), {
                'isSecret': isSecret, 'spPars': {'timeout': 30},
                'force': True, 'grace_period': 0,
                'ignore_not_found': True, 'wait': False,
            },
        ),
    ]
    assert ([(command, args, kwargs) for (_, command, args, kwargs) in calls]
            == expectedCalls)
    MUT.json.loads.assert_called_once_with('applied-json')


@pytest.mark.parametrize('phase', ['APPLIED', 'WAITED'])
@pytest.mark.parametrize('cleanup', [
    'success', 'nonzero', 'exception', 'timeout', 'absent',
])
@pytest.mark.parametrize('raises', [False, True])
def test_NewRes_FailureAndCleanupMatrixPreservesPrimary(
    phase: str,
    cleanup: str,
    raises: bool,
) -> None:
    cli = BuildCLI()
    resource = {
        'kind': 'VirtualMachine',
        'metadata': {'name': 'example', 'uid': 'created-uid'},
    }
    manifest = json.dumps(resource)
    MUT.json.loads.return_value = resource
    primary = (
        RuntimeError('original creation error') if raises
        else Result(False, 'original creation failure')
    )
    responses = [Result(True, manifest), Result(True)]
    if (phase == 'WAITED'): responses.append(Result(True, manifest))
    responses.append(primary)
    if (phase == 'WAITED'):
        responses.append(Result(
            True, '' if (cleanup == 'absent') else 'created-uid',
        ))
        if (cleanup != 'absent'):
            responses.append({
                'success': Result(True),
                'nonzero': Result(False, 'cleanup failed'),
                'exception': RuntimeError('cleanup failed'),
                'timeout': subprocess.TimeoutExpired('delete', 30),
            }[cleanup])
    cli.Run.side_effect = responses

    if raises:
        with pytest.raises(RuntimeError) as caught:
            cli.NewRes(manifest, checkExist=True)
        assert (caught.value is primary)
    else:
        (actualPhase, result, actualResource) = cli.NewRes(
            manifest, checkExist=True,
        )
        assert (actualPhase == cli.ResPhase[phase])
        assert (result is primary)
        expectedResource = resource if (phase == 'WAITED') else None
        assert (actualResource is expectedResource)
    assert ([call.args[0] for call in cli.Run.call_args_list] == (
        ['create', 'get', 'apply'] +
        (['wait', 'get'] if (phase == 'WAITED') else []) +
        (['delete'] if ((phase == 'WAITED') and (cleanup != 'absent')) else [])
    ))
    if (phase == 'WAITED'): MUT.json.loads.assert_called_once_with(manifest)
    else: MUT.json.loads.assert_not_called()
