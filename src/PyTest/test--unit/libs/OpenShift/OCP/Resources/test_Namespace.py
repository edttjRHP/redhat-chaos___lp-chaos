from unittest import mock

import pytest

from libs.CustomTypes import NamedDict
from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.OpenShift.OCP.Resources import OwnInf, ResCfg
import libs.OpenShift.OCP.Resources.Namespace as MUT
from libs.Utils.CmdExec import CmdRes


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('useYAML', [False, True])
def test_Create_ForwardsNativeOrAuthoritativeYAML(
    resourceCLI: mock.MagicMock, tracked: bool, useYAML: bool,
) -> None:
    text = 'kind: Namespace\nmetadata:\n  name: native-one\n'
    cfg = (
        ResCfg(meta=object(), spec=None, yaml=text) if useYAML
        else ResCfg(meta=NamedDict(name='native-one'))
    )
    producer = (
        text if useYAML
        else {'subCmd': 'namespace', 'args': ['native-one']}
    )
    resource = {'kind': 'Namespace', 'metadata': {'name': 'native-one'}}
    resourceCLI.NewRes.return_value = (
        OCcli.ResPhase.APPLIED, CmdRes('', '', 0, True), resource,
    )
    resourceCLI.EnsureRes.return_value = resource
    owned = []
    assert (MUT.Create(
        resourceCLI, cfg, (OwnInf(owned) if tracked else None),
    ) is resource)
    if tracked:
        resourceCLI.EnsureRes.assert_called_once_with(
            producer, owned, isSecret=False, existResDelTO='5m',
        )
    else:
        resourceCLI.NewRes.assert_called_once_with(producer, isSecret=False)


@pytest.mark.parametrize('cfg', [
    ResCfg(meta=NamedDict(name='one', namespace='not-native')),
    ResCfg(spec=NamedDict(finalizers=[])),
    ResCfg(yaml='kind: Secret'),
])
def test_Create_RejectsInvalidConfigBeforeLifecycle(
    resourceCLI: mock.MagicMock, cfg: ResCfg,
) -> None:
    with pytest.raises(ValueError): MUT.Create(resourceCLI, cfg)
    resourceCLI.NewRes.assert_not_called()
    resourceCLI.EnsureRes.assert_not_called()
