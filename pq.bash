#!/bin/bash

error_out() {
    echo "$1" > "${RESPONSE}"
    popd &> /dev/null
    [[ -e ${REPOPATH} ]] && rm -rf "${REPOPATH}"
    [[ -e ${PATCHFILE} ]] && rm "${PATCHFILE}"
    [[ -e ${TMPFILE} ]] && rm "${TMPFILE}"
    echo "WKRESPONSE:${RESPONSE}"
    exit $2
}

patch=$1
repo=$2
REPOPATH=$(mktemp -uq)
RESPONSE=$(mktemp -uq)
PATCHFILE=$(mktemp -uq)
TMPFILE=$(mktemp -uq)
PUSH_ERR_FILE=$(mktemp -uq)

#DRYRUN="--dry-run"
DRYRUN=""

if ! [[ $(ssh -x -p 29418 Philantrop@galileo.mailstation.de gerrit ls-projects | grep "^${repo}$") ]]; then
    error_out "Repository not in Gerrit" 0
fi

[[ -e ${REPOPATH} ]] && rm -rf "${REPOPATH}"
mkdir "${REPOPATH}"

pushd "${REPOPATH}"
git clone ssh://Philantrop@galileo.mailstation.de:29418/"${repo}".git

if ! [[ -d "${repo}" ]]; then
    error_out "Cloning failed" 3
fi

cd "${repo}"
wget "${patch}" -O "${PATCHFILE}"

if ! [[ -s ${PATCHFILE} ]]; then
    error_out "Fetching ${patch} failed" 4
fi

MSG=${PATCHFILE}

sed -i -e "/^---$/i\\\nWKWKWKWK:" "${PATCHFILE}"
sed -i -e "/WKWKWKWK:/iPatch-URL: ${patch}" "${PATCHFILE}"

clean_message=`sed -e '
                /^diff --git a\/.*/{
                        s///
                        q
                }
                /^Signed-off-by:/d
                /^#/d
        ' "$MSG" | git stripspace`

_gen_ChangeIdInput() {
    echo "tree `git write-tree`"
    if parent=`git rev-parse "HEAD^0" 2>/dev/null`
    then
        echo "parent $parent"
    fi
    echo "author `git var GIT_AUTHOR_IDENT`"
    echo "committer `git var GIT_COMMITTER_IDENT`"
    echo
    printf '%s' "$clean_message"
}
_gen_ChangeId() {
    _gen_ChangeIdInput |
    git hash-object -t commit --stdin
}

CHGID=$(_gen_ChangeId)

sed -i -e "s;WKWKWKWK:;Change-Id: I${CHGID};" "${PATCHFILE}"

if [[ $(git am --quiet ${PATCHFILE} 2>${TMPFILE} ; grep "." ${TMPFILE}) ]]; then
    error_out "Applying ${patch} failed" 5
fi

rm "${PATCHFILE}"

git push ${DRYRUN} --quiet origin HEAD:refs/for/master &> "${PUSH_ERR_FILE}"
rc=$?

if [[ ${rc} == 0 ]]; then
    echo "Pushed successfully to Gerrit" > "${RESPONSE}"
else
    error_out "Push to Gerrit failed" 6
fi

popd

rm -rf "${REPOPATH}"

echo "WKRESPONSE:${RESPONSE}"
