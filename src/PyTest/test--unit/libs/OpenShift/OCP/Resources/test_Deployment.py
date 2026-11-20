from typing import Optional
from unittest import mock

import pytest

from libs.CustomTypes import NamedDict
import libs.OpenShift.OCP.Resources.Deployment as MUT


vldMeta = {'namespace', 'name'}
vldSpec = {'image'}


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('namespace', [None, '', 'apps'])
def test_Create_ForwardsNativeSubCommand(
    resourceCLI: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tracked: bool,
    namespace: Optional[str],
) -> None:
    meta = NamedDict(
        name='web', **({} if (namespace is None) else {'namespace': namespace})
    )
    cfg = MUT.ResCfg(meta=meta, spec=NamedDict(image='registry/img'))
    resource = {'kind': 'Deployment', 'metadata': {'name': 'web'}}
    ownInf = MUT.OwnInf(owned=[]) if tracked else None
    validate = mock.Mock(return_value=None)
    create = mock.Mock(return_value=resource)
    monkeypatch.setattr(MUT, '_ValResCfg', validate)
    monkeypatch.setattr(MUT, '_ResCreate', create)

    assert (MUT.Create(resourceCLI, cfg, ownInf) is resource)
    validate.assert_called_once_with(cfg, vldMeta, vldSpec, 'Deployment')
    create.assert_called_once_with(
        resourceCLI,
        {
            'subCmd': 'deployment', 'args': ['web'],
            'opts': {
                'image': 'registry/img',
                **({'n': namespace} if namespace else {}),
            },
        },
        ownInf,
    )
    resourceCLI.NewRes.assert_not_called()
    resourceCLI.EnsureRes.assert_not_called()


@pytest.mark.parametrize('tracked', [False, True])
def test_Create_ForwardsAuthoritativeYAML(
    resourceCLI: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tracked: bool,
) -> None:
    text = 'kind: Deployment\nmetadata:\n  name: web\n'
    # Authoritative YAML ignores the native `meta` and `spec`.
    cfg = MUT.ResCfg(
        meta=NamedDict(labels={}), spec=NamedDict(replicas=2), yaml=text,
    )
    resource = {'kind': 'Deployment', 'metadata': {'name': 'web'}}
    ownInf = MUT.OwnInf(owned=[]) if tracked else None
    validate = mock.Mock(return_value=None)
    create = mock.Mock(return_value=resource)
    monkeypatch.setattr(MUT, '_ValResCfg', validate)
    monkeypatch.setattr(MUT, '_ResCreate', create)

    assert (MUT.Create(resourceCLI, cfg, ownInf) is resource)
    validate.assert_called_once_with(cfg, vldMeta, vldSpec, 'Deployment')
    create.assert_called_once_with(resourceCLI, text, ownInf)
    resourceCLI.NewRes.assert_not_called()
    resourceCLI.EnsureRes.assert_not_called()


@pytest.mark.parametrize(('cfg', 'match'), [
    (
        MUT.ResCfg(
            meta=NamedDict(name='web', labels={}),
            spec=NamedDict(image='registry/img'),
        ),
        r"Unsupported `resCfg\.meta` keys found: \{'labels'\}",
    ),
    (
        MUT.ResCfg(
            meta=NamedDict(name='web'),
            spec=NamedDict(image='registry/img', replicas=2),
        ),
        r"Unsupported `resCfg\.\w+` keys found: \{'replicas'\}",
    ),
    (
        MUT.ResCfg(yaml='kind: Secret\nmetadata:\n  name: web\n'),
        r'Expected `Deployment`, got `Secret`\.',
    ),
])
def test_Create_RejectsInvalidConfigurationBeforeCreation(
    resourceCLI: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    cfg: MUT.ResCfg,
    match: str,
) -> None:
    create = mock.Mock()
    monkeypatch.setattr(MUT, '_ResCreate', create)
    owned = []

    with pytest.raises(ValueError, match=match):
        MUT.Create(resourceCLI, cfg, MUT.OwnInf(owned=owned))

    create.assert_not_called()
    assert (owned == [])
    resourceCLI.NewRes.assert_not_called()
    resourceCLI.EnsureRes.assert_not_called()


def test_Create_PropagatesCreationFailure(
    resourceCLI: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = MUT.ResCfg(
        meta=NamedDict(name='web'), spec=NamedDict(image='registry/img'),
    )
    failure = RuntimeError('resource creation failed')
    create = mock.Mock(side_effect=failure)
    monkeypatch.setattr(MUT, '_ResCreate', create)

    with pytest.raises(RuntimeError) as caught:
        MUT.Create(resourceCLI, cfg)

    assert (caught.value is failure)
    create.assert_called_once_with(
        resourceCLI,
        {
            'subCmd': 'deployment', 'args': ['web'],
            'opts': {'image': 'registry/img'},
        },
        None,
    )
    resourceCLI.NewRes.assert_not_called()
    resourceCLI.EnsureRes.assert_not_called()
