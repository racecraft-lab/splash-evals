FROM --platform=linux/arm64/v8 python:3.11-slim-bookworm@sha256:bbc491ed39611eede47b1058ad4afb9ea957fb3bf4a7f1b442c6a5628ab93bdc

# Every wheel, including transitive dependencies, is staged before this build.
# The locked hashes and --no-index make missing, substituted, or unpinned
# artifacts fail closed without contacting a package index.
COPY control-plane-requirements.lock /tmp/control-plane-requirements.lock
COPY wheels/ /wheels/
RUN python -m pip install \
      --no-cache-dir \
      --no-index \
      --find-links=/wheels \
      --only-binary=:all: \
      --require-hashes \
      --requirement /tmp/control-plane-requirements.lock \
 && python -m pip check \
 && rm -rf /wheels /tmp/control-plane-requirements.lock

COPY --chown=65532:65532 runner.py /opt/racecraft/runner.py
COPY --chown=65532:65532 mini-swe-agent.yaml /opt/racecraft/mini-swe-agent.yaml
COPY --chown=65532:65532 scorer_entrypoint.py /opt/racecraft/scorer_entrypoint.py

# mini-SWE-agent initializes its config directory at import time. Keep that
# state inside the runtime's bounded /tmp tmpfs and suppress startup text so
# stdout remains a JSON-only RPC channel.
ENV MSWEA_GLOBAL_CONFIG_DIR=/tmp/mini-swe-agent \
    MSWEA_SILENT_STARTUP=1

USER 65532:65532
WORKDIR /work

# This is a build-time import/configuration proof. It performs no inference,
# Docker nesting, dataset access, or network operation.
RUN python -I /opt/racecraft/runner.py --smoke-check \
 && python -I -c 'import importlib.metadata as m; from pathlib import Path; from swebench.harness.grading import get_eval_report; from swebench.harness.utils import make_test_spec; assert m.version("swebench") == "5.0.2"; assert callable(get_eval_report) and callable(make_test_spec); compile(Path("/opt/racecraft/scorer_entrypoint.py").read_text(encoding="utf-8"), "/opt/racecraft/scorer_entrypoint.py", "exec")'
