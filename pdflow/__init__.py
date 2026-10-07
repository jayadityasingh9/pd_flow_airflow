"""pdflow — Raven PD flow (RTL build -> Fusion Compiler route_opt) driven by Airflow.

Plain Python; only airflow_ops.py imports Airflow, so everything else is unit-testable and
usable from the CLI (bin/pdflow.sh).
"""
