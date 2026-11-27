"""NeoMundi measurement for Microsoft Agent Framework.

Attach one middleware to a chat client and every model call it makes is
measured, with the provider's own token count and the latency the call
actually took.

    from agent_framework.openai import OpenAIChatCompletionClient
    from agent_framework_neomundi import NeoMundiChatMiddleware
    from neomundi import NeoMundiClient

    measure = NeoMundiChatMiddleware(
        client=NeoMundiClient(api_key=..., base_url="https://api.neomundi.io")
    )
    chat = OpenAIChatCompletionClient(model="gpt-4o-mini", middleware=[measure])

🔴 Read `LIMITATIONS.md` before drawing a conclusion from a score. In
particular §1: the token count here is the framework's, and the framework
was measured not to invent one -- it is **not** verified against the
provider's own bytes on each call, the way the LiteLLM integration
verifies it.
"""

from agent_framework_neomundi.middleware import (
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_INTERNAL_ERROR,
    NOT_SUBMITTED_INVALID_TOKEN_COUNT,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_NO_RESULT,
    NOT_SUBMITTED_NO_TOKEN_COUNT,
    NOT_SUBMITTED_STREAMING,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    OUTCOMES,
    SUBMITTED,
    TOKENS_FRAMEWORK_SUM,
    TOKENS_FRAMEWORK_TOTAL,
    NeoMundiChatMiddleware,
    Outcome,
    usage_token_count,
)
from agent_framework_neomundi.version import __version__

#: What this integration asserts about itself, machine-readable.
INTEGRATION_CONTRACT = {
    "integration": "agent-framework-neomundi",
    "host": "agent-framework",
    "host_verified": "1.26.0",
    "attachment_point": "ChatMiddleware",
    "token_count_source": "ChatResponse.usage_details",
    "token_count_verified_against_provider_payload": False,
    "latency_source": "measured around call_next()",
    "streaming": "refused by name",
    "measurement_versions_verified": (),
}

__all__ = [
    "__version__",
    "INTEGRATION_CONTRACT",
    "NeoMundiChatMiddleware",
    "Outcome",
    "OUTCOMES",
    "SUBMITTED",
    "NOT_SUBMITTED_LOCAL_ONLY",
    "NOT_SUBMITTED_NO_TOKEN_COUNT",
    "NOT_SUBMITTED_INVALID_TOKEN_COUNT",
    "NOT_SUBMITTED_STREAMING",
    "NOT_SUBMITTED_NO_RESULT",
    "NOT_SUBMITTED_APPLICATION_ERROR",
    "NOT_SUBMITTED_TRANSPORT_ERROR",
    "NOT_SUBMITTED_INTERNAL_ERROR",
    "TOKENS_FRAMEWORK_TOTAL",
    "TOKENS_FRAMEWORK_SUM",
    "usage_token_count",
]
