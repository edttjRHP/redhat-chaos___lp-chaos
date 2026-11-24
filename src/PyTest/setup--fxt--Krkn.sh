#!/bin/bash
# Install Krkn and KrknHub for the `fxtKrkn` Fixture.
#
# Download the public `main` branch archives (no authentication) and extract
#   them into the Fixture install paths. Relative paths are resolved from this
#   script directory.
#
# Arg.:
#   mode    Policy if existing installation exist.
#           Supported values:
#             -1:   Skip it (default).
#              0:   Keep the existing installation and re-install its
#                   dependencies.
#              1:   Force a clean install.
#
# Env. Var.:
#   IENG_PYTEST____FXT__KRKN____DEF_FXT_CFG__ROOTPATH_KRKN
#       Krkn install path (default: `vendor/krkn`).
#   IENG_PYTEST____FXT__KRKN____DEF_FXT_CFG__ROOTPATH_KRKNHUB
#       KrknHub install path (default: `vendor/krknHub`).
#
# Requires:
#   envsubst    GNU `envsubst` on `PATH` (used by `fxtKrkn`), checked before
#               any download.
typeset _scrName="${BASH_SOURCE[0]##*/}"
typeset _scrPath="$(p="${BASH_SOURCE[0]}"; [ "${p:0:1}" = / ] || p="./${p}"; CDPATH= \command cd -L -- "${p%/*}/" 2> /dev/null;\command pwd -L)"
set -euxo pipefail; shopt -s inherit_errexit

typeset -i mode="${1:--1}"; (($#)) && shift

typeset e= instPath= srcURL='https://github.com/krkn-chaos'
typeset -i doInst=0
typeset -A instRootArr=(
    [krkn]="${IENG_PYTEST____FXT__KRKN____DEF_FXT_CFG__ROOTPATH_KRKN:-${_scrPath}/vendor/krkn}"
    [krkn-hub]="${IENG_PYTEST____FXT__KRKN____DEF_FXT_CFG__ROOTPATH_KRKNHUB:-${_scrPath}/vendor/krknHub}"
)

case ${mode} in (-1|0|1);; (*)false;; esac

# The `fxtKrkn` renders KrknHub templates with GNU `envsubst`. Only command
#   presence is checked here, before any download.
command -v envsubst 1> /dev/null || {
    echo 'The `envsubst` not found on `PATH`; install GNU `envsubst` as per '\
'`docs/guide--Krkn.md`.' 1>&2
    exit 1
}

for e in "${!instRootArr[@]}"; do
    instPath="${instRootArr[${e}]%/}"
    doInst=1
    [ "${instPath::1}" = / ] || instPath="${_scrPath}/${instPath}"
    { [ -e "${instPath}" ] && [ -d "${instPath}" ]; } && {
        case ${mode} in
          (-1)  continue;;
          (0)   doInst=0;;
          (1)   rm -rf "${instPath}/";;
        esac
    }
    mkdir -p -- "${instPath%/*}"
    ((doInst)) && (
        # Extract into a temp. dir., then rename it into place, so a failed
        #   download or extraction never leaves a partial install behind.
        typeset tmpDir=
        trap 'rm -rf -- "${tmpDir}"' EXIT
        tmpDir="$(mktemp -d -- "${instPath}.tmp.XXXXXX")"
        {

            curl -fsSL "${srcURL}/${e}/archive/refs/heads/main.tar.gz" |
            tar -xzf - --strip-components=1 -C "${tmpDir}/"
        }
        mv -T -- "${tmpDir}" "${instPath}"
    true )
    {
        [ -r "${instPath}/requirements.txt" ] &&
        pip install -U -r "${instPath}/requirements.txt"
    }
done

true
