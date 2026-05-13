# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

MetaGPT is a multi-agent framework that materializes the SOP of a software company (`Code = SOP(Team)`). A `Team` hires LLM-backed `Role`s (ProductManager, Architect, Engineer, etc.) that exchange `Message`s through an `Environment` (default: `MGXEnv`). Each role drives one or more `Action`s, which encapsulate a single LLM-or-tool invocation and return a `Message` back to the environment.

Python: `>=3.9, <3.12`. The package is installed editable from `setup.py` (`pip install -e .`).

## Common commands

### Install (development)
```bash
pip install -e .              # core
pip install -e .[test]        # adds pytest, playwright deps, etc.
pip install -e .[dev]         # pylint, black, isort, pre-commit
pip install -e .[rag]         # llama-index extras
npm install -g @mermaid-js/mermaid-cli   # required by docs/diagram actions
```

### Configuration
The runtime reads `~/.metagpt/config2.yaml` (created by `metagpt --init-config`, template in `config/config2.example.yaml`). Tests read `tests/config2.yaml` — the unit-test workflow copies it to `~/.metagpt/config2.yaml` before running.

### Run the agent
```bash
metagpt "Create a 2048 game"   # CLI entrypoint -> metagpt.software_company:app
# Equivalent in Python:
python -c "from metagpt.software_company import generate_repo; generate_repo('Create a 2048 game')"
```
Outputs land in `./workspace/` (`DEFAULT_WORKSPACE_ROOT = METAGPT_ROOT/workspace`); serialized team state under `workspace/storage/team`. Use `--recover-path workspace/storage/team` to resume a serialized run.

### Test
The full suite is gated through `pytest.ini`, which `--ignore`s the majority of modules that hit live APIs. Running plain `pytest` only executes the offline-safe subset.

```bash
# Offline-safe subset (matches CI):
export ALLOW_OPENAI_API_CALL=0
mkdir -p ~/.metagpt && cp tests/config2.yaml ~/.metagpt/config2.yaml
pytest

# A single test file (the explicit path overrides the default ignore list):
pytest tests/metagpt/utils/test_text.py -q

# A single test:
pytest tests/metagpt/utils/test_text.py::test_decode_unicode_escape -q

# Coverage HTML in ./htmlcov/, XML in ./cov.xml (configured in pytest.ini)
coverage report -m
```
`tests/conftest.py` builds `MockLLM` from `tests/data/rsp_cache.json` and writes new responses to `rsp_cache_new.json`. Set `ALLOW_OPENAI_API_CALL=1` only when intentionally producing new cached responses.

### Lint / format
```bash
pre-commit install
pre-commit run --all-files     # runs isort, ruff, black per .pre-commit-config.yaml
ruff check .                   # ruff.toml: select=E,F; line-length=120; py39
black --line-length 120 .
isort --profile black .
```
CI runs `pre-commit run --all-files` and `pytest` on every PR (`.github/workflows/pre-commit.yaml`, `unittest.yaml`).

## Architecture

### Execution loop
1. `metagpt.software_company.generate_repo` builds a `Context` from `metagpt.config2.config`, instantiates a `Team`, hires roles, and calls `team.run(n_round, idea)`.
2. `Team` owns an `Environment` (`MGXEnv` when `use_mgx=True`, otherwise `Environment`). The env routes `Message`s by `send_to`/`cause_by` tags and triggers each role's `_observe → _think → _act` cycle.
3. A `Role`'s `_act` runs the currently selected `Action`. Actions return a `Message`/`ActionOutput`; the role publishes it back to the env, which feeds it to subscribers on the next tick.
4. After `n_round` ticks (or when no role has work), `team.run` returns and `ctx.kwargs["project_path"]` points to the generated repo under `workspace/`.

### Configuration system (`metagpt/config2.py` + `metagpt/configs/`)
`Config` is a Pydantic model assembled from per-feature configs (`llm_config`, `embedding_config`, `s3_config`, `redis_config`, `workspace_config`, `mermaid_config`, `browser_config`, `search_config`, `role_zero_config`, `role_custom_config`, `models_config`, `compress_msg_config`, `exp_pool_config`, `omniparse_config`). `LLMConfig.api_type` (`LLMType`) selects the provider in `metagpt/provider/`. `ContextMixin` injects `self.config` / `self.llm` into roles and actions; do not read globals directly inside roles.

### Roles, actions, schema
- `metagpt/roles/role.py` is the base; concrete roles include `ProductManager`, `Architect`, `ProjectManager`, `Engineer`, `Engineer2`, `QaEngineer`, `Researcher`, `TeamLeader`, plus the Data Interpreter family in `metagpt/roles/di/` (`DataInterpreter`, `DataAnalyst`, `RoleZero`, `Engineer2`, `SWEAgent`, `TeamLeader`). The default hire list in `software_company.py` is `[TeamLeader, ProductManager, Architect, Engineer2, DataAnalyst]`.
- `metagpt/actions/action.py` + `action_node.py` define the action interface. `ActionNode` is the structured-output primitive; many design/PRD actions compose nodes via `ActionGraph`.
- `metagpt/schema.py` defines `Message`, `AIMessage`, `Task`, `TaskResult`, `MessageQueue`, and `SerializationMixin`. Routing constants live in `metagpt/const.py` (`MESSAGE_ROUTE_TO_ALL`, `MESSAGE_ROUTE_TO_SELF`, etc.).

### Environments and tools
- `metagpt/environment/` hosts `Environment` (basic broadcast) and specialized envs (`mgx`, `android`, `werewolf`, `minecraft`, `stanford_town`). `MGXEnv` is the default for `Team`.
- `metagpt/tools/` exposes browser/search/translate/vision tools registered via `tool_registry`. `metagpt/rag/` wires llama-index retrievers/embeddings (only when the `rag` extras are installed).
- `metagpt/ext/` contains research extensions (`aflow`, `sela`, `spo`, `cr`, `android_assistant`, `werewolf`, `stanford_town`); these are excluded from the default test run (`norecursedirs` in `pytest.ini`) and may pull heavy optional dependencies.

### Persistence
`Team.serialize()` writes `workspace/storage/team/team.json` (path overridable). `Team.deserialize(stg_path)` rebuilds the team and ctx; `software_company.generate_repo(..., recover_path=...)` is the supported entry point.

## Working in this repo

- The CI test step runs plain `pytest`, which honours every `--ignore` in `pytest.ini`. If you add a test that needs to run in CI, do not add a new `--ignore`; if you delete an `--ignore`, expect the test to need a `MockLLM` cache entry.
- `--doctest-modules` is on by default, so every docstring example in `metagpt/` is collected as a test. New docstring examples must be runnable or guarded.
- Roles/actions must access config and LLM through `ContextMixin` (`self.config`, `self.llm`, `self.context`) rather than the module-level `config` singleton, otherwise serialization and the per-team context override break.
- When extending `Engineer`-style roles, prefer subclassing `RoleZero` / `Engineer2` (the DI stack) rather than the legacy `metagpt.roles.engineer.Engineer`, which is being deprecated alongside the commented-out hire calls in `software_company.py`.
- Workspace artifacts (`./workspace/`, `./logs/`) are generated; do not commit them.
