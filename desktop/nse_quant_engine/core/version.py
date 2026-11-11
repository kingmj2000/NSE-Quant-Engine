"""Single source of the application version.

The app version labels a release of the software (UI, pipeline, tooling). It is
NOT the ranking schema version: `ranking_schema_version` (currently 2) in
validation_status.json / score_history.csv only changes when the official
scoring formula changes, and doing so resets the evidence clock. A UI or tooling
release never touches it.
"""
APP_NAME = "NSE Quant Engine"
APP_VERSION = "5.0"
RELEASE_DATE = "2026-11-11"
