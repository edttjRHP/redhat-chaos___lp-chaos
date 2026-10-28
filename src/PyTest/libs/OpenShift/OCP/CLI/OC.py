'''Thin shim around the `oc` CLI.'''
from enum import auto, IntEnum
import json
import logging
import sys
from typing import Any, Dict, Optional, Tuple, Union

from libs.Utils.CLI import GoCobraCLI
from libs.Utils.CmdExec import CmdExec, CmdRes


logs = logging.getLogger(__name__)


class OCcli(GoCobraCLI):
    '''
    Thin shim around the `oc` CLI.

    Inherits common-option auto-discovery and Cobra-correct flag handling
    from `GoCobraCLI`.
    '''

    class ResPhase(IntEnum):
        '''
        Phase indicator for `NewRes` / `DelRes` results.
        '''
        DRY_RUN = 0
        EXISTED = auto()
        APPLIED = auto()
        DELETED = auto()
        WAITED  = auto()

    def __init__(
        self,
        binExec: str = 'oc',
        cmdExec: Optional[CmdExec] = None,
        **defOpts: Any,
    ) -> None:
        '''
        :param binExec:
            The `oc` CLI executable name or path (default: `'oc'`).
        :param cmdExec:
            Optional CmdExec instance (default: `new CmdExec()`).
        :param defOpts:
            Default values for common options, e.g.
            `namespace='windows-bsod'`, `kubeconfig='/path/to/kcfg'`.
        '''
        super().__init__(binExec, cmdExec=cmdExec, **defOpts)

    def EnsureRes(
        self,
        resYAMLorSubCmd: Union[str, Dict],
        owned: list[tuple['OCcli', str, str]],
        isSecret: bool = False,
        existResDelTO: str = '5m',
    ) -> Dict[str, Any]:
        '''
        Reuse an existing resource or create and record a new resource. An
        existing resource, that is being deleted, is awaited then recreated.

        :param resYAMLorSubCmd:
            Resource YAML or create sub-command specification, as in
            `self.NewRes`.
        :param owned:
            Ownership records (oc, resRef, resUID). Only successfully created
            resources are appended; existing resources stay unowned.
        :param isSecret:
            Protect command input and output containing credential data.
        :param existResDelTO:
            Timeout for an existing resource's pending deletion
            (default: '5m'). Wait once, then re-ensure once.
        :return:
            Existing or newly (re-)created resource Live Object State in a
            `dict`.
        :raises RuntimeError:
            The command failed, its response is not a resource dictionary,
            deletion wait failed, the re-ensure still finds a deleting object,
            or a new resource lacks kind, name or UID. The message identifies
            the operation/phase without including command output.
        :raises ValueError:
            The `self.NewRes()` received invalid resource Live Object State.
        '''
        reCreate = False
        while True:
            (phase, result, resource) = self.NewRes(
                resYAMLorSubCmd,
                checkExist=True,
                isSecret=isSecret,
                waitCond=None,
            )
            if not result.success:
                raise RuntimeError(
                    f'`EnsureRes()`: Failed at `{phase.name}`.'
                )
            if (not isinstance(resource, dict) or not resource):
                raise RuntimeError(
                    f'`EnsureRes()`: Invalid resource at `{phase.name}`.'
                )
            deleting = (
                (phase == self.ResPhase.EXISTED) and
                (
                    resource.get('metadata', {}).get('deletionTimestamp')
                    is not None
                )
            )
            if ((phase == self.ResPhase.EXISTED) and not deleting):
                return resource
            try:
                (kind, name) = (
                    resource['kind'],
                    resource['metadata']['name'],
                )
                if not all((
                    isinstance(value, str) and
                    value
                ) for value in (kind, name)): raise ValueError()
                if not deleting:
                    uid = resource['metadata']['uid']
                    if not (isinstance(uid, str) and uid): raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise RuntimeError(
                    f'`EnsureRes()`: Invalid identity at `{phase.name}`.'
                ) from None
            resRef = f'{kind}/{name}'
            if deleting:
                message = (
                    f'`EnsureRes()`: Waiting for deletion of `{resRef}` '
                    f'failed (timeout: {existResDelTO}).'
                )
                if reCreate:
                    raise RuntimeError(
                        f'`EnsureRes()`: Re-create `{resRef}` failed. '
                        'Previous resource instance is still being deleted '
                        f'after wating for `{existResDelTO}`.'
                    )
                try:
                    waited = self.Run(
                        'wait',
                        resRef,
                        isSecret=isSecret,
                        **{'for': 'delete'}, timeout=existResDelTO,
                    )
                except Exception: raise RuntimeError(message) from None
                if not waited.success: raise RuntimeError(message)
                reCreate = True
                continue
            owned.append((self, resRef, uid))
            return resource

    def NewRes(
        self,
        resYAMLorSubCmd: Union[str, Dict],
        checkExist: bool = False,
        isSecret: bool = False,
        waitCond: Optional[str] = 'create',
        waitTime: str = '2m',
    ) -> Tuple['OCcli.ResPhase', CmdRes, Optional[Dict[str, Any]]]:
        '''
        Idempotent resource creation:
        `oc create ... --dry-run=client -o yaml --save-config | oc apply -f -`

        Two modes based on `resYAMLorSubCmd` type:
          - `str`
            Raw YAML manifest, used when there is no corresponding `oc create`
            sub-command.
            Piped to `oc create -f - --dry-run=client ...`
          - dict
            The `oc create` sub-command spec. (forward-compatible API version)
            Calls
            `oc create <subCmd...> <args...> <opts...> --dry-run=client ...`

        :param resYAMLorSubCmd:
            Either a raw YAML `str`, or a `dict` with keys:
              - 'subCmd': str    -> The `oc create` sub-commands
                                    (e.g. `'secret generic'`).
              - 'args': List     -> The sub-commands pos. arguments
                                    (e.g. `['my-secret']`).
              - 'opts': Dict     -> The sub-commands keyword options
                                    (e.g. `{'from_literal': 'k=v'}`).
        :param checkExist:
            Look up for existing resource first before creating. If found,
            return `EXISTED` with the lookup result and the existing resource
            Live Object State.
            Setting to `False` (default) skips the lookup.
        :param isSecret:
            Forward framework log/record suppression to every producer and
            consumer call. Raw results remain the caller's responsibility.
        :param waitCond:
            `oc wait --for` condition (default: `'create'`).
            Pass `None` to skip the wait step entirely.
        :param waitTime:
            `oc wait --timeout` value (default: `'2m'`).
        :return:
            `(phase, lastCmdRes, resObj)` tuple. `phase` is a `ResPhase` enum
            indicating which step produced the result:
              - DRY_RUN  -> The `dry-run` failed.
              - EXISTED  -> The lookup found existing resource or it failed.
              - APPLIED  -> The `apply` failed  or `wait` skipped.
              - WAITED   -> The `wait` completed (new resource only).
            Check `lastCmdRes.success` for the outcome.
            The `resObj` is `None` before an object is obtained. Otherwise it
            is the resource Live Object State (at lookup time for existing
            resource or at creation time fo new resource).
            The Live Object State is NOT updated if it is waited via
            `waitCond`.
        :raises ValueError:
            A successful lookup or apply returned invalid resource JSON, or
            the apply response lacks the identity needed for the wait.
            The error identifies the phase without including response data.
        :raises Exception:
            Propagates exceptions from the CLI command runner.
        '''
        if isinstance(resYAMLorSubCmd, str):
            # YAML path: oc create -f - --dry-run=client -o yaml --save-config
            dryRun = self.Run(
                'create',
                '-f', '-',
                input=resYAMLorSubCmd,
                isSecret=isSecret,
                dry_run='client', o='yaml', save_config=True,
            )
        else:
            # Sub-command path: oc create <subCmd> *args **opts --dry-run ...
            createOpts = {
                **resYAMLorSubCmd.get('opts', {}),
                'isSecret': isSecret,
                'dry_run': 'client', 'o': 'yaml', 'save_config': True,
            }
            dryRun = self.Run(
                f"create {resYAMLorSubCmd['subCmd']}",
                *resYAMLorSubCmd.get('args', []),
                **createOpts,
            )

        if not dryRun.success: return (self.ResPhase.DRY_RUN, dryRun, None)

        if checkExist:
            found = self.Run(
                'get',
                '-f', '-',
                input=dryRun.stdout,
                isSecret=isSecret,
                o='json', ignore_not_found=True,
            )
            if not found.success: return (self.ResPhase.EXISTED, found, None)
            if (found.stdout or '').strip():
                try:
                    resObj = json.loads(found.stdout)
                    if not (isinstance(resObj, dict) and resObj):
                        raise ValueError()
                except (TypeError, ValueError):
                    raise ValueError(
                        '`NewRes()`: Invalid resource Live Object State at '
                        f'{self.ResPhase.EXISTED.name}.'
                    ) from None
                return (self.ResPhase.EXISTED, found, resObj)

        applied = self.Run(
            'apply',
            '-f', '-',
            input=dryRun.stdout,
            isSecret=isSecret,
            o='json',
        )
        if not applied.success: return (self.ResPhase.APPLIED, applied, None)
        try:
            resObj = json.loads(applied.stdout)
            if not (isinstance(resObj, dict) and resObj): raise ValueError()
        except (TypeError, ValueError):
            raise ValueError(
                '`NewRes()`: Invalid resource Live Object State at '
                f'{self.ResPhase.APPLIED.name}.'
            ) from None
        if (waitCond is None): return (self.ResPhase.APPLIED, applied, resObj)

        try:
            resRef = f"{resObj['kind']}/{resObj['metadata']['name']}"
        except (KeyError, TypeError):
            raise ValueError(
                '`NewRes()`: Apply response has no wait resource identity.'
            ) from None
        return (self.ResPhase.WAITED, self.Run(
            'wait',
            resRef,
            isSecret=isSecret,
            **{'for':  waitCond}, timeout=waitTime,
        ), resObj)

    def GetResUID(self, resRef: str) -> tuple[bool, str, str]:
        '''
        Look up a resource UID in this CLI client's configured context.

        :param resRef:
            Resource Reference (`<kind>/<name>`).
        :return:
            A `tuple` where:
              - lookupOK:   Lookup command success, not an existence flag.
              - resUID:     Stripped Resource UID output, empty if the resource
                            is absent.
              - lookupErr:  Stripped standard error output, which may be empty.
        :raises Exception:
            Propagates exceptions from the CLI command runner.
        '''
        result = self.Run(
            'get',
            resRef,
            o='jsonpath={.metadata.uid}', ignore_not_found=True,
        )
        return (result.success, result.stdout.strip(), result.stderr.strip())

    @staticmethod
    def CleanUpRes(ownRes: list[tuple['OCcli', str, str]]) -> None:
        '''
        Delete resources recorded as owned through their creation CLI clients.

        A resource is deleted only when its current UID matches the recorded
        UID. Lookup and deletion are not atomic. Each record is attempted even
        if an earlier lookup or deletion fails.

        :param ownRes:
            Ownership records containing:
              - oc:     CLI client from the resource creation context.
              - resRef: Resource Reference (`<kind>/<name>`).
              - ownUID: Resource UID recorded after resource creation.
        :raises RuntimeError:
            Aggregated failures raise `RuntimeError` when no exception is
            active. Otherwise, failures are logged and the active exception is
            preserved.
        '''
        primExc = sys.exc_info()[1]
        clnFail = []
        for (oc, resRef, ownUID) in ownRes:
            try:
                (lookupOK, curUID, lookupErr) = oc.GetResUID(resRef)
                if not lookupOK:
                    clnFail.append(
                        f'Verify ownership of `{resRef}`: {lookupErr}'
                    )
                    logs.error(
                        'Could not verify ownership of `%s` before '
                        'deletion: %s',
                        resRef,
                        lookupErr,
                    )
                    continue
                if not curUID:
                    logs.info('Resource `%s` is already absent.', resRef)
                    continue
                if (curUID != ownUID):
                    logs.warning(
                        'Not deleting resource `%s` because its UID changed.',
                        resRef,
                    )
                    continue
                (phase, result) = oc.DelRes(resRef)
                if not result.success:
                    clnFail.append(f'Delete `{resRef}` at `{phase.name}`: '
                                   f'{result.stderr}')
                    logs.error(
                        'Could not clean up resource `%s` at `%s`: %s',
                        resRef,
                        phase.name,
                        result.stderr,
                    )
                    continue
                logs.info('Cleaned up resource `%s`.', resRef)
            except Exception as error:
                clnFail.append(f'Delete `{resRef}`: {error}')
                logs.exception('Could not clean up resource `%s`.', resRef)
        if clnFail:
            clnErr = 'Resource cleanup failed: ' + '; '.join(clnFail)
            if (primExc is None): raise RuntimeError(clnErr)
            logs.error('%s; preserving primary exception.', clnErr)

    def DelRes(
        self,
        resRef: str,
        waitTime: str = '2m',
    ) -> Tuple['OCcli.ResPhase', CmdRes]:
        '''
        Idempotent resource deletion with wait:
        `oc delete <resRef> --ignore-not-found`

        :param resRef:
            Resource reference (e.g. `'VirtualMachine/my-vm'`,
            `'Pod/test-pod'`).
        :param waitTime:
            `oc wait --timeout` value (default: `'2m'`).
        :return:
            `(phase, lastCmdRes)` tuple. `phase` is a `ResPhase` enum:
              - DELETED  -> The "delete" failed.
              - WAITED   -> The "wait" completed (deletion).
            Check `lastCmdRes.success` for the outcome.
        '''
        deleted = self.Run(
            'delete', resRef,
            ignore_not_found=True,
        )
        if not deleted.success: return (self.ResPhase.DELETED, deleted)

        return (self.ResPhase.WAITED, self.Run(
            'wait', resRef,
            **{'for': 'delete'}, timeout=waitTime,
        ))
