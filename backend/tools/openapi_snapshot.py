"""Write the committed OpenAPI snapshot.

    ..\\.venv\\Scripts\\python tools\\openapi_snapshot.py

The snapshot is what `tests/test_openapi_contract.py` compares against, so this
is the command to run -- deliberately, after reading the diff -- when an
endpoint is meant to change. Running it is how the change gets into review as a
diff of the API rather than as a diff of the file that happens to define it.

Separate from the test so the test never writes. A snapshot test that updates
its own expectation when it fails is a test that cannot fail.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "tests" / "openapi.json"


def schema() -> dict:
    sys.path.insert(0, str(ROOT))
    # Importing app.main runs no lifespan, so nothing starts -- but the scan
    # flag is set anyway, in case that ever stops being true.
    os.environ.setdefault("KAI_NO_BACKGROUND_SCAN", "1")
    from app.main import app

    return app.openapi()


def serialise(document: dict) -> str:
    # Sorted keys and a trailing newline, so the diff is about the API rather
    # than about dictionary ordering between two Python versions.
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    SNAPSHOT.write_text(serialise(schema()), encoding="utf-8")
    print(f"wrote {SNAPSHOT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
