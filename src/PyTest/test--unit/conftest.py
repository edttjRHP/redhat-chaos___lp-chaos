'''Shared fixtures for the isolated unit suite.'''

from collections.abc import Iterator
from unittest import mock

import pytest

from libs.OpenShift.LP.Virt.CLI.VirtCtl import VirtCtlCLI
from libs.OpenShift.LP.Virt.VM.VM import VM
from libs.OpenShift.OCP.CLI.OC import OCcli
import Utils


@pytest.fixture(name='virtualMachine')
def VirtualMachine() -> Iterator[Utils.VMcontext]:
    '''
    Yield a VM wrapper with mocked command clients.

    :yield: VM wrapper and its mocked OpenShift clients.
    '''
    oc = mock.create_autospec(OCcli, instance=True)
    oc.GetResUID.return_value = (True, 'cluster-one', '')
    virtCtl = mock.create_autospec(VirtCtlCLI, instance=True)
    yield Utils.VMcontext(
        vm=VM('vm-one', 'namespace', oc=oc, virtCtl=virtCtl),
        oc=oc,
        virtCtl=virtCtl,
    )
