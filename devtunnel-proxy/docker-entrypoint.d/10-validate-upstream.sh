#!/bin/sh
set -eu

if ! printf '%s' "${DEV_TUNNEL_URL:-}" | grep -Eq '^https?://[A-Za-z0-9.-]+\.devtunnels\.ms(:[0-9]{1,5})?/?$'; then
  echo >&2 "DEV_TUNNEL_URL must be a Dev Tunnel HTTP(S) origin."
  exit 1
fi

export DEV_TUNNEL_URL="${DEV_TUNNEL_URL%/}"
