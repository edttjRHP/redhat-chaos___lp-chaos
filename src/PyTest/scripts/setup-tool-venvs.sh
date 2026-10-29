#!/usr/bin/env bash
# Create isolated virtualenvs for benchmark-runner and krkn.
#
# Defaults under src/PyTest/vendor/; sibling checkouts under ~/chaos (or
# parent of this repo) are used when vendor/ is empty.
#
# Env overrides:
#   PYTHON_BIN, BR_ROOT, BR_SRC, BR_PIP_SPEC, KRKN_SRC, KRKNHUB_ROOT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTEST_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# lp-chaos repo root, then its parent (e.g. ~/chaos).
REPO_ROOT="$(cd "${PYTEST_ROOT}/../.." && pwd)"
WORKSPACE_ROOT="$(cd "${REPO_ROOT}/.." && pwd)"

BR_ROOT="${BR_ROOT:-${PYTEST_ROOT}/vendor/benchmark-runner}"
BR_PIP_SPEC="${BR_PIP_SPEC:-benchmark-runner>=1.0.1064}"
BR_SRC="${BR_SRC:-}"
KRKN_SRC="${KRKN_SRC:-}"
KRKNHUB_ROOT="${KRKNHUB_ROOT:-}"

log() { printf '%s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

realpath_of() { readlink -f "$1"; }

pick_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    printf '%s\n' "${PYTHON_BIN}"
    return 0
  fi
  local candidate
  for candidate in python3.11 python3.12 python3.13 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  die "No suitable Python found. Set PYTHON_BIN."
}

require_python() {
  PYTHON_BIN="$(pick_python)"
  command -v "${PYTHON_BIN}" >/dev/null 2>&1 || \
    die "Python interpreter not found: ${PYTHON_BIN}"
  "${PYTHON_BIN}" -c \
    'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || \
    die "${PYTHON_BIN} must be Python 3.11 or newer"
  log "Using Python: ${PYTHON_BIN}"
}

# Symlink vendor/<name> -> real checkout when vendor entry is missing or a stub.
ensure_vendor_link() {
  local name="$1"
  local target="$2"
  local link_path="${PYTEST_ROOT}/vendor/${name}"
  local resolved
  resolved="$(realpath_of "${target}")"
  mkdir -p "${PYTEST_ROOT}/vendor"
  if [[ -L "${link_path}" ]]; then
    [[ "$(realpath_of "${link_path}")" == "${resolved}" ]] && return 0
    rm -f "${link_path}"
  elif [[ -d "${link_path}" ]]; then
    # Real checkout already in vendor/.
    [[ -e "${link_path}/requirements.txt" || -e "${link_path}/env.sh" ]] && \
      return 0
    # Empty / .venv-only stub from a failed earlier run.
    rm -rf "${link_path}"
  elif [[ -e "${link_path}" ]]; then
    die "Refusing to replace ${link_path}"
  fi
  [[ "${link_path}" == "${resolved}" ]] && return 0
  ln -s "${resolved}" "${link_path}"
  log "Linked ${link_path} -> ${resolved}"
}

