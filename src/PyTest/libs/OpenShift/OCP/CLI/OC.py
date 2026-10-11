'''Thin shim around the `oc` CLI.'''
from enum import auto, IntEnum
from typing import Dict, Optional, Tuple, Union

from libs.Utils.CLI import GoCobraCLI
from libs.Utils.CmdExec import CmdExec, CmdRes


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
        APPLIED = auto()
        DELETED = auto()
        WAITED  = auto()

    def __init__(
        self,
        binExec: str = 'oc',
        cmdExec: Optional[CmdExec] = None,
        **defOpts,
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

    def NewRes(
        self,
        resOrYaml: Union[str, Dict],
        waitCond: Optional[str] = 'create',
        waitTime: str = '2m',
    ) -> Tuple['OCcli.ResPhase', CmdRes]:
        '''
        Idempotent resource creation:
        `oc create ... --dry-run=client -o yaml --save-config | oc apply -f -`

        Two modes based on `resOrYaml` type:
          - `str`
            Raw YAML manifest, used when there is no corresponding `oc create`
            sub-command.
            Piped to `oc create -f - --dry-run=client ...`
          - dict
            The `oc create` sub-command spec. (forward-compatible API version)
            Calls
            `oc create <subCmd...> <args...> <opts...> --dry-run=client ...`

        :param resOrYaml:
            Either a raw YAML `str`, or a `dict` with keys:
              - 'subCmd': str    -> The `oc create` sub-commands
                                    (e.g. `'secret generic'`).
              - 'args': List     -> The sub-commands pos. arguments
                                    (e.g. `['my-secret']`).
              - 'opts': Dict     -> The sub-commands keyword options
                                    (e.g. `{'from_literal': 'k=v'}`).
        :param waitCond:
            `oc wait --for` condition (default: `'create'`).
            Pass `None` to skip the wait step entirely.
        :param waitTime:
            `oc wait --timeout` value (default: `'2m'`).
        :return:
            `(phase, lastCmdRes)` tuple. `phase` is a `ResPhase` enum
            indicating which step produced the result:
              - DRY_RUN  -> The "dry-run" failed.
              - APPLIED  -> The "apply" failed  or "wait" skipped.
              - WAITED   -> The "wait" completed (create).
            Check `lastCmdRes.success` for the outcome.
        '''
        if isinstance(resOrYaml, str):
            # YAML path: oc create -f - --dry-run=client -o yaml --save-config
            dryRun = self.Run(
                'create', '-f', '-',
                input=resOrYaml,
                dry_run='client', o='yaml', save_config=True,
            )
        else:
            # Subcommand path: oc create <subCmd> *args **opts --dry-run ...
            createOpts = {**resOrYaml.get('opts', {}),
                          'dry_run': 'client', 'o': 'yaml', 'save_config': True}
            dryRun = self.Run(
                f"create {resOrYaml['subCmd']}",
                *resOrYaml.get('args', []),
                **createOpts,
            )

        if not dryRun.success: return (self.ResPhase.DRY_RUN, dryRun)

        applied = self.Run('apply', '-f', '-', input=dryRun.stdout)
        if (not applied.success or (waitCond is None)):
            return (self.ResPhase.APPLIED, applied)

        # Extract resource ref. from "apply" output (e.g.
        #   'VirtualMachine.kubevirt.io/my-vm configured').
        resRef = applied.stdout.strip()
        resRef = resRef.split()[0] if resRef else ''
        if not resRef: return (self.ResPhase.APPLIED, applied)

        return (self.ResPhase.WAITED, self.Run(
            'wait', resRef,
            **{'for':  waitCond}, timeout=waitTime,
        ))

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
