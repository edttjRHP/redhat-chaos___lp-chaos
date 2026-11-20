'''OpenShift Resource Deployment handler.'''
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
    Create or reuse a Deployment.

    Native mode uses `oc create deployment`, which labels the Deployment, its
    selector and its Pod template with `app=<name>`.

    :param oc:
        CLI Client for target OpenShift Cluster.
    :param resCfg:
        See `ResCfg` for details.
        Native mode input:
            resCfg=ResCfg(
                meta=NamedDict(namespace='<ns>', name='<name>'),
                spec=NamedDict(image='<image>'),
            )
    :param ownInf:
        A `OwnInf` instance, containing list to track newly created Resource.
        Set to `None` (default) if no tracking is desired.

    :return:
        Existing or newly (re-)created resource Live Object State in a `dict`.

    :raises ValueError:
        Invalid `resCfg` input.
    :raises RuntimeError:
        Deployment creation failed.
    '''
    _ValResCfg(resCfg, {'namespace', 'name'}, {'image'}, 'Deployment')
    if ((resYAML := resCfg.yaml) is None):
        ns = resCfg.meta.get('namespace')
        resInput = {
            'subCmd': 'deployment', 'args': [resCfg.meta.name],
            'opts': {'image': resCfg.spec.image, **({'n': ns} if ns else {})},
        }
    else: resInput = resYAML
    return _ResCreate(oc, resInput, ownInf)
