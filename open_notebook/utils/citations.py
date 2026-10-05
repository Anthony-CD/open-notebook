"""Structured citation support for chat, source-chat and ask.

Replaces the earlier approach (model emits raw record IDs like
``[source:abc123]`` inline, frontend regex-extracts them from the raw answer
text). That mechanism is fragile: the model can hallucinate an ID, cite
something that was never actually retrieved, or skip citing altogether with
no way to tell the two cases apart.

Instead:

1. Build a numbered :class:`Reference` list from whatever was actually
   retrieved for this turn (``build_references_from_notebook_context``,
   ``build_references_from_source_context``, or ``build_references_from_search_results``).
2. Render it with :func:`render_reference_list` and inject that into the
   prompt instead of a raw blob, so the model sees "[1] <title>, <location>:
   <snippet>" entries and is instructed to cite using the bracket number.
3. After the model answers, :func:`parse_cited_references` extracts which
   ``[n]`` numbers actually appear in the answer and returns the matching
   ``Reference`` objects in first-occurrence order — this is the structured
   citation list the API returns and the frontend renders as chips.

Location granularity is honest about what's actually available:
- Full source text / notes: no location (whole-document citation only) —
  there is no page or chunk index tracked for these in the DB.
- Insights: ``location`` is the insight type (e.g. "Insight: summary").
- Ask (vector search results): no location beyond "semantic search match" —
  the vector-search SQL function groups/aggregates by document id and does
  not surface the embedding chunk's ``order`` field.

This is a prompting mitigation, not a hallucination guarantee: the model can
still ignore the instruction, cite a number that doesn't support the
sentence next to it, or (per the honesty instruction) say it lacks evidence
when it shouldn't. Only the *shape* of citations — that each one maps to a
real retrieved item - is structurally guaranteed; the semantic correctness
of "this [n] actually supports this sentence" is not.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

SNIPPET_MAX_CHARS = 400

# Matches bracketed citation numbers like "[1]" or "[12]" - deliberately not
# the old "[source:abc123]" pattern, which this module replaces.
_CITATION_NUMBER_PATTERN = re.compile(r"\[(\d+)\]")


class Reference(BaseModel):
    """One numbered entry in the reference list shown to the model."""

    number: int
    ref_type: str  # "source" | "source_insight" | "note"
    ref_id: str  # full record id, e.g. "source:abc123"
    title: str
    location: Optional[str] = None
    snippet: str


def _snippet(text: Optional[str], limit: int = SNIPPET_MAX_CHARS) -> str:
    if not isinstance(text, str):
        return ""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit].rstrip() + "…"


def _renumber(references: List[Reference]) -> List[Reference]:
    for i, ref in enumerate(references, start=1):
        ref.number = i
    return references


def build_references_from_notebook_context(
    context_data: Optional[Dict[str, Any]],
) -> List[Reference]:
    """Build a numbered reference list from notebook-chat context data.

    ``context_data`` has the shape produced by
    ``open_notebook.utils.context_builder.build_notebook_context``:
    ``{"sources": [source_context, ...], "notes": [note_context, ...]}``,
    where each ``source_context`` is ``{id, title, insights, full_text?}``
    (``full_text`` only present for "full content" inclusion) and each
    ``note_context`` is ``{id, title, content}``.
    """
    references: List[Reference] = []
    if not context_data:
        return references

    for source in context_data.get("sources") or []:
        if not isinstance(source, dict):
            continue
        source_id = source.get("id")
        title = source.get("title") or "Untitled source"
        full_text = source.get("full_text")
        if source_id and isinstance(full_text, str) and full_text.strip():
            references.append(
                Reference(
                    number=0,
                    ref_type="source",
                    ref_id=str(source_id),
                    title=str(title),
                    location=None,
                    snippet=_snippet(full_text),
                )
            )
        for insight in source.get("insights") or []:
            if not isinstance(insight, dict):
                continue
            insight_id = insight.get("id")
            if not insight_id:
                continue
            insight_type = insight.get("insight_type") or "insight"
            references.append(
                Reference(
                    number=0,
                    ref_type="source_insight",
                    ref_id=str(insight_id),
                    title=str(title),
                    location=f"Insight: {insight_type}",
                    snippet=_snippet(insight.get("content")),
                )
            )

    for note in context_data.get("notes") or []:
        if not isinstance(note, dict):
            continue
        note_id = note.get("id")
        content = note.get("content")
        if note_id and isinstance(content, str) and content.strip():
            references.append(
                Reference(
                    number=0,
                    ref_type="note",
                    ref_id=str(note_id),
                    title=str(note.get("title") or "Untitled note"),
                    location=None,
                    snippet=_snippet(content),
                )
            )

    return _renumber(references)


def build_references_from_source_context(
    context_data: Optional[Dict[str, Any]],
) -> List[Reference]:
    """Build a numbered reference list from source-chat context data.

    ``context_data`` has the shape produced by
    ``open_notebook.utils.context_builder.build_source_context``:
    ``{"sources": [source_context], "insights": [insight_dict, ...]}``.
    """
    references: List[Reference] = []
    if not context_data:
        return references

    for source in context_data.get("sources") or []:
        if not isinstance(source, dict):
            continue
        source_id = source.get("id")
        full_text = source.get("full_text")
        if source_id and isinstance(full_text, str) and full_text.strip():
            references.append(
                Reference(
                    number=0,
                    ref_type="source",
                    ref_id=str(source_id),
                    title=str(source.get("title") or "Untitled source"),
                    location=None,
                    snippet=_snippet(full_text),
                )
            )

    for insight in context_data.get("insights") or []:
        if not isinstance(insight, dict):
            continue
        insight_id = insight.get("id")
        if not insight_id:
            continue
        insight_type = insight.get("insight_type") or "insight"
        source_title = None
        if context_data.get("sources"):
            first_source = context_data["sources"][0]
            if isinstance(first_source, dict):
                source_title = first_source.get("title")
        references.append(
            Reference(
                number=0,
                ref_type="source_insight",
                ref_id=str(insight_id),
                title=str(source_title or "Untitled source"),
                location=f"Insight: {insight_type}",
                snippet=_snippet(insight.get("content")),
            )
        )

    return _renumber(references)


def build_references_from_search_results(
    results: Optional[List[Dict[str, Any]]],
) -> List[Reference]:
    """Build a numbered reference list from ``vector_search``/``text_search`` rows.

    Each row (after the SurrealQL function's own grouping) looks like
    ``{id, parent_id, title, similarity, matches: [content, ...]}``. There is
    no chunk/page location surfaced by that function - only which document
    matched and the matched snippet(s) - so ``location`` is left ``None``.
    Rows are deduplicated by ``id`` (the same document can come back from
    multiple sub-queries in the ask graph).
    """
    references: List[Reference] = []
    if not results:
        return references

    seen_ids: set[str] = set()
    for row in results:
        if not isinstance(row, dict):
            continue
        ref_id = row.get("id")
        if not ref_id:
            continue
        ref_id = str(ref_id)
        if ref_id in seen_ids:
            continue
        seen_ids.add(ref_id)

        matches = row.get("matches")
        if isinstance(matches, list) and matches:
            snippet_source = " ".join(str(m) for m in matches if isinstance(m, str))
        else:
            snippet_source = str(row.get("content") or "")

        ref_type = "source"
        if ref_id.startswith("note:"):
            ref_type = "note"
        elif ref_id.startswith("source_insight:"):
            ref_type = "source_insight"

        references.append(
            Reference(
                number=0,
                ref_type=ref_type,
                ref_id=ref_id,
                title=str(row.get("title") or "Untitled"),
                location=None,
                snippet=_snippet(snippet_source),
            )
        )

    return _renumber(references)


def render_reference_list(references: List[Reference]) -> str:
    """Render ``[n] <title>, <location>: <snippet>`` lines for the prompt."""
    lines = []
    for ref in references:
        location_part = f", {ref.location}" if ref.location else ""
        lines.append(f"[{ref.number}] {ref.title}{location_part}: {ref.snippet}")
    return "\n".join(lines)


def parse_cited_references(
    answer_text: str, references: List[Reference]
) -> List[Reference]:
    """Extract which ``[n]`` citations actually appear in ``answer_text``.

    Returns the matching references in first-occurrence order, deduplicated.
    Numbers that don't match any reference (hallucinated or out of range)
    are silently dropped - the point of this function is to report only
    citations that are actually backed by a retrieved item.
    """
    if not answer_text or not references:
        return []

    by_number = {ref.number: ref for ref in references}
    cited: List[Reference] = []
    seen_numbers: set[int] = set()
    for match in _CITATION_NUMBER_PATTERN.finditer(answer_text):
        number = int(match.group(1))
        if number in seen_numbers:
            continue
        ref = by_number.get(number)
        if ref is None:
            continue
        seen_numbers.add(number)
        cited.append(ref)
    return cited


def references_to_citation_dicts(references: List[Reference]) -> List[Dict[str, Any]]:
    """Plain-dict form for stashing on ``AIMessage.additional_kwargs``.

    Keeping this as plain dicts (rather than the pydantic model) ensures it
    round-trips through the LangGraph SqliteSaver checkpoint serializer the
    same way the rest of ``additional_kwargs`` does.
    """
    return [ref.model_dump() for ref in references]
