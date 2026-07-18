"""Native Qt view components for run_app.py (Decision Center, Candidates Workbench).

These modules are pure UI: read-only over existing output/ artifacts.
They never mutate scoring, validation, adaptive weights, history writers or
portfolio-selection logic. They rely on core.candidate_selection for the
canonical ordering authority.
"""
