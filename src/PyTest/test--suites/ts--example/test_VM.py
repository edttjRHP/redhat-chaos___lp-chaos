from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    from fixtures.VM import FxtObjVM


class TestVM():
    '''Template-based VM creation and guest connectivity example.'''

    @pytest.mark.vm
    @pytest.mark.parametrize(
        'fxtVM,',
        [({'ns': 'vm-test', 'namePfx': 'vm-example'},)],
        indirect=True,
    )
    def test_VMcreate(self, fxtVM: 'FxtObjVM') -> None:
        '''Create one VM, start it, and check a PowerShell response via SSH.'''
        assert (len(fxtVM.vmList) == 1)
        testVM = fxtVM.vmList[0]
        assert testVM.Start()

        result = testVM.ssh.RunPowerShell('Write-Output vm-example-ready\n')
        assert result.success
        assert (result.stdout.strip() == 'vm-example-ready')
