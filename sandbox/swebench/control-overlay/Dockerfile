FROM local/swebench-control@sha256:21a8cc29bc180fa4b5460c0f52d1bd8030b44ddf2c00158d0de18fa15b8f7297

COPY --chown=0:0 --chmod=0444 scorer_entrypoint.py /opt/racecraft/scorer_entrypoint.py

# Prove the overlay still contains the reviewed source and the pinned official
# scorer APIs without running a benchmark or performing inference.
RUN python -I -B -c 'import hashlib, importlib.metadata as metadata; from pathlib import Path; from swebench.harness.grading import get_eval_report; from swebench.harness.utils import make_test_spec; source = Path("/opt/racecraft/scorer_entrypoint.py").read_bytes(); assert metadata.version("swebench") == "5.0.2"; assert hashlib.sha256(source).hexdigest() == "e313d7fdf0ff500562cddaccd4feada8d9cba4c8b4adf838685fd893e0896858"; compile(source.decode("utf-8"), "/opt/racecraft/scorer_entrypoint.py", "exec"); assert callable(get_eval_report) and callable(make_test_spec)'
