from types import SimpleNamespace
from unittest import mock

import pytest

import libs.OpenShift.OCP.Resources.Namespace as MUT


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('useYAML', [False, True])
def test_Create_ForwardsNativeOrAuthoritativeYAML(
    resourceCLI: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tracked: bool,
    useYAML: bool,
) -> None:
    text = 'kind: Namespace\nmetadata:\n  name: native-one\n'
    cfg = (
        SimpleNamespace(meta=object(), spec=None, yaml=text) if useYAML
        else SimpleNamespace(
            meta=SimpleNamespace(name='native-one'), spec={}, yaml=None,
        )
    )
    producer = (
        text if useYAML
        else {'subCmd': 'namespace', 'args': ['native-one']}
    )
    resource = {'kind': 'Namespace', 'metadata': {'name': 'native-one'}}
    ownInf = object() if tracked else None
    validate = mock.Mock(return_value=None)
    create = mock.Mock(return_value=resource)
    monkeypatch.setattr(MUT, '_ValResCfg', validate)
    monkeypatch.setattr(MUT, '_ResCreate', create)

    assert (MUT.Create(resourceCLI, cfg, ownInf) is resource)
    validate.assert_called_once_with(cfg, {'name'}, set(), 'Namespace')
    create.assert_called_once_with(resourceCLI, producer, ownInf)
    resourceCLI.NewRes.assert_not_called()
    resourceCLI.EnsureRes.assert_not_called()


@pytest.mark.parametrize('cfg', [
    SimpleNamespace(
        meta={'name': 'one', 'namespace': 'not-native'}, spec={}, yaml=None,
    ),
    SimpleNamespace(meta={}, spec={'finalizers': []}, yaml=None),
    SimpleNamespace(meta={}, spec={}, yaml='kind: Secret'),
])
def test_Create_PropagatesValidationFailureBeforeCreation(
    resourceCLI: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    cfg: SimpleNamespace,
) -> None:
    failure = ValueError('invalid Namespace configuration')
    validate = mock.Mock(side_effect=failure)
    create = mock.Mock()
    monkeypatch.setattr(MUT, '_ValResCfg', validate)
    monkeypatch.setattr(MUT, '_ResCreate', create)

    with pytest.raises(ValueError) as caught:
        MUT.Create(resourceCLI, cfg)

    assert (caught.value is failure)
    validate.assert_called_once_with(cfg, {'name'}, set(), 'Namespace')
    create.assert_not_called()
    resourceCLI.NewRes.assert_not_called()
    resourceCLI.EnsureRes.assert_not_called()


def test_Create_PropagatesCreationFailure(
    resourceCLI: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = SimpleNamespace(
        meta=SimpleNamespace(name='native-one'), spec={}, yaml=None,
    )
    failure = RuntimeError('resource creation failed')
    validate = mock.Mock(return_value=None)
    create = mock.Mock(side_effect=failure)
    monkeypatch.setattr(MUT, '_ValResCfg', validate)
    monkeypatch.setattr(MUT, '_ResCreate', create)

    with pytest.raises(RuntimeError) as caught:
        MUT.Create(resourceCLI, cfg)

    assert (caught.value is failure)
    create.assert_called_once_with(
        resourceCLI, {'subCmd': 'namespace', 'args': ['native-one']}, None,
    )
    resourceCLI.NewRes.assert_not_called()
    resourceCLI.EnsureRes.assert_not_called()
