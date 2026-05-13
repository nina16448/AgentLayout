"""AssetPlannerRole -- Role wrapper around the PlanAssets Action.

Watches AnalyzeBrief output. Reads the enriched DesignSpec from
``Message.instruct_content`` and runs ``PlanAssets`` to produce a LayoutTree
describing the semantic groups among foreground elements.
"""
from __future__ import annotations

from metagpt.logs import logger
from metagpt.roles import Role
from metagpt.schema import Message

from metagpt.ext.agentlayout.actions.analyze_brief import AnalyzeBrief
from metagpt.ext.agentlayout.actions.plan_assets import PlanAssets
from metagpt.ext.agentlayout.schema import DesignSpec


class AssetPlannerRole(Role):
    name: str = "AssetPlanner"
    profile: str = "Layout Asset Planner"
    goal: str = (
        "Analyze semantic relationships among foreground elements and emit a "
        "LayoutTree. Background images are excluded from the tree by design."
    )
    constraints: str = (
        "Output must conform to metagpt.ext.agentlayout.schema.LayoutTree; "
        "every node id must be a real DesignSpec element id; only the synthetic "
        "root node is allowed."
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_actions([PlanAssets])
        self._watch([AnalyzeBrief])

    async def _act(self) -> Message:
        msg = self.rc.history[-1]
        spec = msg.instruct_content
        if not isinstance(spec, DesignSpec):
            raise ValueError(
                "AssetPlannerRole expected DesignSpec as instruct_content "
                f"(from AnalyzeBrief). Got: {type(spec).__name__}"
            )

        plan: PlanAssets = self.actions[0]
        tree = await plan.run(spec=spec)

        logger.info(
            f"AssetPlannerRole produced LayoutTree "
            f"({len(tree.all_element_ids())} nodes, "
            f"{len(tree.root.children)} top-level children)"
        )
        return Message(
            content=f"LayoutTree ready: {len(tree.all_element_ids())} nodes.",
            instruct_content=tree,
            role=self.profile,
            cause_by=PlanAssets,
        )
