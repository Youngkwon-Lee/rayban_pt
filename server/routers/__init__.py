"""HTTP endpoints for the rayban local bridge, grouped by domain.

Each module exposes a ``router`` that :mod:`app` includes.  Routers read
mutable configuration and process state through ``bridge_core`` so that
runtime overrides are observed by every caller.
"""
