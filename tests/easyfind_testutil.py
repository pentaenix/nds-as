"""Helpers for EasyFind tests."""
from __future__ import annotations

from rae.easyfind.models import EasyFindDocument
from rae.easyfind.node_index import attach_bucket_lookup


def finalize_easyfind_document(document: EasyFindDocument) -> EasyFindDocument:
    """Attach required baked lookup tables before save/load tests."""
    return attach_bucket_lookup(document)
