"""Providers — the only layer that knows how an external service actually works.

Everything above depends on the Protocols in api/services.py. This is where
filesystem paths, REST endpoints, and auth live, and where they stop.
"""
