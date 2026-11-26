#!/usr/bin/env bash
set -euo pipefail

target="${1:?需要镜像仓库地址}"
host="${target#https://}"
host="${host%%/*}"
if [[ -n "${2:-}" ]]; then
  if [[ -z "${MIRROR_TOKEN:-}" ]]; then
    echo "::error::请配置 $host 的仓库写入凭据"
    exit 1
  fi
  auth="$(printf '%s:%s' "$2" "$MIRROR_TOKEN" | base64 -w0)"
  echo "::add-mask::$auth"
  export GIT_CONFIG_COUNT=1
  export GIT_CONFIG_KEY_0="http.https://$host/.extraHeader"
  export GIT_CONFIG_VALUE_0="Authorization: Basic $auth"
  unset auth MIRROR_TOKEN
  if [[ "$host" == gitcode.com ]]; then
    export GIT_CONFIG_COUNT=2
    export GIT_CONFIG_KEY_1="http.https://gitcode.com/.userAgent"
  fi
fi

list_refs() {
  git ls-remote --refs "$1" 'refs/heads/*' 'refs/tags/*' | LC_ALL=C sort
}

up_to_date() {
  local upstream mirror
  upstream="$(list_refs origin)" && mirror="$(list_refs "$target")" &&
    [[ -n "$upstream" && "$upstream" == "$mirror" ]]
}

sync_refs() {
  local shallow=()
  if [[ "$(git rev-parse --is-shallow-repository)" == true ]]; then
    shallow=(--unshallow)
  fi
  git fetch "${shallow[@]}" --prune --no-tags origin \
    '+refs/heads/*:refs/azurpilot-mirror/heads/*' \
    '+refs/tags/*:refs/azurpilot-mirror/tags/*' &&
    git push "$target" \
      'refs/azurpilot-mirror/heads/*:refs/heads/*' \
      'refs/azurpilot-mirror/tags/*:refs/tags/*'
}

for attempt in 1 2 3 4 5; do
  if [[ "$host" == gitcode.com ]]; then
    export GIT_CONFIG_VALUE_1="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/$((120 + RANDOM % 20)).0.$RANDOM.0 Safari/537.36"
  fi
  if up_to_date || { sync_refs && up_to_date; }; then
    echo '已核对 GitHub 和镜像的全部分支、标签及提交'
    exit 0
  fi
  if (( attempt < 5 )); then
    echo "镜像同步或核对失败，准备第 $((attempt + 1))/5 次尝试"
    sleep "$((attempt * 2))"
  fi
done
echo '::error::镜像分支或标签仍与 GitHub 不一致，请检查网络和镜像侧独有提交'
exit 1
