from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dash-license-scan")
except PackageNotFoundError:
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
