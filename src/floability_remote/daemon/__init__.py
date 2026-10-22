"""Authenticated loopback daemon shared by browser and MCP clients."""

API_PREFIX = "/api/v1"

# Increment this when an in-place package update changes the daemon contract in
# a way that an already-running process cannot provide.  The lifecycle manager
# uses it to replace an idle stale daemon instead of silently reusing it.
API_REVISION = 2
