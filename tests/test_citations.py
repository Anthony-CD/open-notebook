"""Tests for structured chat/ask citations (open_notebook/utils/citations.py).

Covers the hard parts called out for P1 "Chat con citas":
- citation parsing from a known reference list + a mock model answer
- the "no evidence" honest-answer path (empty/irrelevant retrieval doesn't
  crash, and there's simply nothing to cite)
- ES/EN sources mixed in one notebook (the plumbing is structural - it
  doesn't care what language the title/snippet text is in)
- contradictory sources are kept as separate numbered citations rather than
  the system silently picking one

No live DB and no real model calls anywhere in this file - graph-level tests
mock `provision_langchain_model` and the model's `.invoke`/`.ainvoke`.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from open_notebook.utils.citations import (
    Reference,
    build_references_from_notebook_context,
    build_references_from_search_results,
    build_references_from_source_context,
    parse_cited_references,
    references_to_citation_dicts,
    render_reference_list,
)

# --- build_references_from_notebook_context ----------------------------------


def test_notebook_context_builds_source_and_insight_and_note_references():
    context_data = {
        "sources": [
            {
                "id": "source:1",
                "title": "Deep Learning Basics",
                "full_text": "Deep learning uses layered neural networks.",
                "insights": [
                    {
                        "id": "source_insight:1",
                        "insight_type": "summary",
                        "content": "Summary: neural nets learn hierarchical features.",
                    }
                ],
            }
        ],
        "notes": [
            {"id": "note:1", "title": "My note", "content": "Remember to re-read chapter 3."}
        ],
    }

    refs = build_references_from_notebook_context(context_data)

    assert [r.number for r in refs] == [1, 2, 3]
    assert refs[0].ref_type == "source"
    assert refs[0].ref_id == "source:1"
    assert refs[0].location is None  # whole-document citation, no page tracking exists

    assert refs[1].ref_type == "source_insight"
    assert refs[1].ref_id == "source_insight:1"
    assert refs[1].location == "Insight: summary"

    assert refs[2].ref_type == "note"
    assert refs[2].ref_id == "note:1"


def test_notebook_context_short_mode_has_no_full_text_reference():
    """"insights"-only inclusion mode has no full_text - only the insight(s)
    become citable, not the source document itself (there's nothing to quote
    from if the full text was never fetched)."""
    context_data = {
        "sources": [
            {
                "id": "source:1",
                "title": "Short Context Source",
                "insights": [
                    {"id": "source_insight:1", "insight_type": "summary", "content": "x"}
                ],
                # no "full_text" key - short-context mode
            }
        ],
        "notes": [],
    }

    refs = build_references_from_notebook_context(context_data)
    assert len(refs) == 1
    assert refs[0].ref_type == "source_insight"


def test_notebook_context_empty_is_empty():
    assert build_references_from_notebook_context(None) == []
    assert build_references_from_notebook_context({}) == []
    assert build_references_from_notebook_context({"sources": [], "notes": []}) == []


# --- build_references_from_source_context -------------------------------------


def test_source_context_builds_source_and_insights():
    context_data = {
        "sources": [
            {"id": "source:1", "title": "My Doc", "full_text": "Full document text."}
        ],
        "insights": [
            {"id": "source_insight:1", "insight_type": "summary", "content": "A summary."},
            {"id": "source_insight:2", "insight_type": "key_topics", "content": "Topics."},
        ],
    }

    refs = build_references_from_source_context(context_data)

    assert len(refs) == 3
    assert refs[0].ref_type == "source"
    assert refs[0].location is None
    assert refs[1].location == "Insight: summary"
    assert refs[1].title == "My Doc"  # insight inherits the parent source's title
    assert refs[2].location == "Insight: key_topics"


# --- build_references_from_search_results (ask.py) ---------------------------


def test_search_results_dedupes_by_id_and_has_no_location():
    """vector_search groups/aggregates by document id and never surfaces a
    chunk/page position, so ask-graph citations are honestly document-level
    only - location must stay None, never invented."""
    results = [
        {"id": "source:1", "title": "Doc A", "matches": ["chunk one"]},
        {"id": "source:1", "title": "Doc A", "matches": ["chunk one duplicate branch"]},
        {"id": "note:2", "title": "Doc B", "matches": ["chunk two"]},
    ]

    refs = build_references_from_search_results(results)

    assert len(refs) == 2  # source:1 deduplicated across the two sub-query branches
    assert all(r.location is None for r in refs)
    assert refs[0].ref_id == "source:1"
    assert refs[1].ref_type == "note"


def test_search_results_empty_is_empty_not_a_crash():
    """The 'no evidence' case for ask.py: zero retrieved rows must not raise,
    and must produce zero citations (nothing to honestly cite)."""
    assert build_references_from_search_results([]) == []
    assert build_references_from_search_results(None) == []


# --- ES/EN mixed sources (structural) -----------------------------------------


def test_mixed_language_sources_both_become_distinct_references():
    """The citation plumbing is purely structural - it must not care whether
    a source's title/snippet is Spanish or English."""
    context_data = {
        "sources": [
            {
                "id": "source:es",
                "title": "Aprendizaje profundo",
                "full_text": "El aprendizaje profundo utiliza redes neuronales en capas.",
                "insights": [],
            },
            {
                "id": "source:en",
                "title": "Deep Learning",
                "full_text": "Deep learning uses layered neural networks.",
                "insights": [],
            },
        ],
        "notes": [],
    }

    refs = build_references_from_notebook_context(context_data)

    assert [r.ref_id for r in refs] == ["source:es", "source:en"]
    assert refs[0].title == "Aprendizaje profundo"
    assert refs[1].title == "Deep Learning"
    rendered = render_reference_list(refs)
    assert "[1] Aprendizaje profundo" in rendered
    assert "[2] Deep Learning" in rendered


