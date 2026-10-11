import logging
from pathlib import Path
import time

import pytest

from libs.OpenShift.LP.Virt.VM.VM import VM


logs = logging.getLogger()


class TestBSOD():
    '''BSOD tests'''

    @pytest.mark.vm
    @pytest.mark.parametrize(
        'vmCreate,',
        [({'ns': 'bsod-test', 'namePfx': 'bsod-2022'},)],
        indirect=True,
    )
    def test_VMcreate(self, vmCreate):
        '''Create a VM then test its connectiviy via SSH.'''
        testVM = vmCreate[0]
        logs.info(f'VM name: {testVM.name}')
        logs.info(f'SSH target: {testVM.ssh.target}')

        start = time.perf_counter()
        result = testVM.ssh.RunPowerShell(
            'Get-ComputerInfo | Select-Object CsName'
        )
        logs.info(f'Execution time: {time.perf_counter()-start}')
        assert result.success
        logs.info(result.stdout)

        start = time.perf_counter()
        result = testVM.ssh.Send(
            Path('/Users/vvijay/scripts/guest/churn.ps1'),
            'C:/TEMP/',
        )
        logs.info(f'Execution time: {time.perf_counter()-start}')
        assert result.success

        start = time.perf_counter()
        result = testVM.ssh.Recv(
            Path('C:/TEMP/churn.ps1'),
            '/Users/vvijay/scripts/check',
        )
        logs.info(f'Execution time: {time.perf_counter()-start}')
        assert result.success

    @pytest.mark.vm__br
    @pytest.mark.parametrize('fxtVMbyBR,', [({'count': 2},)], indirect=True)
    def test_BenchmarkRunner(self, fxtVMbyBR):
        for vm in fxtVMbyBR.vmList:
            logs.info(f'VM name: {vm.name}')
            logs.info(f'SSH target: {vm.ssh.target}')
            # Ensure guest reachability.
            info = vm.ssh.RunPowerShell('Get-Service -Name sshd')
            assert info.success, f'The `virtctl ssh` failed: {info.stderr}'
            assert ('Running' in info.stdout), (
                f'The `sshd` Service is not Running on guest: {info.stdout!r}'
            )

        # Ensure the requested VMs are running.
        assert (len(fxtVMbyBR.vmList) == 2), (
            f'Expected 2 VMs created, got {len(fxtVMbyBR.vmList)}: '
            f'{[v.name for v in fxtVMbyBR.vmList]!r}'
        )
