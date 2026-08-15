class ScraperError(RuntimeError):
    """Base exception for scraper failures."""


class ScraperRequestError(ScraperError):
    """Raised when a request cannot be completed within the configured retry policy."""


class AccessRestrictedError(ScraperRequestError):
    """Raised when the site presents an access-control or anti-bot response."""


class ResultsStructureError(ScraperError):
    """Raised when a results page no longer matches the observed table contract."""


class PaginationError(ScraperError):
    """Raised when pagination loops or exceeds the configured page bound."""
