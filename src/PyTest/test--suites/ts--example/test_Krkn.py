from typing import TYPE_CHECKING

import pytest

from libs.CustomTypes import NamedDict
from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.OpenShift.OCP.Resources import (
    OwnInf, ResCfg,
    GetUIDs,
    Deployment, Namespace,
)

if TYPE_CHECKING:
    from fixtures.Krkn import FxtObjKrkn


_POD_SCN__NS = 'krkn--pod--test'
_POD_SCN__APP = 'krkn--pod--example'
# The native `Deployment.Create` labels its Pods with `app=<name>`.
_POD_SCN__LABEL = f'app={_POD_SCN__APP}'
_POD_SCN__CTR_IMG = 'registry.access.redhat.com/ubi9/httpd-24'


class TestKrkn():
    '''KrknHub Pod Scenario example and pending VM/host scenarios.'''

    @pytest.mark.krkn
    @pytest.mark.parametrize(
        'fxtKrkn,',
        [({
            'ns': _POD_SCN__NS,
            'fxtInf': NamedDict(krknCfg=NamedDict(krknEnv={
                # Exact Namespace (regex) and the test-owned Pod label only.
                'NAMESPACE': f'^{_POD_SCN__NS}$',
                'POD_LABEL': _POD_SCN__LABEL,
                'DISRUPTION_COUNT': '1',
            })),
        },)],
        indirect=True,
    )
    def test_OwnedPodRecoversFromPodKill(self, fxtKrkn: 'FxtObjKrkn') -> None:
        '''
        Kill the test-owned Pod via KrknHub `pod-scenarios` and check that its
        Deployment replaces it.
        '''
        oc = fxtKrkn.cli.oc
        deployRef = f'Deployment/{_POD_SCN__APP}'
        waitOpts = {
            'timeout': '3m',
            'for': 'jsonpath={.status.readyReplicas}=1',
        }
        owned = []
        try:
            Namespace.Create(
                oc,
                ResCfg(meta=NamedDict(name=_POD_SCN__NS)),
                OwnInf(owned=owned),
            )
            assert (len(owned) == 1), 'Refusing a pre-existing Namespace.'
            Deployment.Create(
                oc,
                ResCfg(
                    meta=NamedDict(namespace=_POD_SCN__NS, name=_POD_SCN__APP),
                    spec=NamedDict(image=_POD_SCN__CTR_IMG),
                ),
                OwnInf(owned=owned),
            )
            assert (len(owned) == 2), 'Refusing a pre-existing Deployment.'
            assert oc.Run('wait', deployRef, **waitOpts).success
            result = oc.Run('get', 'Pods', l=_POD_SCN__LABEL, o='json')
            assert result.success
            oldUIDs = GetUIDs(result.stdout)
            assert (len(oldUIDs) == 1)

            with pytest.raises(SystemExit) as excInfo:
                fxtKrkn.krknHub.Run('pod-scenarios')
            assert (excInfo.value.code == 0)

            # The killed Pod must be replaced: one Ready replica, none of the
            #   non-terminating target Pods may keep the original UID.
            assert oc.Run('wait', deployRef, **waitOpts).success
            result = oc.Run('get', 'pods', l=_POD_SCN__LABEL, o='json')
            assert result.success
            newUIDs = GetUIDs(result.stdout)
            assert (len(newUIDs) == 1)
            assert not (newUIDs & oldUIDs)
        finally:
            OCcli.CleanUpRes(owned[::-1])
