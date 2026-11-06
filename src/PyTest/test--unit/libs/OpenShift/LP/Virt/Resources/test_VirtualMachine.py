from unittest import mock
import json
import subprocess
from typing import Any

import pytest

import libs.OpenShift.LP.Virt.Resources.VirtualMachine as MUT
from libs.CustomTypes import NamedDict
from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.OpenShift.OCP.Resources import OwnInf, ResCfg
from libs.Utils.CmdExec import CmdRes


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('options', [
    None, {}, {'waitTime': '15m'}, {'waitCond': None},
])
def test_Create_ForwardsYAMLAndOptions(
    tracked: bool,
    options: dict | None,
) -> None:
    cli = mock.create_autospec(OCcli, instance=True)
    resource = {'kind': 'VirtualMachine'}
    manifest = 'kind: VirtualMachine\nmetadata:\n  name: example\n'
    cli.NewRes.return_value = (
        OCcli.ResPhase.WAITED, CmdRes('', '', 0, True), resource,
    )
    cli.EnsureRes.return_value = resource
    owned = []
    cfg = ResCfg(meta=NamedDict(ignored=True), yaml=manifest)
    assert (MUT.Create(
        cli, cfg, (OwnInf(owned) if tracked else None), options,
    ) is resource)
    if tracked:
        cli.EnsureRes.assert_called_once_with(
            manifest, owned, isSecret=False, existResDelTO='5m',
            optsNewRes=dict(options or {}),
        )
    else:
        cli.NewRes.assert_called_once_with(
            manifest, isSecret=False, **(options or {}),
        )


@pytest.mark.parametrize('tracked,reused', [
    (False, False), (True, False), (True, True),
])
@pytest.mark.parametrize('options', [
    None, {}, {'waitTime': '15m'}, {'waitCond': None},
    {'waitCond': 'condition=Ready', 'waitTime': '7m'},
])
def test_Create_NativeLifecycleHonorsOptionsAndOwnership(
    tracked: bool,
    reused: bool,
    options: dict | None,
) -> None:
    resource = {
        'kind': 'VirtualMachine',
        'metadata': {'name': 'example', 'uid': 'uid'},
    }
    manifest = json.dumps(resource)
    original = (dict(options) if (options is not None) else None)
    waitCond = (options or {}).get('waitCond', 'create')
    executor = mock.Mock()
    executor.Run.return_value = CmdRes('', '', 0, True)
    cli = OCcli(cmdExec=executor)
    executor.Run.reset_mock()
    responses = [CmdRes(manifest, '', 0, True)]
    if tracked:
        responses.append(CmdRes((manifest if reused else ''), '', 0, True))
    if not reused:
        responses.append(CmdRes(manifest, '', 0, True))
        if (waitCond is not None): responses.append(CmdRes('', '', 0, True))
    executor.Run.side_effect = responses
    owned = []
    assert (MUT.Create(
        cli, ResCfg(yaml=manifest), (OwnInf(owned) if tracked else None),
        options,
    ) == resource)
    assert (options == original)
    assert (owned == ([
        (cli, 'VirtualMachine/example', 'uid'),
    ] if (tracked and not reused) else []))
    commands = ['create'] + (['get'] if tracked else [])
    if not reused:
        commands.append('apply')
        if (waitCond is not None): commands.append('wait')
    assert ([call.args[0][1] for call in executor.Run.call_args_list] == (
        commands
    ))
    assert all(
        (call.kwargs['isSecret'] is False)
        for call in executor.Run.call_args_list
    )
    if (not reused and (waitCond is not None)):
        waitArgs = executor.Run.call_args.args[0]
        assert (f'--for={waitCond}' in waitArgs)
        assert (
            f"--timeout={(options or {}).get('waitTime', '2m')}" in waitArgs
        )


@pytest.mark.parametrize('manifest', [None, 'kind: Secret'])
def test_Create_RejectsMissingOrWrongKindYAML(manifest: str | None) -> None:
    cli = mock.create_autospec(OCcli, instance=True)
    with pytest.raises(ValueError): MUT.Create(cli, ResCfg(yaml=manifest))
    cli.NewRes.assert_not_called()
    cli.EnsureRes.assert_not_called()


def test_Create_PropagatesCreationFailure() -> None:
    cli = mock.create_autospec(OCcli, instance=True)
    primary = RuntimeError('creation failed')
    cli.EnsureRes.side_effect = primary
    with pytest.raises(RuntimeError) as caught:
        MUT.Create(cli, ResCfg(yaml='kind: VirtualMachine'), OwnInf([]))
    assert (caught.value is primary)


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('phase', ['APPLIED', 'WAITED'])
@pytest.mark.parametrize('cleanup', [
    'success', 'nonzero', 'exception', 'timeout', 'absent',
])
@pytest.mark.parametrize('raises', [False, True])
def test_Create_FailedCreationRemainsFatalThroughActualHelpers(
    tracked: bool,
    phase: str,
    cleanup: str,
    raises: bool,
) -> None:
    cli = mock.create_autospec(OCcli, instance=True)
    cli.ResPhase = OCcli.ResPhase
    manifest = json.dumps({
        'kind': 'VirtualMachine', 'metadata': {'name': 'example'},
    })
    primary = (
        RuntimeError('original creation error') if raises
        else CmdRes('', 'original creation failure', 1, False)
    )
    responses = [CmdRes(manifest, '', 0, True)]
    if tracked: responses.append(CmdRes('', '', 0, True))
    if (phase == 'WAITED'): responses.append(CmdRes(manifest, '', 0, True))
    responses.append(primary)
    cleanupResult = {
        'success': CmdRes('deleted', '', 0, True),
        'nonzero': CmdRes('', 'cleanup failed', 1, False),
        'exception': RuntimeError('cleanup failed'),
        'timeout': subprocess.TimeoutExpired('delete', 30),
        'absent': CmdRes('', '', 0, True),
    }[cleanup]
    responses.append(cleanupResult)
    cli.Run.side_effect = responses
    returned = []

    def NewRes(*args: Any, **kwargs: Any) -> tuple:
        result = OCcli.NewRes(cli, *args, **kwargs)
        returned.append(result)
        return result

    cli.NewRes.side_effect = NewRes
    cli.EnsureRes.side_effect = lambda *args, **kwargs: OCcli.EnsureRes(
        cli, *args, **kwargs,
    )
    owned = []
    with pytest.raises(RuntimeError) as caught:
        MUT.Create(cli, ResCfg(yaml=manifest),
                   (OwnInf(owned) if tracked else None))
    if raises:
        assert (caught.value is primary)
        assert (returned == [])
    else:
        assert (str(caught.value) == (
            f'`EnsureRes()`: Failed at `{phase}`.' if tracked else
            f'Resource creation failed at {phase}.'
        ))
        assert (len(returned) == 1)
        assert (returned[0][0] == OCcli.ResPhase[phase])
        assert (returned[0][1] is primary)
        if (phase == 'APPLIED'): assert (returned[0][2] is None)
        else: assert (returned[0][2] == json.loads(manifest))
    assert (owned == [])
    cli.NewRes.assert_called_once()
    assert (cli.Run.call_args_list[-1] == mock.call(
        'delete', '-f', '-', input=manifest, isSecret=False,
        force=True, grace_period=0, ignore_not_found=True, wait=False,
        spPars={'timeout': 30},
    ))
    assert ([call.args[0] for call in cli.Run.call_args_list] == (
        ['create'] + (['get'] if tracked else []) + ['apply'] +
        (['wait'] if (phase == 'WAITED') else []) + ['delete']
    ))
