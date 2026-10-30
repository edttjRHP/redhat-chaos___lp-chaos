'''Offline boundaries for resource unit tests.'''
import socket
import subprocess
from unittest import mock

import pytest

from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.Utils.CmdExec import CmdExec


@pytest.fixture(autouse=True)
def OfflineResources(monkeypatch: pytest.MonkeyPatch) -> None:
    denied = mock.Mock(side_effect=AssertionError('External access forbidden'))
    monkeypatch.setattr(CmdExec, 'Run', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(socket.socket, 'connect', denied)


@pytest.fixture(name='resourceCLI')
def ResourceCLI() -> mock.MagicMock:
    return mock.create_autospec(OCcli, instance=True)
