'''Common models shared across fixture modules.'''
import os

from fixtures.libs.CommonConstant import LPQE__DEF__NAMESPACE
from libs.CustomTypes import NamedDict


LPQE__DEF__FXT_CFG = NamedDict(
    kCfg=(
        os.getenv('KUBECONFIG') or
        os.path.expanduser('~/.kube/config')
    ),
    # The `--kubeconfig` option will be overridden by the above `.kCfg`.
    cliCfg=NamedDict(
        ocCfg={},
    ),
    ns=LPQE__DEF__NAMESPACE,
    fxtInf=NamedDict(),
    testPars=NamedDict(),
)
