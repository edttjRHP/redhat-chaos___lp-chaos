'''Shared fixtures for the isolated unit suite.'''

from collections.abc import Iterator
from unittest import mock

import pytest

from libs.OpenShift.LP.Virt.CLI.VirtCtl import VirtCtlCLI
from libs.OpenShift.LP.Virt.VM.RHOVssh import RHOVsshCon
from libs.OpenShift.LP.Virt.VM.VM import VM
from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.Utils.CmdExec import CmdExec
import Utils


@pytest.fixture(name='rhovConnection')
def RHOVconnection() -> Iterator[Utils.RHOVconnectionContext]:
    virtCtl = mock.create_autospec(VirtCtlCLI, instance=True)
    cmdExec = mock.create_autospec(CmdExec, instance=True)
    yield Utils.RHOVconnectionContext(
        connection=RHOVsshCon(
            ns='namespace',
            host='vm-one',
            user='user',
            idFile='~/key',
            ctrlPersist='2h',
            virtCtl=virtCtl,
            cmdExec=cmdExec,
        ),
        virtCtl=virtCtl,
        cmdExec=cmdExec,
    )


@pytest.fixture(name='virtualMachine')
def VirtualMachine() -> Iterator[Utils.VMcontext]:
    oc = mock.create_autospec(OCcli, instance=True)
    virtCtl = mock.create_autospec(VirtCtlCLI, instance=True)
    yield Utils.VMcontext(
        vm=VM('vm-one', 'namespace', oc=oc, virtCtl=virtCtl),
        oc=oc,
        virtCtl=virtCtl,
    )
