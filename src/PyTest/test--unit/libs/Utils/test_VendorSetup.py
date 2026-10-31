'''Unit tests for libs.Utils.VendorSetup.'''
from __future__ import annotations

from pathlib import Path

import pytest

import libs.Utils.VendorSetup as MUT


def test_SrcEnvFromMarkexpr_Krkn() -> None:
    assert (MUT.SrcEnvFromMarkexpr('krkn') == 'KRKN_SRC')
    assert (MUT.SrcEnvFromMarkexpr('krkn and not slow') == 'KRKN_SRC')


def test_SrcEnvFromMarkexpr_VmBr() -> None:
    assert (MUT.SrcEnvFromMarkexpr('vm__br') == 'BR_SRC')


def test_SrcEnvFromMarkexpr_Empty() -> None:
    assert (MUT.SrcEnvFromMarkexpr('') is None)
    assert (MUT.SrcEnvFromMarkexpr('vm') is None)


def test_SrcEnvFromMarkexpr_RejectsBoth() -> None:
    with pytest.raises(RuntimeError, match='One vendor'):
        MUT.SrcEnvFromMarkexpr('krkn or vm__br')


def test_VendorFromMarkexpr_ReturnsPair() -> None:
    assert (MUT.VendorFromMarkexpr('krkn') == ('KRKN_SRC', 'krkn'))
    assert (
        MUT.VendorFromMarkexpr('vm__br') == ('BR_SRC', 'benchmark-runner')
    )


def test_VendorPython_Path() -> None:
    py = MUT.VendorPython('krkn')
    assert (py.name == 'python')
    assert (py.parent.name == 'bin')
    assert ('vendor' in py.parts)
    assert ('krkn' in py.parts)


def test_ResolveBasePython_Prefers311() -> None:
    base = MUT.ResolveBasePython()
    # Must be a usable 3.11 binary (name or absolute path).
    assert base
    out = __import__('subprocess').check_output(
        [base, '-c', 'import sys; print("%d.%d" % sys.version_info[:2])'],
        text=True,
    ).strip()
    assert (out == '3.11')


def test_MaybeReexecPytest_SkipsWhenAlreadyInVenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    venvRoot = tmp_path / '.venv'
    (venvRoot / 'bin').mkdir(parents=True)
    vendorPy = venvRoot / 'bin' / 'python'
    vendorPy.write_text('', encoding='utf-8')
    monkeypatch.setattr(MUT.sys, 'prefix', str(venvRoot))
    calls: list[tuple] = []
    monkeypatch.setattr(
        MUT.os, 'execv',
        lambda *a, **k: calls.append((a, k)),
    )
    MUT.MaybeReexecPytest(vendorPy)
    assert (calls == [])


def test_MaybeReexecPytest_ExecsVenvLauncherNotResolvedBase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    venvRoot = tmp_path / 'vendor' / 'krkn' / '.venv'
    (venvRoot / 'bin').mkdir(parents=True)
    vendorPy = venvRoot / 'bin' / 'python'
    vendorPy.write_text('', encoding='utf-8')
    monkeypatch.setattr(MUT.sys, 'prefix', '/unrelated')
    monkeypatch.setattr(MUT.sys, 'argv', ['pytest', '-m', 'krkn'])
    calls: list[list[str]] = []

    def fakeExecv(path: str, args: list[str]) -> None:
        calls.append([path, *args])
        raise SystemExit(0)

    monkeypatch.setattr(MUT.os, 'execv', fakeExecv)
    with pytest.raises(SystemExit):
        MUT.MaybeReexecPytest(vendorPy)
    assert (len(calls) == 1)
    # Must exec the venv path string, not a resolved base interpreter.
    assert (calls[0][0] == str(vendorPy))
    assert (calls[0][1:] == [str(vendorPy), '-m', 'pytest', '-m', 'krkn'])


def test_ResolveSrcDir_UsesEnv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    req = tmp_path / 'requirements.txt'
    req.write_text('pytest\n', encoding='utf-8')
    monkeypatch.setenv('KRKN_SRC', str(tmp_path))
    assert (MUT.ResolveSrcDir('KRKN_SRC') == tmp_path.resolve())


def test_ResolveSrcDir_MissingEnv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('BR_SRC', raising=False)
    with pytest.raises(EnvironmentError, match='BR_SRC'):
        MUT.ResolveSrcDir('BR_SRC')
