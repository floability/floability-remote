"""Local web interface and versioned JSON API for floability-remote.

Importing this package does not import FastAPI; `floability-remote web` loads
`floability_remote.web.server` only when the web command runs.
"""

API_PREFIX = "/api/v1"
