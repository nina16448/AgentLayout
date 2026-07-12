"""A3-15B: adjudication vote analysis (self-preference bias diagnostic).

Read-only over frozen annotation runs; zero API calls.

n80 (primary): for every decision unit where the three annotators disagree,
record which annotator(s) the adjudicated value matches. Decision units:
  - semantic_type per asset
  - parent_id per asset
  - same-group boolean per unordered asset pair
Key bias metric: P(adjudication sides with annotator j | j is the lone
dissenter in a 2-1 split). Adjudicator model (GPT-5.6 sol) == annotator
`hui`'s model, so a markedly higher side-with rate for hui than for
neiji/nina indicates self-preference.

pilot (secondary): the oracle is a full independent re-annotation (also
GPT-5.6 sol); report its overall per-annotator alignment instead
(T/hui = GPT-5.6 sol, neiji = Fable 5).
"""

import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path

RUNS = Path(__file__).resolve().parent / "runs" / "a3"
N80 = RUNS / "a3-relation-annot-n80-01"
PILOT = RUNS / "a3-gateab-pilot-n20-01"
OUT = (
    Path(__file__).resolve().parent
    / "evaluations"
    / "a3-adjudication-bias"
    / "a3.adjudication-bias.v1"
    / "a3-n80-adjudication-vote-v1"
)

N80_ANNOTATORS = {"hui": "gpt-5.6-sol", "neiji": "claude-fable-5", "nina": "gemini-3.5-flash"}
PILOT_ANNOTATORS = {"T": "gpt-5.6-sol", "hui": "gpt-5.6-sol", "neiji": "claude-fable-5"}
ADJUDICATOR_MODEL = "gpt-5.6-sol"


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def asset_map(annotation):
    return {a["asset_id"]: a for a in annotation["assets"]}


def units_for(annotations):
    """Yield (axis, unit_key, {annotator: value}) for every decision unit."""
    names = list(annotations)
    ids = sorted(asset_map(annotations[names[0]]))
    maps = {n: asset_map(annotations[n]) for n in names}
    for aid in ids:
        yield "semantic_type", aid, {n: maps[n][aid]["semantic_type"] for n in names}
        yield "parent_id", aid, {n: maps[n][aid]["parent_id"] for n in names}
    for a, b in combinations(ids, 2):
        yield (
            "same_group",
            f"{a}|{b}",
            {n: maps[n][a]["group_id"] == maps[n][b]["group_id"] for n in names},
        )


def analyze_n80():
    stats = {
        axis: {
            "contested": 0,
            "adj_matches": defaultdict(int),
            "adj_matches_none": 0,
            "splits_2_1": 0,
            "splits_1_1_1": 0,
            "minority_cases": defaultdict(int),
            "adj_sides_with_minority": defaultdict(int),
        }
        for axis in ("semantic_type", "parent_id", "same_group")
    }
    n_samples = 0
    for sdir in sorted((N80 / "samples").iterdir()):
        adj_path = N80 / "adjudication" / "samples" / sdir.name / "annotation_adjudicated.json"
        if not adj_path.exists():
            raise FileNotFoundError(adj_path)
        annos = {k: load(sdir / "annotation" / f"annotation_{k}.json") for k in N80_ANNOTATORS}
        adjudicated = load(adj_path)
        adj_map = asset_map(adjudicated)
        n_samples += 1
        for axis, key, votes in units_for(annos):
            if len(set(votes.values())) == 1:
                continue  # unanimous
            st = stats[axis]
            st["contested"] += 1
            if axis == "semantic_type":
                adj_val = adj_map[key]["semantic_type"]
            elif axis == "parent_id":
                adj_val = adj_map[key]["parent_id"]
            else:
                a, b = key.split("|")
                adj_val = adj_map[a]["group_id"] == adj_map[b]["group_id"]
            matched = [n for n, v in votes.items() if v == adj_val]
            for n in matched:
                st["adj_matches"][n] += 1
            if not matched:
                st["adj_matches_none"] += 1
            counts = defaultdict(list)
            for n, v in votes.items():
                counts[repr(v)].append(n)
            if len(counts) == 2:
                st["splits_2_1"] += 1
                minority = min(counts.values(), key=len)[0]
                st["minority_cases"][minority] += 1
                if votes[minority] == adj_val:
                    st["adj_sides_with_minority"][minority] += 1
            else:
                st["splits_1_1_1"] += 1
    for axis, st in stats.items():
        st["adj_matches"] = dict(st["adj_matches"])
        st["minority_cases"] = dict(st["minority_cases"])
        st["adj_sides_with_minority"] = dict(st["adj_sides_with_minority"])
        st["side_with_minority_rate"] = {
            n: st["adj_sides_with_minority"].get(n, 0) / c
            for n, c in st["minority_cases"].items()
        }
    return {"n_samples": n_samples, "annotator_models": N80_ANNOTATORS, "axes": stats}


def analyze_pilot():
    totals = {axis: defaultdict(lambda: [0, 0]) for axis in ("semantic_type", "parent_id", "same_group")}
    n_samples = 0
    for sdir in sorted((PILOT / "samples").iterdir()):
        annos = {k: load(sdir / "annotation" / f"annotation_{k}.json") for k in PILOT_ANNOTATORS}
        oracle = load(sdir / "annotation" / "annotation_nina.json")
        o_map = asset_map(oracle)
        n_samples += 1
        for axis, key, votes in units_for(annos):
            if axis == "semantic_type":
                o_val = o_map[key]["semantic_type"]
            elif axis == "parent_id":
                o_val = o_map[key]["parent_id"]
            else:
                a, b = key.split("|")
                o_val = o_map[a]["group_id"] == o_map[b]["group_id"]
            for n, v in votes.items():
                pair = totals[axis][n]
                pair[1] += 1
                if v == o_val:
                    pair[0] += 1
    out = {"n_samples": n_samples, "annotator_models": PILOT_ANNOTATORS, "oracle_model": ADJUDICATOR_MODEL, "alignment": {}}
    for axis, per in totals.items():
        out["alignment"][axis] = {n: {"agree": a, "total": t, "rate": a / t} for n, (a, t) in per.items()}
    return out


def main():
    result = {
        "analysis_id": "a3-n80-adjudication-vote-v1",
        "adjudicator_model": ADJUDICATOR_MODEL,
        "n80": analyze_n80(),
        "pilot_secondary": analyze_pilot(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / "aggregate.json"
    if out_path.exists():
        raise SystemExit(f"refusing to overwrite existing bundle: {out_path}")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nwritten: {out_path}")


if __name__ == "__main__":
    main()
