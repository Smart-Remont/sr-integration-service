"""Test environment. Runs before any ``src`` import: config reads env at import time."""

import os

os.environ.setdefault("INTEGRATION_HS_BI_USER", "hs_bi")
os.environ.setdefault("INTEGRATION_HS_BI_PASSWORD", "test-hs-bi-password")
os.environ["API_DOCS_ENABLED"] = "false"
