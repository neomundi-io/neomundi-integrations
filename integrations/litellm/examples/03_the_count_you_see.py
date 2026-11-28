"""litellm-neomundi -- the number LiteLLM shows you, and whether a provider said it.

    pip install litellm-neomundi && python examples/03_the_count_you_see.py

No key, no account, no socket: `mock_response` makes LiteLLM build the
response itself, so no provider ever reported a token count.
"""

import time

import litellm

from litellm_neomundi import NeoMundiLogger

logger = NeoMundiLogger(submit=False)  # <- line 1: nothing ever leaves the process
litellm.callbacks = [logger]  # <- line 2

r = litellm.completion(
    model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], mock_response="Hello."
)
# LiteLLM runs success callbacks on a worker thread; the logger has no flush().
for _ in range(500):
    if logger.last_outcome is not None:
        break
    time.sleep(0.01)

print("LiteLLM reports      :", r.usage.total_tokens, "tokens")
print("traced to a provider :", logger.last_outcome.status)
print("why                  :", logger.last_outcome.reason)
