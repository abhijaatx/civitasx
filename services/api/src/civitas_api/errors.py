class CivitasError(Exception):
    """Base application error."""


class ConflictError(CivitasError):
    """Optimistic version conflict."""


class NotFoundError(CivitasError):
    """The authenticated owner cannot access the requested object."""


class OwnershipError(NotFoundError):
    """Deliberately indistinguishable from a missing private object."""


class RateLimitError(CivitasError):
    """A paid operation would exceed an admission limit."""