resolve_paths() {
  local candidate

  if [[ -z "${KRKN_SRC}" ]]; then
    for candidate in \
        "${PYTEST_ROOT}/vendor/krkn" \
        "${WORKSPACE_ROOT}/krkn"; do
      if [[ -f "${candidate}/requirements.txt" && \
            -f "${candidate}/run_kraken.py" ]]; then
        KRKN_SRC="$(realpath_of "${candidate}")"
        break
      fi
    done
  else
    KRKN_SRC="$(realpath_of "${KRKN_SRC}")"
  fi
  [[ -n "${KRKN_SRC}" ]] || die \
    "No krkn checkout found. Set KRKN_SRC=/path/to/krkn"
  [[ -f "${KRKN_SRC}/requirements.txt" && -f "${KRKN_SRC}/run_kraken.py" ]] || \
    die "KRKN_SRC=${KRKN_SRC} missing requirements.txt or run_kraken.py"
  ensure_vendor_link krkn "${KRKN_SRC}"

  if [[ -z "${KRKNHUB_ROOT}" ]]; then
    for candidate in \
        "${PYTEST_ROOT}/vendor/krknHub" \
        "${WORKSPACE_ROOT}/krkn-hub"; do
      if [[ -f "${candidate}/env.sh" ]]; then
        KRKNHUB_ROOT="$(realpath_of "${candidate}")"
        break
      fi
    done
    KRKNHUB_ROOT="${KRKNHUB_ROOT:-${PYTEST_ROOT}/vendor/krknHub}"
  else
    KRKNHUB_ROOT="$(realpath_of "${KRKNHUB_ROOT}")"
  fi
  if [[ -f "${KRKNHUB_ROOT}/env.sh" ]]; then
    ensure_vendor_link krknHub "${KRKNHUB_ROOT}"
  else
    log "WARNING: krkn-hub missing at ${KRKNHUB_ROOT}"
  fi

  if [[ -n "${BR_SRC}" ]]; then
    BR_SRC="$(realpath_of "${BR_SRC}")"
    [[ -d "${BR_SRC}" ]] || die "BR_SRC does not exist: ${BR_SRC}"
  fi
  mkdir -p "${BR_ROOT}"
}

create_venv() {
  local root="$1"
  local venv_dir="${root}/.venv"
  mkdir -p "${root}"
  if [[ ! -x "${venv_dir}/bin/python" ]]; then
    log "Creating venv: ${venv_dir}"
    rm -rf "${venv_dir}"
    "${PYTHON_BIN}" -m venv "${venv_dir}" || die "Failed to create ${venv_dir}"
    "${venv_dir}/bin/python" -m pip install --upgrade pip
  else
    log "Reusing venv: ${venv_dir}"
  fi
  printf '%s\n' "${venv_dir}/bin/python"
}

venv_pip() {
  local python_bin="$1"
  shift
  [[ -x "${python_bin}" ]] || die "Missing interpreter: ${python_bin}"
  "${python_bin}" -m pip install "$@"
}

setup_benchmark_runner() {
  local python_bin
  python_bin="$(create_venv "${BR_ROOT}")"
  if [[ -n "${BR_SRC}" ]]; then
    log "Installing benchmark-runner editable from ${BR_SRC}"
    venv_pip "${python_bin}" -e "${BR_SRC}"
  else
    log "Installing benchmark-runner from ${BR_PIP_SPEC}"
    venv_pip "${python_bin}" "${BR_PIP_SPEC}"
  fi
}

setup_krkn() {
  local python_bin
  log "Using krkn source: ${KRKN_SRC}"
  python_bin="$(create_venv "${KRKN_SRC}")"
  log "Installing krkn requirements"
  venv_pip "${python_bin}" -r "${KRKN_SRC}/requirements.txt"
  # setup.py needs pkg_resources (setuptools<82); avoid isolated build env.
  log "Installing krkn editable (no build isolation)"
  venv_pip "${python_bin}" --no-build-isolation -e "${KRKN_SRC}"
}

main() {
  require_python
  resolve_paths
  setup_benchmark_runner
  setup_krkn
  log "Tool venvs ready."
  log "  benchmark-runner: ${BR_ROOT}/.venv"
  log "  krkn:             ${KRKN_SRC}/.venv"
  log "Fixture overrides (if not using vendor/ symlinks):"
  log "  export IENG_PYTEST____FXT__BR____DEF_FXT_CFG__ROOTPATH=${BR_ROOT}"
  log "  export IENG_PYTEST____FXT__KRKN____DEF_FXT_CFG__ROOTPATH_KRKN=${KRKN_SRC}"
  log "  export IENG_PYTEST____FXT__KRKN____DEF_FXT_CFG__ROOTPATH_KRKNHUB=${KRKNHUB_ROOT}"
}

main "$@"
