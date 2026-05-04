#!/bin/bash
scp -r *.py manifest.json services.yaml strings.json translations root@homeassistant.local:/homeassistant/custom_components/ads_multi
