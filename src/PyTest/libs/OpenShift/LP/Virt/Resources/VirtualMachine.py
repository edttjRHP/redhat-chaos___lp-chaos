'''OpenShift Virtualization VirtualMachine resource handler.'''
from typing import Any, Optional

from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.OpenShift.OCP.Resources import OwnInf, ResCfg, _ResCreate, _ValResCfg


def Create(
    oc: OCcli,
    resCfg: ResCfg,
    ownInf: Optional[OwnInf] = None,
    optsNewRes: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    '''
    Create or reuse a VirtualMachine from an authoritative YAML manifest.

    :param oc:          CLI client for the target OpenShift cluster.
    :param resCfg:      Resource configuration with `yaml` set.
    :param ownInf:      Optional ownership records for newly created VMs.
    :param optsNewRes:  Creation options, including wait condition and timeout.

    :return:            Existing or newly created resource live state.

    :raises ValueError: YAML is absent or has a different resource kind.
    :raises RuntimeError: Resource creation or resolution failed.
    '''
    if (resCfg.yaml is None):
        raise ValueError('VirtualMachine creation requires YAML.')
    _ValResCfg(resCfg, set(), set(), 'VirtualMachine')
    return _ResCreate(oc, resCfg.yaml, ownInf, optsNewRes=optsNewRes)
