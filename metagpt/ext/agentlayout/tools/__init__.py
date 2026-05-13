"""Non-LLM tools for the AgentLayout pipeline.

Each module is a pure-Python or CV utility that the pipeline driver invokes
between LLM-Agent steps. These tools never call an LLM; they perform
deterministic computation, validation, or rendering against the schemas
defined in ``metagpt.ext.agentlayout.schema``.
"""
