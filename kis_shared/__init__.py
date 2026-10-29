"""Credential-free, cross-repository exclusive KIS session coordination."""
from .lease import Blocked, Lease

__all__ = ['Blocked', 'Lease']
