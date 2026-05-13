"""LLM Actions for the AgentLayout pipeline.

Each module in this package implements one of the four LLM Agents described
in ``layout_agent/README.md``:

    analyze_brief.py    -- Analyst         (user brief -> DesignSpec)
    plan_assets.py      -- Asset Planner   (DesignSpec -> LayoutTree)
    generate_layout.py  -- Layout Generator (DesignSpec + LayoutTree -> CandidatesBatch)
    judge_aesthetic.py  -- Aesthetic Judge (rendered images -> AestheticJudgement)

Every Action subclasses ``metagpt.actions.Action`` and overrides ``run``;
inputs and outputs are typed against ``metagpt.ext.agentlayout.schema``.
"""
