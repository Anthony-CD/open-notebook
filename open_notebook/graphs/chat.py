import asyncio
import sqlite3
from typing import Annotated, Optional

from ai_prompter import Prompter
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from open_notebook.ai.provision import provision_langchain_model
from open_notebook.config import LANGGRAPH_CHECKPOINT_FILE
from open_notebook.domain.notebook import Notebook
from open_notebook.exceptions import OpenNotebookError
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.citations import (
    build_references_from_notebook_context,
    parse_cited_references,
    references_to_citation_dicts,
    render_reference_list,
)
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.text_utils import extract_text_content


class ThreadState(TypedDict):
    messages: Annotated[list, add_messages]
    notebook: Optional[Notebook]
    # At runtime this is actually the raw context dict built by
    # build_notebook_context ({"sources": [...], "notes": [...]}), passed
    # straight through from the API request - see call_model_with_messages,
    # which turns it into a numbered reference list before it ever reaches
    # the prompt.
    context: Optional[dict]
    context_config: Optional[dict]
    model_override: Optional[str]


def call_model_with_messages(state: ThreadState, config: RunnableConfig) -> dict:
    try:
        # Build a numbered reference list from whatever was actually
        # retrieved/selected for this turn, instead of handing the model a
        # raw dict (previously str()-interpolated straight into the prompt -
        # unstructured and not something citations could be verified
        # against). See open_notebook/utils/citations.py.
        raw_context = state.get("context")
        references = (
            build_references_from_notebook_context(raw_context)
            if isinstance(raw_context, dict)
            else []
        )
        rendered_context = render_reference_list(references) if references else None
        prompt_data = {**state, "context": rendered_context}
        system_prompt = Prompter(prompt_template="chat/system").render(data=prompt_data)  # type: ignore[arg-type]
        payload = [SystemMessage(content=system_prompt)] + state.get("messages", [])
        model_id = config.get("configurable", {}).get("model_id") or state.get(
            "model_override"
        )

        # Handle async model provisioning from sync context
        def run_in_new_loop():
            """Run the async function in a new event loop"""
            new_loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(new_loop)
                return new_loop.run_until_complete(
                    provision_langchain_model(
                        str(payload), model_id, "chat", max_tokens=8192
                    )
                )
            finally:
                new_loop.close()
                asyncio.set_event_loop(None)

        try:
            # Try to get the current event loop
            asyncio.get_running_loop()
            # If we're in an event loop, run in a thread with a new loop
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_new_loop)
                model = future.result()
        except RuntimeError:
            # No event loop running, safe to use asyncio.run()
            model = asyncio.run(
                provision_langchain_model(
                    str(payload),
                    model_id,
                    "chat",
                    max_tokens=8192,
                )
            )

        ai_message = model.invoke(payload)

        # Clean thinking content from AI response (e.g., <think>...</think> tags)
        content = extract_text_content(ai_message.content)
        cleaned_content = clean_thinking_content(content)

        # Only the [n] markers the model actually used, matched against the
        # reference list built above - never derived from the raw text via
        # regex on document IDs. Stashed on additional_kwargs so it survives
        # the LangGraph SqliteSaver checkpoint and is still there when this
        # message is loaded again (see api/routers/_chat_shared.py).
        cited_references = parse_cited_references(cleaned_content, references)
        cleaned_message = ai_message.model_copy(
            update={
                "content": cleaned_content,
                "additional_kwargs": {
                    **ai_message.additional_kwargs,
                    "citations": references_to_citation_dicts(cited_references),
                },
            }
        )

        return {"messages": cleaned_message}
    except OpenNotebookError:
        raise
    except Exception as e:
        error_class, user_message = classify_error(e)
        raise error_class(user_message) from e


conn = sqlite3.connect(
    LANGGRAPH_CHECKPOINT_FILE,
    check_same_thread=False,
)
memory = SqliteSaver(conn)

agent_state = StateGraph(ThreadState)
agent_state.add_node("agent", call_model_with_messages)
agent_state.add_edge(START, "agent")
agent_state.add_edge("agent", END)
graph = agent_state.compile(checkpointer=memory)
