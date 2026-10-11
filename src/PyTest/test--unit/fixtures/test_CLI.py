from unittest import mock

import pytest

import fixtures.CLI as MUT
import Utils


def test_FxtOCcli_UsesDefaultAndCustomOptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'OCcli')
    factory = MUT.FxtOCcli.__wrapped__()
    cmdExec = mock.MagicMock(name='cmdExec')

    first = factory()
    second = factory(
        ns='custom',
        binExec='custom-oc',
        cmdExec=cmdExec,
        context='context',
        kubeconfig='/kube',
    )

    assert first is MUT.OCcli.return_value
    assert second is MUT.OCcli.return_value
    assert MUT.OCcli.call_args_list == [
        mock.call(n='default'),
        mock.call(
            n='custom',
            binExec='custom-oc',
            cmdExec=cmdExec,
            context='context',
            kubeconfig='/kube',
        ),
    ]

    with pytest.raises(TypeError, match='multiple values'):
        factory(ns='custom', n='duplicate')


def test_FxtVirtCtlCLI_UsesDefaultAndCustomOptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT, 'VirtCtlCLI')
    factory = MUT.FxtVirtCtlCLI.__wrapped__()
    cmdExec = mock.MagicMock(name='cmdExec')

    first = factory()
    second = factory(
        ns='custom',
        binExec='custom-virtctl',
        cmdExec=cmdExec,
        context='context',
        kubeconfig='/kube',
    )

    assert first is MUT.VirtCtlCLI.return_value
    assert second is MUT.VirtCtlCLI.return_value
    assert MUT.VirtCtlCLI.call_args_list == [
        mock.call(n='default'),
        mock.call(
            n='custom',
            binExec='custom-virtctl',
            cmdExec=cmdExec,
            context='context',
            kubeconfig='/kube',
        ),
    ]

    with pytest.raises(TypeError, match='multiple values'):
        factory(ns='custom', n='duplicate')


def test_CLIfactories_AreRegisteredWithModuleScope() -> None:
    assert MUT.FxtOCcli.name == 'fxtOCcli'
    assert MUT.FxtOCcli._fixture_function_marker.scope == 'module'
    assert MUT.FxtVirtCtlCLI.name == 'fxtVirtCtlCLI'
    assert MUT.FxtVirtCtlCLI._fixture_function_marker.scope == 'module'
