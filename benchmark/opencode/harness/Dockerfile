FROM ubuntu:24.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 python3-pip python3-venv git ca-certificates libjemalloc2 \
    && rm -rf /var/lib/apt/lists/*

# Copy a small wrapper that invokes the mounted entrypoint via bash
# (bind mounts preserve host permissions, so we need bash to execute it)
RUN printf '#!/bin/bash\nset -euo pipefail\nexec bash /entrypoint.sh "$@"\n' > /run.sh && chmod +x /run.sh

# All runtime files are mounted from host:
#   - uv, opencode at /usr/local/bin/
#   - entrypoint script at /entrypoint.sh  (MUST be mounted, no +x needed)
#   - config files at /config/
#   - linux source at /source/
#   - task JSON at /task.json
#   - results at /results/
#   - aide repo at /aide/
ENTRYPOINT ["/run.sh"]
