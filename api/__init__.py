"""Personal OS API package.

Making `api` a package means running from the repo root with absolute imports
(`from api.database import ...`) rather than flat imports that only resolve when the
working directory happens to be `api/`. Costs one empty file; removes a class of
"works on my machine" import errors.
"""
