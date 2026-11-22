#!/bin/bash
typeset _scrName="${BASH_SOURCE[0]##*/}"
typeset _scrPath="$(p="${BASH_SOURCE[0]}"; [ "${p:0:1}" = / ] || p="./${p}"; CDPATH= \command cd -L -- "${p%/*}/" 2> /dev/null;\command pwd -L)"
set -euxo pipefail; shopt -s inherit_errexit

typeset -i devInst="${1:-0}"; (($#)) && shift
typeset -i mode="${1:--1}"; (($#)) && shift

typeset e=
typeset -i doInst=1
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
    typeset fxtID=
    cd "${_scrPath}/"
    for e in "${_scrPath}/requirements--fxtLibs--"*'.txt'; do
        reqArgs+=("-r" "${e}")
    done
    pip install -U "${reqArgs[@]}"
    for e in "${_scrPath}/requirements--fxtVenv--"*'.txt'; do
        { [ -e "${instPath}" ] && [ -d "${instPath}" ]; } && {
            case ${mode} in
            (-1)  continue;;
            (0)   doInst=0;;
            (1)   rm -rf "${vEnvRootArr[${fxtID}]}/";;
            esac
        }
        fxtID="${e##*--}"; fxtID="${fxtID%.txt}"
        ((doInst)) && python3 -m venv --clear "${vEnvRootArr[${fxtID}]}/"
        "${vEnvRootArr[${fxtID}]}/bin/pip" install -r "${e}"
    done
    for e in \
        "${_scrPath}/setup--fxt--"*'.sh' ${mode} \
    ; do
        bash "${e}"
    done
true )

true