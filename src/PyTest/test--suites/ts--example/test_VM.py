import os
import re
from typing import TYPE_CHECKING

import pytest

from libs.CustomTypes import NamedDict


if TYPE_CHECKING:
    from fixtures.VM import FxtObjVM


class TestVM():
    '''Template-based VM creation and guest connectivity example.'''

    @pytest.mark.vm
    @pytest.mark.parametrize(
        'fxtVM,',
        [({
            'ns': 'vm-test',
            'namePfx': 'vm-example',
            'gstCfg': NamedDict(imgSrc=NamedDict(CDI=NamedDict._TypeReplace(
                NamedDict(
                    type='AWS-S3',
                    credType='None',
                    url=os.getenv(
                        (
                            'IENG_PYTEST____TS__EXAMPLE__VM____'
                            'FXT_CFG__GSTCFG_IMGSRC_CDI_URL'
                        ),
                        (
                            'https://s3.us-east-1.amazonaws.com/'
                            'ieng--vm-image--windows--us-east-1/'
                            'win11/dist/win11-25H2--template--base.qcow2'
                        ),
                    ),
                ),
            ))),
        },)],
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
