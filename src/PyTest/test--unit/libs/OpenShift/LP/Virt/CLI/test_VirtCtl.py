from typing import Any
from unittest import mock

import pytest

import libs.OpenShift.LP.Virt.CLI.VirtCtl as MUT


@pytest.mark.parametrize(
    'constructorArgs,expectedBinExec,expectedCmdExec,expectedDefOpts',
    [
        ({}, 'virtctl', None, {}),
        (
            {
                'binExec': 'custom-virtctl',
                'cmdExec': mock.sentinel.executor,
                'namespace': 'ns',
            },
            'custom-virtctl',
            mock.sentinel.executor,
            {'namespace': 'ns'},
        ),
    ],
)
def test_Init_ForwardsArgumentsToBase(
    monkeypatch: pytest.MonkeyPatch,
    constructorArgs: dict[str, Any],
    expectedBinExec: str,
    expectedCmdExec: Any,
    expectedDefOpts: dict[str, Any],
) -> None:
    calls: list[tuple[Any, str, Any, dict[str, Any]]] = []

    def RecordInit(
        self: MUT.GoCobraCLI,
        binExec: str,
        cmdExec: Any = None,
        **defOpts: Any,
    ) -> None:
        calls.append((self, binExec, cmdExec, defOpts))

    monkeypatch.setattr(MUT.GoCobraCLI, '__init__', RecordInit)
    cli = MUT.VirtCtlCLI(**constructorArgs)

    assert (calls == [
        (cli, expectedBinExec, expectedCmdExec, expectedDefOpts),
    ])


def test_Run_InheritsBaseImplementation() -> None:
    assert (MUT.VirtCtlCLI.Run is MUT.GoCobraCLI.Run)
