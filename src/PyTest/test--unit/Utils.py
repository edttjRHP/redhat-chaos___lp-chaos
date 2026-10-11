'''Shared unit-test helpers.'''

from typing import Any, NamedTuple
from unittest import mock

import pytest

from libs.OpenShift.LP.Virt.VM.RHOVssh import RHOVsshCon
from libs.OpenShift.LP.Virt.VM.VM import VM


class RHOVconnectionContext(NamedTuple):
    connection: RHOVsshCon
    virtCtl: mock.MagicMock
    cmdExec: mock.MagicMock

class VMcontext(NamedTuple):
    vm: VM
    oc: mock.MagicMock
    virtCtl: mock.MagicMock


def AutoPatch(
    monkeypatch: pytest.MonkeyPatch,
    target: Any,
    name: str,
) -> None:
    '''Replace `target.name` with an autospecced mock of that attribute.

    `target` is intentionally dynamic because patch namespaces may be modules,
    classes, or instances.
    '''
    spec = mock.create_autospec(getattr(target, name))
    monkeypatch.setattr(target, name, spec)
