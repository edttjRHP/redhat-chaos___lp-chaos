import subprocess
import sys
from unittest import mock

import pytest

import libs.OpenShift.LP.Virt.CLI.VirtCtl as MUT
from libs.Utils.CmdExec import CmdRes
import Utils


@pytest.mark.parametrize('isSecret', [False, True])
@pytest.mark.parametrize('text', [False, True])
def test_Run_InheritedExecutorPreservesRealProcessStdinAndResults(
    isSecret: bool, text: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    cli = MUT.VirtCtlCLI(sys.executable)
    caplog.clear()
    payload = 'synthetic-stdin' if text else b'synthetic-stdin'
    result = cli.Run(
        '-c',
        'import sys; '
        'sys.stdout.buffer.write(sys.stdin.buffer.read()); '
        'sys.stderr.buffer.write(b"synthetic-stderr")',
        input=payload, isSecret=isSecret,
        spPars={'text': text, 'timeout': 5},
    )
    assert (result == CmdRes(
        payload, 'synthetic-stderr' if text else b'synthetic-stderr',
        0, True,
    ))
    assert (caplog.text == '')


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


@pytest.mark.parametrize('isSecret', [False, True])
def test_Run_InheritedSecretPolicyPreservesNativeExecution(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    isSecret: bool,
) -> None:
    assert (MUT.VirtCtlCLI.Run is MUT.GoCobraCLI.Run)
    Utils.AutoPatch(monkeypatch, subprocess, 'run')
    subprocess.run.return_value = subprocess.CompletedProcess(
        [], 0, '      --namespace string\n', '',
    )
    executor = Utils.RecordingExec()
    cli = MUT.VirtCtlCLI(
        'virtctl-unit', cmdExec=executor, namespace='selected',
    )
    subprocess.run.reset_mock()
    executor.inputs.clear()
    subprocess.run.return_value = subprocess.CompletedProcess(
        [], 9, b'synthetic-raw-output', b'synthetic-raw-error',
    )

    result = cli.Run(
        'ssh', 'vm/name', input=b'synthetic-stdin', isSecret=isSecret,
        spPars={'text': False, 'timeout': 4},
    )

    assert (result == CmdRes(
        b'synthetic-raw-output', b'synthetic-raw-error', 9, False,
    ))
    subprocess.run.assert_called_once_with(
        ['virtctl-unit', '--namespace=selected', 'ssh', 'vm/name'],
        input=b'synthetic-stdin', capture_output=True, shell=False,
        text=False, timeout=4,
    )
    assert (executor.inputs == ([] if isSecret else [b'synthetic-stdin']))
    if isSecret:
        for value in ('synthetic-raw-output', 'synthetic-raw-error',
                      'synthetic-stdin', 'vm/name', 'selected'):
            assert (value not in caplog.text)
        assert ('Command failed with exit code 9 (Try 1/1)' in caplog.text)
    else: assert ('synthetic-raw-error' in caplog.text)
