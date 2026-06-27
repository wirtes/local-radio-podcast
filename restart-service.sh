#!/usr/bin/env bash
set -euo pipefail

service_name="${1:-local-radio-podcast}"

echo "Stopping ${service_name}..."
sudo systemctl stop "${service_name}"

echo "Starting ${service_name}..."
sudo systemctl start "${service_name}"

echo
sudo systemctl --no-pager --lines=20 status "${service_name}"
