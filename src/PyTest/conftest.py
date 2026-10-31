import logging
import time
from typing import Any


logging.Formatter.converter = time.gmtime


pytest_plugins = [
    'fixtures.CLI',
    'fixtures.VM',
    'fixtures.Krkn',
    'fixtures.BenchmarkRunner',
]


def pytest_configure(config: Any) -> None:
    '''
    Ensure a per-vendor venv for ``-m krkn`` / ``-m vm__br``, then re-exec
    under that interpreter so fixtures stay in-process.
    '''
    from libs.Utils.VendorSetup import MaybeReexecPytest, SetupFromMarkexpr

    markexpr = getattr(config.option, 'markexpr', '') or ''
    vendorPy = SetupFromMarkexpr(markexpr)
    if (vendorPy is not None):
        MaybeReexecPytest(vendorPy)
