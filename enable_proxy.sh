#!/usr/bin/env sh

# Enable proxy variables for this terminal session.
# Preferred usage:
#   source ./enable_proxy.sh
#
# Direct execution also works:
#   ./enable_proxy.sh
# It will open a new interactive shell with these proxy variables enabled.

PROXY_URL="${PROXY_URL:-http://127.0.0.1:7890}"
NO_PROXY_LIST="${NO_PROXY_LIST:-localhost,127.0.0.1,::1,*.local}"

redact_proxy_url() {
  case "$1" in
    *://*@*)
      printf '%s\n' "$1" | sed 's#^\(.*://\)[^@]*@#\1***@#'
      ;;
    *)
      printf '%s\n' "$1"
      ;;
  esac
}

export HTTP_PROXY="$PROXY_URL"
export HTTPS_PROXY="$PROXY_URL"
export ALL_PROXY="$PROXY_URL"

export http_proxy="$PROXY_URL"
export https_proxy="$PROXY_URL"
export all_proxy="$PROXY_URL"

export NO_PROXY="$NO_PROXY_LIST"
export no_proxy="$NO_PROXY_LIST"

echo "Proxy enabled:"
echo "  HTTP_PROXY=$(redact_proxy_url "$HTTP_PROXY")"
echo "  HTTPS_PROXY=$(redact_proxy_url "$HTTPS_PROXY")"
echo "  ALL_PROXY=$(redact_proxy_url "$ALL_PROXY")"
echo "  NO_PROXY=$NO_PROXY"

if ! (return 0 2>/dev/null); then
  echo
  echo "Opened a new shell with proxy enabled. Run 'exit' to return."
  exec "${SHELL:-/bin/zsh}" -i
fi
