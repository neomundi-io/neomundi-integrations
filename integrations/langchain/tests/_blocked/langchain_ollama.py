"""Shadows the real `langchain_ollama` so the example's absence path runs.

Placed on PYTHONPATH by `test_minimal_example_explains_a_missing_optional
_provider`. Raising ModuleNotFoundError on import is what an uninstalled
package looks like, without uninstalling anything.
"""

raise ModuleNotFoundError("No module named 'langchain_ollama'")
