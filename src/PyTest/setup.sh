#!/bin/bash
typeset _scrName="${BASH_SOURCE[0]##*/}"
typeset _scrPath="$(p="${BASH_SOURCE[0]}"; [ "${p:0:1}" = / ] || p="./${p}"; CDPATH= \command cd -L -- "${p%/*}/" 2> /dev/null;\command pwd -L)"
set -euxo pipefail; shopt -s inherit_errexit

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

( shopt -s nullglob
    typeset fxtID=
    cd "${_scrPath}/"
    for e in "${_scrPath}/requirements--fxtLibs--"*'.txt'; do
        reqArgs+=("-r" "${e}")
    done
    pip install -U "${reqArgs[@]}"
    for e in "${_scrPath}/requirements--fxtVenv--"*'.txt'; do
        fxtID="${e##*--}"; fxtID="${fxtID%.txt}"
        python3 -m venv --clear "${vEnvRootArr[${fxtID}]}"
        "${vEnvRootArr[${fxtID}]}/bin/pip" install -r "${e}"
    done
true )

true