'''Ensure one vendor venv per marker from ``KRKN_SRC`` / ``BR_SRC``.'''
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


logs = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
# Vendors (krkn/BR) pin packages without py3.14 wheels; prefer 3.11.
_VENDOR_PYTHON = os.environ.get('VENDOR_PYTHON', 'python3.11')
# marker → (source env var, vendor/ subdir name)
_MARKER_TO_VENDOR = {
    'krkn': ('KRKN_SRC', 'krkn'),
    'vm__br': ('BR_SRC', 'benchmark-runner'),
}


def SrcEnvFromMarkexpr(markexpr: str) -> Optional[str]:
    '''
    Map pytest ``-m`` to a source env var name.

    ``krkn`` → ``KRKN_SRC``, ``vm__br`` → ``BR_SRC``.
    '''
    vendor = VendorFromMarkexpr(markexpr)
    if (vendor is None):
        return None
    return vendor[0]


def VendorFromMarkexpr(markexpr: str) -> Optional[tuple[str, str]]:
    '''
    Map pytest ``-m`` to ``(srcEnv, vendorName)``.

    Returns ``None`` when no vendor marker is selected.
    '''
    expr = (markexpr or '').strip()
    if not expr:
        return None

    matched = [
        (envName, vendorName)
        for (marker, (envName, vendorName)) in _MARKER_TO_VENDOR.items()
        if re.search(rf'\b{re.escape(marker)}\b', expr)
    ]
    if not matched:
        return None
    uniq = list({item for item in matched})
    if (len(uniq) > 1):
        raise RuntimeError(
            'One vendor at a time: do not combine -m krkn and -m vm__br '
            f'(got markexpr={expr!r})'
        )
    return uniq[0]


def ResolveSrcDir(srcEnv: str) -> Path:
    '''Resolve checkout path from ``KRKN_SRC`` or ``BR_SRC``.'''
    raw = (os.environ.get(srcEnv) or '').strip()
    if not raw:
        raise EnvironmentError(
            f'{srcEnv} is not set. Export it to the vendor checkout, e.g. '
            f'export {srcEnv}=/path/to/'
            f'{"krkn" if srcEnv == "KRKN_SRC" else "benchmark-runner"}'
        )
    srcDir = Path(raw).expanduser().resolve()
    req = srcDir / 'requirements.txt'
    if not req.is_file():
        raise FileNotFoundError(
            f'{srcEnv}={srcDir} has no requirements.txt'
        )
    return srcDir


def VendorDir(vendorName: str) -> Path:
    '''``src/PyTest/vendor/<vendorName>``.'''
    return (_ROOT / 'vendor' / vendorName)


def VendorPython(vendorName: str) -> Path:
    '''Interpreter for ``vendor/<vendorName>/.venv``.'''
    return (VendorDir(vendorName) / '.venv' / 'bin' / 'python')


def ResolveBasePython() -> str:
    '''
    Interpreter used to *create* vendor venvs.

    Prefer ``VENDOR_PYTHON`` / ``python3.11`` so pins like ``numpy==1.26.4``
    get wheels. Do not use the framework ``.venv`` when it is 3.12+.
    '''
    candidates = [_VENDOR_PYTHON, 'python3.11', sys.executable]
    seen: set[str] = set()
    for cand in candidates:
        if (not cand) or (cand in seen):
            continue
        seen.add(cand)
        try:
            out = subprocess.check_output(
                [cand, '-c', 'import sys; print("%d.%d" % sys.version_info[:2])'],
                text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            continue
        if (out == '3.11'):
            return cand
    raise EnvironmentError(
        'Need python3.11 to create vendor venvs (krkn/BR pins need 3.11 '
        'wheels). Install python3.11 or set VENDOR_PYTHON=/path/to/python3.11'
    )


def _VenvVersion(py: Path) -> Optional[str]:
    '''Return ``major.minor`` for an existing venv python, else ``None``.'''
    try:
        return subprocess.check_output(
            [str(py), '-c', 'import sys; print("%d.%d" % sys.version_info[:2])'],
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def EnsureVenv(vendorName: str) -> Path:
    '''Create ``vendor/<vendorName>/.venv`` (Python 3.11); return its python.'''
    venvDir = (VendorDir(vendorName) / '.venv')
    py = VendorPython(vendorName)
    if py.is_file():
        ver = _VenvVersion(py)
        if (ver != '3.11'):
            logs.warning(
                'Recreating %s (was Python %s; need 3.11)', venvDir, ver,
            )
            shutil.rmtree(venvDir)
    if not py.is_file():
        basePy = ResolveBasePython()
        logs.info('Creating venv %s with %s', venvDir, basePy)
        venvDir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.check_call([basePy, '-m', 'venv', str(venvDir)])
    if not py.is_file():
        raise FileNotFoundError(f'venv python missing: {py}')
    return py


def InstallIntoVenv(srcEnv: str, vendorName: str) -> Path:
    '''
    Install framework + checkout requirements (editable) into the vendor venv.

    Returns the vendor interpreter path.
    '''
    srcDir = ResolveSrcDir(srcEnv)
    py = EnsureVenv(vendorName)
    logs.info(
        'Installing into %s from %s=%s', py, srcEnv, srcDir,
    )
    subprocess.check_call(
        [str(py), '-m', 'pip', 'install', '-U', 'pip'],
    )
    subprocess.check_call(
        [str(py), '-m', 'pip', 'install', '-r', str(_ROOT / 'requirements.txt')],
    )
    subprocess.check_call(
        [
            str(py), '-m', 'pip', 'install',
            '-r', str(srcDir / 'requirements.txt'),
        ],
    )
    editable = [str(py), '-m', 'pip', 'install']
    if (srcEnv == 'KRKN_SRC'):
        editable.append('--no-build-isolation')
    editable.extend(['-e', str(srcDir)])
    subprocess.check_call(editable)
    logs.info('Ready: vendor=%s python=%s', vendorName, py)
    return py


def SetupFromMarkexpr(markexpr: str) -> Optional[Path]:
    '''
    Ensure the vendor venv for ``markexpr`` and return its python.

    ``None`` when the markexpr needs no vendor (e.g. ``-m vm``).
    '''
    vendor = VendorFromMarkexpr(markexpr)
    if (vendor is None):
        return None
    (srcEnv, vendorName) = vendor
    return InstallIntoVenv(srcEnv, vendorName)


def MaybeReexecPytest(vendorPy: Path) -> None:
    '''
    Re-exec pytest under ``vendorPy`` when this process is a different
    interpreter (keeps fixtures in-process inside the vendor venv).

    Do **not** ``Path.resolve()`` the venv ``python`` before exec: it often
    symlinks to a base install (e.g. uv's cpython), and exec'ing that path
    leaves the venv so ``pytest`` is missing.
    '''
    venvRoot = vendorPy.parent.parent.resolve()  # .../.venv
    if (Path(sys.prefix).resolve() == venvRoot):
        return
    launcher = str(vendorPy)
    logs.info('Re-exec pytest under vendor venv: %s', launcher)
    os.execv(launcher, [launcher, '-m', 'pytest', *sys.argv[1:]])
