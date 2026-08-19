"""HTTP surface for UC-07.

Thin by design: each endpoint authorises through the service, translates a result object into a
response model, and does nothing else. **No frontend is built here** (§4) — these are the service
contracts a future coaching UI will call.
"""
