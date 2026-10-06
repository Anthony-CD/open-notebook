"""Regression test for a bug found during live QA (2026-10-06): a source
whose transformation hit a real AuthenticationError (bad/revoked API key)
got stuck "processing" indefinitely - surreal_commands retried the whole
process_source command up to 15 times with exponential backoff (up to 120s
apart), each attempt silently re-running extraction and re-submitting an
embed job, because `stop_on` only listed ValueError/ConfigurationError and
AuthenticationError fell through to the default "retry it" path.
run_transformation_command had the identical gap (same transform_graph
call, 5 max_attempts).

Confirmed live: a test source stuck on a stale credential retried 8 times
over several minutes before the fix; after it, the command failed cleanly
on the first attempt with a sanitized error message.

This test guards the fix at the one place that matters for a decorator
whose retry wrapper only runs inside surreal_commands' own job executor
(not when the decorated function is called directly in-process, which is
why this checks the declared policy rather than trying to exercise the
wrapper) - see commands/source_commands.py's `_PERMANENT_FAILURE_TYPES`.
"""

import inspect

from commands.source_commands import _PERMANENT_FAILURE_TYPES
from open_notebook.exceptions import (
    AuthenticationError,
    ConfigurationError,
    NotFoundError,
)


class TestPermanentFailureTypesIncludeAuthAndNotFound:
    def test_authentication_error_is_permanent(self):
        """A bad/revoked API key will never succeed just by retrying."""
        assert AuthenticationError in _PERMANENT_FAILURE_TYPES

    def test_not_found_error_is_permanent(self):
        """A missing source/transformation/notebook won't appear by retrying."""
        assert NotFoundError in _PERMANENT_FAILURE_TYPES

    def test_configuration_error_still_permanent(self):
        """Pre-existing entry - guard against an accidental regression."""
        assert ConfigurationError in _PERMANENT_FAILURE_TYPES

    def test_value_error_still_permanent(self):
        """Pre-existing entry - guard against an accidental regression."""
        assert ValueError in _PERMANENT_FAILURE_TYPES


class TestBothRetryableCommandsUseTheSharedPolicy:
    """Both commands call the same transform_graph.ainvoke() internally and
    both had the identical gap - assert their source actually wires
    `stop_on` to the shared constant, not a copy-pasted literal list that
    could silently drift out of sync again."""

    def test_process_source_and_run_transformation_reference_shared_constant(self):
        import commands.source_commands as module

        source = inspect.getsource(module)
        # Exactly two @command(...) retry blocks exist in this file
        # (process_source, run_transformation); both must declare
        # stop_on=_PERMANENT_FAILURE_TYPES, not a hand-written list that
        # could individually drift and reintroduce this bug for one of the
        # two commands but not the other.
        assert source.count('"stop_on": _PERMANENT_FAILURE_TYPES') == 2, (
            "Expected both process_source_command and run_transformation_command "
            "to declare stop_on=_PERMANENT_FAILURE_TYPES exactly once each."
        )
