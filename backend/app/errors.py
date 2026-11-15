"""Errors shared by the research readers and the API (main.py maps InvalidRequest, and only it, to HTTP 422)."""


class InvalidRequest(ValueError):
    """The request cannot be answered as asked: an invalid parameter or rule, or data the request needs that does not exist
    (no exported history, too few seasons). Any other exception is an internal failure (HTTP 500), never a bad request."""
