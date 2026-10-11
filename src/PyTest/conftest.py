import logging
import time


logging.Formatter.converter = time.gmtime


pytest_plugins = [
    'fixtures.CLI',
    'fixtures.VM',
    'fixtures.KrknLib',
    'fixtures.BenchmarkRunner',
]
