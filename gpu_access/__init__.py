"""Privileged GPU enforcement service.

This package deliberately does not import Django. The root service has a narrow PostgreSQL
contract and never evaluates application-owned strings as commands.
"""
