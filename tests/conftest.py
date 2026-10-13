"""Test isolation: point the app at a throwaway database BEFORE any app
module is imported. app.database opens ``settings.db_path`` at import time,
so without this the suite created/migrated the real data/optimizarr.db."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="optimizarr-tests-")
os.environ["OPTIMIZARR_DB_PATH"] = os.path.join(_tmp, "optimizarr.db")
# ...and logs/statistics too, so test failures never land in the real logs/
os.environ["OPTIMIZARR_LOG_DIR"] = os.path.join(_tmp, "logs")
# ...and an empty content-classifier runtime, so tests never run the real model
os.environ["OPTIMIZARR_CLIP_DIR"] = os.path.join(_tmp, "clip")
