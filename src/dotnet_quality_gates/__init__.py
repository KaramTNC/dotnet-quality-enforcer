"""Quality and coverage gates for C#/.NET repositories."""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("dotnet-quality-gates")
except PackageNotFoundError:
    # Source checkouts can be imported before installation.
    __version__ = "0.0.0+unknown"
