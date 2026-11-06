'''OpenShift Resource Namespace handler.'''
from typing import Any, Optional

from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.OpenShift.OCP.Resources import (
    OwnInf, ResCfg,
    _ValResCfg, _ResCreate,
)


def Create(
    oc: OCcli,
    resCfg: ResCfg,
    ownInf: Optional[OwnInf] = None,
) -> dict[str, Any]:
    '''
    Create or reuse a Namespace.

    :param oc:
        CLI Client for target OpenShift Cluster.
    :param resCfg:
        See `ResCfg` for details.
        Native mode input:
            resCfg=ResCfg(
                meta=NamedDict(name='<name>'),
            )
    :param ownInf:
        A `OwnInf` instance, containing list to track newly created Resource.
        Set to `None` (default) if no tracking is desired.

    :return:
        Existing or newly (re-)created resource Live Object State in a `dict`.
    '''
    _ValResCfg(resCfg, {'name'}, set(), 'Namespace')
    if ((resYAML := resCfg.yaml) is None):
        resInput = {'subCmd': 'namespace', 'args': [resCfg.meta.name]}
    else: resInput = resYAML
    return _ResCreate(oc, resInput, ownInf)
