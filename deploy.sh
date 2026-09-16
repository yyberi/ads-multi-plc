#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "$0")"

case "${1:-}" in
    --local-pyads)
        if [[ $# -gt 2 || ( $# -eq 2 && "$2" != --prepare-only ) ]]; then
            echo "Usage: $0 [--local-pyads [--prepare-only]]" >&2
            exit 1
        fi
        "${PYTHON_BIN:-python3}" scripts/prepare-pyads-deploy.py
        if [[ "${2:-}" == --prepare-only ]]; then
            exit 0
        fi
        cd .pyads-deploy/ads_multi
        # SSH add-on exposes HA's /config as /homeassistant.
        ssh root@homeassistant.local 'mkdir -p /homeassistant/custom_components/ads_multi'
        # Upload the wheel before the manifest that references it.
        scp -r wheels *.py services.yaml strings.json translations root@homeassistant.local:/homeassistant/custom_components/ads_multi
        scp manifest.json root@homeassistant.local:/homeassistant/custom_components/ads_multi/manifest.json
        echo "Deployed with local amd64 pyads 3.6.0 wheel. Restart Home Assistant Core to install it."
        exit 0
        ;;
    "") ;;
    *) echo "Usage: $0 [--local-pyads [--prepare-only]]" >&2; exit 1 ;;
esac

cd custom_components/ads_multi

scp -r *.py manifest.json services.yaml strings.json translations root@homeassistant.local:/homeassistant/custom_components/ads_multi
# scp -r *.py manifest.json services.yaml strings.json translations tero@127.0.0.1:18123:/homeassistant/custom_components/ads_multi
