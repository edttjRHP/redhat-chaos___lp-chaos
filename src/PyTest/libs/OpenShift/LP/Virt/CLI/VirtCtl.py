'''Thin shim around the `virtctl` CLI.'''
from typing import Optional

from libs.Utils.CLI import GoCobraCLI
from libs.Utils.CmdExec import CmdExec


class VirtCtlCLI(GoCobraCLI):
    '''
    Thin shim around the `virtctl` CLI.

    Inherits common-option auto-discovery and Cobra-correct flag handling
    from `GoCobraCLI`.
    '''

    def __init__(
        self,
        binExec: str = 'virtctl',
        cmdExec: Optional[CmdExec] = None,
        **defOpts,
    ) -> None:
        '''
        :param binExec:
            The `virtctl` CLI executable name or path (default: `'virtctl'`).
        :param cmdExec:
            Optional CmdExec instance (default: `new CmdExec()`).
        :param defOpts:
            Default values for common options, e.g.
            `namespace='windows-bsod'`, `kubeconfig='/path/to/kcfg'`.
        '''
        super().__init__(binExec, cmdExec=cmdExec, **defOpts)
