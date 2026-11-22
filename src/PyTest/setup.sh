#!/bin/bash
# Arg.:
#   MODE    Policy if existing installation exist.
#           Supported values:
#            -1 Skip it (default).
#             0 Keep the existing installation and re-install its dependencies.
#             1 Force a clean install.
typeset _scrName="${BASH_SOURCE[0]##*/}"
typeset _scrPath="$(p="${BASH_SOURCE[0]}"; [ "${p:0:1}" = / ] || p="./${p}"; CDPATH= \command cd -L -- "${p%/*}/" 2> /dev/null;\command pwd -L)"
set -euxo pipefail; shopt -s inherit_errexit

typeset -i devInst="${1:-0}"; (($#)) && shift
typeset -i mode="${1:--1}"; (($#)) && shift

typeset e=
typeset -a reqArgs=(
    "-r" "${_scrPath}/requirements--main.txt"
    "-r" "${_scrPath}/requirements--unit.txt"
)
typeset -A vEnvRootArr=(
    [BenchmarkRunner]=\
"${IENG_PYTEST____FXT__BENCHMARKRUNNER____DEF_FXT_CFG__ROOTPATH_BR:-\
vendor/BenchmarkRunner}"
)

case ${mode} in (-1|0|1);; (*)false;; esac
# If Prod. Install is requested, remove the `requirements--unit.txt`
((devInst)) || unset 'reqArgs[-1]' 'reqArgs[-1]'

( shopt -s nullglob
    typeset fxtID= instPath=
    typeset -i doInst=0
    cd "${_scrPath}/"
    for e in "${_scrPath}/requirements--fxtLibs--"*'.txt'; do
        reqArgs+=("-r" "${e}")
    done
    pip install -U "${reqArgs[@]}"
    for e in "${_scrPath}/requirements--fxtVenv--"*'.txt'; do
        fxtID="${e##*--}"; fxtID="${fxtID%.txt}"
        instPath="${vEnvRootArr[${fxtID}]}"
        doInst=1
        { [ -e "${instPath}" ] && [ -d "${instPath}" ]; } && {
            case ${mode} in
            (-1)  continue;;
            (0)   doInst=0;;
            (1)   rm -rf "${instPath}/";;
            esac
        }
        ((doInst)) && python3 -m venv --clear "${instPath}/"
        "${instPath}/bin/pip" install -r "${e}"
    done
    for e in \
        "${_scrPath}/setup--fxt--"*'.sh' \
    ; do
        bash "${e}" ${mode}
    done
true )

true