# --- contradictory sources -----------------------------------------------------


def test_contradictory_sources_both_kept_as_separate_citations():
    """Two sources disagreeing with each other must both surface as distinct
    numbered references - the builder must never silently pick one."""
    context_data = {
        "sources": [
            {
                "id": "source:claim_a",
                "title": "Paper A",
                "full_text": "The study found that the treatment improves outcomes.",
                "insights": [],
            },
            {
                "id": "source:claim_b",
                "title": "Paper B",
                "full_text": "The study found that the treatment has no measurable effect.",
                "insights": [],
            },
        ],
        "notes": [],
    }

    refs = build_references_from_notebook_context(context_data)

    assert len(refs) == 2
    assert refs[0].ref_id != refs[1].ref_id
    assert "improves" in refs[0].snippet
    assert "no measurable effect" in refs[1].snippet

    # Both are independently citable - the model can cite [1] and [2] in the
    # same sentence to surface the contradiction instead of the plumbing
    # resolving it for them.
    answer = "Sources disagree on this: one reports a benefit [1], another reports none [2]."
    cited = parse_cited_references(answer, refs)
    assert [c.ref_id for c in cited] == ["source:claim_a", "source:claim_b"]


# --- parse_cited_references ---------------------------------------------------


def test_parse_cited_references_extracts_known_numbers_in_order():
    refs = [
        Reference(number=1, ref_type="source", ref_id="source:a", title="A", snippet="a"),
        Reference(number=2, ref_type="note", ref_id="note:b", title="B", snippet="b"),
        Reference(number=3, ref_type="source_insight", ref_id="source_insight:c", title="C", snippet="c"),
    ]

    answer = "First point [2]. Second point [1][3]. Repeated [2] again."
    cited = parse_cited_references(answer, refs)

    # First-occurrence order, deduplicated.
    assert [c.number for c in cited] == [2, 1, 3]


def test_parse_cited_references_drops_hallucinated_numbers():
    """A bracket number with no matching reference (hallucinated, or simply
    out of range) must be silently dropped, never surfaced as a fake citation."""
    refs = [
        Reference(number=1, ref_type="source", ref_id="source:a", title="A", snippet="a"),
    ]
    answer = "This cites a real one [1] and a made-up one [99]."
    cited = parse_cited_references(answer, refs)
    assert [c.number for c in cited] == [1]


def test_parse_cited_references_no_references_no_crash():
    assert parse_cited_references("Some answer with [1] in it.", []) == []
    assert parse_cited_references("", []) == []


def test_references_to_citation_dicts_round_trips_plain_dicts():
    refs = [
        Reference(
            number=1,
            ref_type="source",
            ref_id="source:a",
            title="A",
            location="Insight: summary",
            snippet="hello",
        )
    ]
    dicts = references_to_citation_dicts(refs)
    assert dicts == [
        {
            "number": 1,
            "ref_type": "source",
            "ref_id": "source:a",
            "title": "A",
            "location": "Insight: summary",
            "snippet": "hello",
        }
    ]


# --- graph-level: "no evidence" honesty path doesn't crash --------------------


def test_chat_graph_handles_no_context_without_crashing():
    """When nothing was selected into context, the chat graph must still run
    (general-knowledge answer, no citations) instead of crashing - and must
    not fabricate a citation out of nothing."""
    from open_notebook.graphs.chat import call_model_with_messages

    mock_model = MagicMock()
    mock_model.invoke.return_value = AIMessage(
        content="I don't have enough information in your notebook to answer that."
    )

    with patch(
        "open_notebook.graphs.chat.provision_langchain_model",
        new=AsyncMock(return_value=mock_model),
    ):
        state = {
            "messages": [HumanMessage(content="What does source X say about Y?")],
            "notebook": None,
            "context": None,
            "context_config": None,
            "model_override": None,
        }
        result = call_model_with_messages(state, {"configurable": {}})

    ai_message = result["messages"]
    assert "enough information" in ai_message.content
    assert ai_message.additional_kwargs["citations"] == []


def test_chat_graph_parses_citations_from_mocked_model_answer():
    """End-to-end (graph-level, mocked model): the model is handed a numbered
    reference list and its [n] answer is parsed back into structured
    citations - this is the "no fragile string matching on the raw answer"
    guarantee the frontend relies on."""
    from open_notebook.graphs.chat import call_model_with_messages

    mock_model = MagicMock()
    mock_model.invoke.return_value = AIMessage(
        content="Deep learning is a subset of machine learning [1]."
    )

    with patch(
        "open_notebook.graphs.chat.provision_langchain_model",
        new=AsyncMock(return_value=mock_model),
    ):
        state = {
            "messages": [HumanMessage(content="What is deep learning?")],
            "notebook": None,
            "context": {
                "sources": [
                    {
                        "id": "source:1",
                        "title": "ML Basics",
                        "full_text": "Deep learning is a subset of machine learning.",
                        "insights": [],
                    }
                ],
                "notes": [],
            },
            "context_config": None,
            "model_override": None,
        }
        result = call_model_with_messages(state, {"configurable": {}})

    ai_message = result["messages"]
    citations = ai_message.additional_kwargs["citations"]
    assert len(citations) == 1
    assert citations[0]["ref_id"] == "source:1"
    assert citations[0]["number"] == 1
