import pytest


class TestChaos():
    '''Pending Krkn Hub VM resiliency and host-observation scenarios.'''

    @pytest.mark.krkn
    @pytest.mark.skip(
        reason='Krkn Hub support for the new design is pending.',
    )
    def test_VMsurvivesVirtLauncherKill(self) -> None:
        '''Placeholder for launcher-Pod disruption and VM/VMI recovery.'''

    @pytest.mark.krkn
    @pytest.mark.skip(
        reason='Krkn Hub support for the new design is pending.',
    )
    def test_HostSideKernelScanOnVMnode(self) -> None:
        '''Placeholder for host-side kernel and dmesg observation.'''
