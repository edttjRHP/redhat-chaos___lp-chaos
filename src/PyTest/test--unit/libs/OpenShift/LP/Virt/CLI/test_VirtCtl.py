from unittest import mock

import pytest

import libs.OpenShift.LP.Virt.CLI.VirtCtl as MUT
import Utils


def test_Init_DelegatesToBase(monkeypatch: pytest.MonkeyPatch) -> None:
    Utils.AutoPatch(monkeypatch, MUT.GoCobraCLI, '__init__')
    MUT.GoCobraCLI.__init__.return_value = None
    executor = mock.MagicMock()

    MUT.VirtCtlCLI(
        binExec='custom-virtctl',
        cmdExec=executor,
        namespace='ns',
    )

    MUT.GoCobraCLI.__init__.assert_called_once_with(
        mock.ANY,
        'custom-virtctl',
        cmdExec=executor,
        namespace='ns',
    )
