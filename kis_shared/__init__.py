"""Shared account coordination; physical KIS I/O always passes the fence."""
from .lease import Blocked, Lease
from .boundary import install
install()
__all__=["Blocked","Lease"]
