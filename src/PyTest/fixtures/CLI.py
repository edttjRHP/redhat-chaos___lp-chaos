'''CLI tool fixtures - factory producers for Cobra-based CLI wrappers.'''
from collections.abc import Callable
from typing import Any

import pytest

from fixtures.libs.CommonConstant import LPQE__DEF__NAMESPACE
from libs.OpenShift.LP.Virt.CLI.VirtCtl import VirtCtlCLI
from libs.OpenShift.OCP.CLI.OC import OCcli


@pytest.fixture(name='fxtOCcli', scope='module')
def FxtOCcli() -> Callable[..., OCcli]:
    '''
    Module-scoped `oc` CLI factory.

    Call with keyword options to configure defaults for the returned instance.
    Defaults to `n=LPQE__DEF__NAMESPACE` if not overridden.
    '''
    def _make(
        ns: str = LPQE__DEF__NAMESPACE,
        **defOpts: Any,
    ) -> OCcli:
        return OCcli(n=ns, **defOpts)
    return _make


@pytest.fixture(name='fxtVirtCtlCLI', scope='module')
def FxtVirtCtlCLI() -> Callable[..., VirtCtlCLI]:
    '''
    Module-scoped `virtctl` CLI factory.

    Call with keyword options to configure defaults for the returned instance.
    Defaults to `n=LPQE__DEF__NAMESPACE` if not overridden.
    '''
    def _make(
        ns: str = LPQE__DEF__NAMESPACE,
        **defOpts: Any,
    ) -> VirtCtlCLI:
        return VirtCtlCLI(n=ns, **defOpts)
    return _make
