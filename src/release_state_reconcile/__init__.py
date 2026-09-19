"""Deterministic, read-only GitHub release-state reconciliation."""

from .reconcile import reconcile_candidate

__all__ = ["reconcile_candidate"]
