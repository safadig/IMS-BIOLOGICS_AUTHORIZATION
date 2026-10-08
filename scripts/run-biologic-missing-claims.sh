#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /opt/ims_router
exec /usr/bin/flock -n /opt/ims_router/biologic_missing_claims.lock \
  /opt/ims_router_venv/bin/python /opt/ims_router/biologic_missing_claims.py --apply "$@"
