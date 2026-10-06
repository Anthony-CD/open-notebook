"""Shared sha256 helper for source duplicate detection.

Centralized so the pre-flight check in ``api/routers/sources.py`` (text/
upload sources, whose content is known before the async pipeline runs) and
the post-extraction hash computed in ``open_notebook/graphs/source.py``
(link sources, whose content is only known after content-core extracts it)
always agree on how a hash is derived from the same bytes/text.

Backward compatibility note: sources created before this field existed have
``content_hash = None``. ``None`` is never passed to :func:`compute_content_hash`
by callers - they skip hashing/dup-checks entirely for falsy content - so
legacy sources are never flagged as duplicates of each other (or of anything
else) until both sides have a real hash. There is no backfill job that reads
old file bytes to retrofit a hash.
"""

from __future__ import annotations

import hashlib
from typing import Union


def compute_content_hash(data: Union[str, bytes]) -> str:
    """Return the hex sha256 digest of ``data``.

    Strings are encoded as UTF-8 before hashing. Used for both raw uploaded
    file bytes and extracted text/URL content - callers decide which bytes
    are the right dedup signal for a given source type.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
