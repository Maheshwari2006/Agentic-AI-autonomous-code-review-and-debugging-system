# Agentic AI — Autonomous Code Review and Debugging System

Autonomous, commit-based, symbol-level code review and debugging for GitHub
repositories, powered by the Anthropic Claude API. Built to analyze a Git
commit the way a senior engineer would: file → function/class → dependency
→ review → fix → verification, never as an undifferentiated blob of diff
text.

---

## 1. Project overview & problem statement

Most "AI code review" tools do `diff -> LLM -> comment`. That approach:
- treats a whole commit as one undifferentiated block, even when it bundles
  several unrelated changes;
- either truncates large diffs/files to a fixed line/character budget, or
  blindly dumps the whole repository into the prompt;
- gives a review with no attempt to verify the fix actually works.

This project instead:
1. Parses changed files into an **AST-based symbol index** (functions,
   methods, classes) — so "what changed" means specific named symbols with
   exact line ranges, not a line-diff.
2. **Groups** changed symbols into independent logical change units using
   file/call relationships (a commit that touches auth, logging, and DB
   code is reviewed as up to three separate concerns, not one).
3. Gives Claude **tools** (`read_file`, `read_function`, `find_callers`,
   `find_callees`, `search_code`, `get_tests_for_file`, ...) and lets it
   decide what additional context it needs before answering — a real
   agentic loop, not a single prompt.
4. Runs a **debugging pass** that tries to disprove each high-severity
   finding before trusting it.
5. Generates an **implementation-ready fix and unified diff patch** per
   issue.
6. **Actually runs tests** against the proposed patch in an isolated
   temporary clone before ever calling something "VERIFIED".

## 2. Features

- Real GitHub REST API integration (commit metadata, parent diff, full
  file content at both revisions, PR metadata).
- Real Anthropic Claude API integration via a provider abstraction
  (`backend/app/llm/`), model configurable via `.env`, with retry/backoff
  and defensive JSON parsing.
- AST-based Python symbol extraction with **no fixed line-count or
  character-count truncation** anywhere in the pipeline (see §6).
- Symbol-level diffing: added/modified/removed functions, methods, classes.
- Logical change-grouping (union-find over file + call relationships) to
  detect multiple independent changes bundled in one commit.
- Repository-aware symbol index for fast caller/callee lookups (the
  "retrieval layer") instead of re-sending whole files per question.
- 8-agent pipeline: Repository Explorer, Change Analyzer, Dependency
  Analyzer, Code Reviewer, Debugging Agent, Fix Generator, Verification
  Agent, Report Generator.
- Structured issue output (JSON) with severity (`CRITICAL`/`HIGH`/
  `MEDIUM`/`LOW`/`INFO`) and category (`CORRECTNESS`/`SECURITY`/
  `PERFORMANCE`/`MAINTAINABILITY`/`RELIABILITY`/`TESTING`/
  `API_COMPATIBILITY`).
- Implementation-ready fix suggestions + generated unified-diff patches
  (never auto-applied to the user's repository).
- Isolated, timeout-bounded, allowlisted-command test execution to verify
  proposed patches (clone → checkout → `git apply` → run tests), clearly
  separated from the read-only analysis code.
- SQLite persistence (Repository, Commit, ChangedFile, ChangedSymbol,
  Review, Issue, Verification), portable to PostgreSQL by changing
  `DATABASE_URL` alone.
- FastAPI backend with Pydantic schemas.
- React dashboard (no build step — see §11 for why, and how to upgrade it).
- Structured logging that never logs secrets.
- Docker + docker-compose.

## 3. Architecture

```mermaid
flowchart LR
    subgraph Input
        GH[GitHub commit]
    end
    GH --> RE[Repository Explorer]
    RE --> CA[Change Analyzer]
    CA --> DA[Dependency Analyzer]
    DA --> RV[Code Review Agent]
    RV --> DB[Debugging Agent]
    DB --> FX[Fix Generator]
    FX --> VF[Verification Agent]
    VF --> RP[Report Generator]
    RP --> OUT[Stored Review + Issues + Verification]
```

## 4. Agent architecture

| # | Agent | File | Responsibility |
|---|-------|------|-----------------|
| 1 | Repository Explorer | `agents/repository_agent.py` | Fetch full previous/current file content, seed the symbol index |
| 2 | Change Analyzer | `agents/change_agent.py` | Symbol-level diff + logical change grouping |
| 3 | Dependency Analyzer | `agents/dependency_agent.py` | Pull in related test files, note cross-file callers |
| 4 | Code Review Agent | `agents/review_agent.py` | Agentic tool-use loop producing structured issues |
| 5 | Debugging Agent | `agents/debugging_agent.py` | Re-investigates HIGH/CRITICAL findings before trusting them |
| 6 | Fix Generator | `agents/fix_agent.py` | Implementation-ready fix + unified diff patch per issue |
| 7 | Verification Agent | `agents/verification_agent.py` | Runs the real verification workflow (see §7) |
| 8 | Report Generator | `agents/report_agent.py` | Compiles overall status/summary/confidence |

All agents share one `AnalysisContext` (`agents/base.py`) rather than each
independently re-fetching or re-sending code to the LLM.

## 5. Data flow

`GitHubClient.get_commit()` → `CommitDetail` (with per-file `patch`, plus
full previous/current content fetched via the Contents API) → `AgentPipeline.run()`
walks the 8 agents over one shared `AnalysisContext` → `AnalysisService._persist()`
writes `Commit`, `ChangedFile`, `ChangedSymbol`, `Review`, `Issue`, and
`Verification` rows → the API/dashboard read from there.

## 6. No fixed line-limit — how it's actually avoided

The old truncation this project replaces looked like:
```python
MAX_DIFF_CHARS = 12000
diff_text[:MAX_DIFF_CHARS]
```
There is **no equivalent anywhere in this codebase**. Concretely:
- `parsing/python_ast.py` parses a file's *entire* source with Python's
  `ast` module and extracts each function/class as its own `Symbol` with
  full, untruncated source text — proven in
  `tests/test_python_ast.py::test_no_arbitrary_line_limit_large_file`,
  which builds a 600-function / >12,000-character file (bigger than the
  old constant) and asserts every function is fully extracted.
- `analysis/change_detection.py` diffs old vs. new **symbol sets**, so a
  10,000-line file with 2 changed functions produces exactly 2 (plus
  dependencies) changed-symbol records — never a truncated diff blob.
- The Review Agent (`agents/review_agent.py`) is given tools, not a fixed
  prompt payload: it can call `read_function`, `find_callers`,
  `find_callees`, `search_code` as many times as it needs (bounded only by
  a *loop-safety* iteration cap, `MAX_AGENT_TOOL_ITERATIONS`, which stops
  runaway tool-call loops, not content size).
- `MAX_SYMBOLS_PER_CONTEXT_BATCH` and the 200,000-character safety cap on
  a single tool result (`agents/review_agent.py::_safe_json`) are the only
  numeric bounds in the pipeline, and both are *batching/loop-safety*
  controls, not content-truncation of any individual symbol/file — if a
  function has 300 callers, the agent gets the first batch and can request
  more via another tool call rather than the content itself being cut.

## 7. Verification workflow (real, not simulated)

`services/verification_runner.py`:
1. `tempfile.mkdtemp()` — fresh isolated workspace.
2. `git clone` the repository, `git checkout <commit sha>`.
3. If a patch was generated with sufficient confidence (≥ 0.55), `git apply` it.
4. Run the first available allowlisted command from `ALLOWED_TEST_COMMANDS`
   (default: `pytest`, `python -m pytest`, `python -m unittest discover`)
   with a hard timeout and a minimal environment (no inherited secrets).
5. Parse pass/fail counts from the output; return
   `VERIFIED` / `PARTIALLY_VERIFIED` / `FAILED` / `UNABLE_TO_VERIFY`.

It is never claimed that a fix works without this actually running — see
`tests/test_pipeline_e2e.py` and `tests/test_verification_runner.py`,
both of which exercise this against a **real local git repository**, not
mocks.

**Security note:** this sandbox does not add a network-namespace boundary
around the test-execution subprocess beyond the minimal-env/timeout/
allowlisted-command controls described above. If you're running this
against untrusted repositories, run the whole verification step (or the
whole backend) inside an additional container/VM boundary with no
outbound network access.

## 8. Installation

```bash
git clone <this-repo>
cd agentic-ai-code-review
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: set ANTHROPIC_API_KEY and (recommended) GITHUB_TOKEN
```

## 9. Environment variables

See `.env.example` for the full list with comments. Required for a real
(non-dry-run) analysis: `ANTHROPIC_API_KEY`. Strongly recommended:
`GITHUB_TOKEN` (60 → 5000 requests/hour).

### GitHub token setup
GitHub → Settings → Developer settings → Personal access tokens → generate
a token with `repo` read scope (public repos work with no token, just a
much lower rate limit).

### Anthropic API key setup
https://console.anthropic.com/ → API Keys → Create Key.

## 10. Database setup

SQLite is created automatically on first run at the path in
`DATABASE_URL` (default `./data/agentic_review.db`). No migration step is
needed for the prototype. To switch to PostgreSQL: `pip install
"psycopg[binary]"` and set `DATABASE_URL=postgresql+psycopg://user:pass@host/db`
— no model changes required (`backend/app/database/models.py` uses only
portable column types).

## 11. Running the backend

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```
Visit `http://localhost:8000` — the FastAPI app serves the dashboard
(`frontend/`) as static files and the API under `/api`.
API docs: `http://localhost:8000/docs`.

## 12. Running the frontend

**Practical decision, documented per the project's own instructions:**
this build environment has no network access to run `npm install` /
`vite build`, so the dashboard is a **React app loaded via CDN with
Babel-standalone in-browser transpilation** — a real, functional React
app with zero build step, served directly as static files by FastAPI (see
§11). This is the most robust option that could be fully authored and
sanity-checked (brace/paren balance, manual code review) in this sandbox.

To upgrade it to a proper Vite/CRA build later (recommended for
production — CDN Babel transpilation has real runtime cost):
```bash
npm create vite@latest frontend-build -- --template react
# move frontend/app.js's component logic into frontend-build/src/App.jsx
# point frontend-build's dev server / build output at the FastAPI static mount
```

For local development against a separately-hosted API, set
`window.API_BASE` in `frontend/index.html` before `app.js` loads, or serve
from the same origin as the backend (the default).

## 13. Running tests

```bash
cd backend
pytest -v
```

**Honesty note about this environment specifically:** the container this
project was built in has no outbound network access (no `pip install`,
no live GitHub/Anthropic calls). Because of that:
- `tests/test_python_ast.py`, `test_change_detection.py`,
  `test_dependency.py`, `test_json_utils.py`, `test_tools.py`,
  `test_github_client.py` (HTTP mocked), `test_llm_provider.py`
  (Anthropic SDK faked via `sys.modules`), `test_verification_runner.py`,
  and `test_pipeline_e2e.py` (full 8-agent pipeline against a **real**
  local git repo, with a fake `LLMProvider` standing in for the
  network-inaccessible Claude API) were all **actually executed and
  passed** in this sandbox using a tiny local `pytest`-shim for
  `pytest.raises`/`fixture`/`monkeypatch` where the real `pytest` package
  itself couldn't be installed.
- `test_api.py` needs `fastapi`/`httpx` (not installable here) and is
  verified by `py_compile` + manual review only in this sandbox — run it
  for real with `pip install -r requirements.txt && pytest tests/test_api.py`.
- Every `.py` file in the project passes `python -m py_compile`.
- No live call to the real GitHub or Anthropic API has been made from
  this environment. Once you add real credentials, exercise the real
  path end-to-end via the dashboard or:
  ```bash
  curl -X POST localhost:8000/api/repositories -d '{"owner":"octocat","name":"Hello-World"}' -H 'Content-Type: application/json'
  curl -X POST localhost:8000/api/commits/<sha>/analyze -d '{"owner":"octocat","repo":"Hello-World","sha":"<sha>"}' -H 'Content-Type: application/json'
  ```

## 14. Docker setup

```bash
cp .env.example .env   # fill in your keys
docker compose up --build
```
Visit `http://localhost:8000`.

## 15. Example workflow

1. Open the dashboard, enter a repository owner/name, click "Load repository".
2. Click a commit in the sidebar.
3. The 8-agent pipeline runs; progress appears per-agent, followed by
   structured issues with severity/category, evidence, suggested fix,
   generated patch, and (if a patch was generated) real verification
   status.

## 16. Example review / acceptance-test walkthrough

`scripts/create_demo_repo.sh` builds a 3-commit local repository matching
the spec's acceptance test exactly:
1. **Initial implementation** — a small `Calculator` module.
2. **Introduce intentional bug** — `divide(a, b)` silently swaps its
   operands (`b / a` instead of `a / b`) with no zero-guard.
3. **Fix bug + unrelated change** — fixes `divide()` **and**, in the same
   commit, adds an unrelated `logger_util.py` — exercising the
   "independent changes in one commit" grouping.

This exact scenario is what `tests/test_pipeline_e2e.py` runs through the
real 8-agent pipeline end-to-end (with a scripted `FakeLLMProvider`
standing in only for the network-inaccessible Claude API in this sandbox)
and asserts:
- the bug is isolated to exactly the `divide` symbol,
- one `CRITICAL` issue is produced,
- a patch is generated,
- the patch is **actually verified by running the real test suite** in an
  isolated clone, and reports `VERIFIED`.

Run it yourself:
```bash
cd backend && pytest tests/test_pipeline_e2e.py -v -s
```

## 17. Limitations

- **AST/symbol parsing currently supports Python only.** The architecture
  (`parsing/base.py`'s `get_parser()` registry, the language-agnostic
  `Symbol` dataclass) is built so JS/TS/Java/Go/C++ parsers can be added
  without touching change detection, dependency analysis, or the agents —
  but only the Python parser (`parsing/python_ast.py`) is implemented.
  Files in unsupported languages fall back to whole-file tracking
  (`detect_changed_symbols_no_parser`) rather than symbol-level analysis.
- **Cross-file dependency resolution is name-based, not import-resolved.**
  `analysis/dependency.py` matches callers/callees by symbol *name* across
  all indexed files. This is fast and works well within one file/module,
  but will over-match same-named functions in unrelated modules in very
  large repositories. `extract_imports()` already collects the data needed
  for a proper import-graph resolver — a natural follow-up enhancement.
- **The Dependency Analyzer does not walk the full repository tree.** It
  targets common test-file naming conventions rather than fetching the
  entire Git tree via the Trees API (which would be a real, possibly
  expensive, extra call per commit). For repos with unconventional test
  layouts, the Review Agent can still request specific files by path via
  the `read_file` tool if it knows to look for them.
- **Verification requires a Python project with a recognizable test setup**
  (`pytest.ini`/`pyproject.toml`/`setup.py`/`setup.cfg`/`requirements.txt`)
  and one of the allowlisted test runners on the host; anything else
  correctly reports `UNABLE_TO_VERIFY` rather than a false `VERIFIED`.
- **No sandboxed network boundary around the verification subprocess** in
  this codebase itself — see the security note in §7.
- **Frontend has no build step** — see §12 for why and how to upgrade it.
- **This sandbox had no network access**, so no live GitHub/Anthropic API
  call could be made while building this — see §13 for exactly what was
  and wasn't executed here.

## 18. Future enhancements

- JS/TS/Java/Go/C++ AST parsers using the existing `Symbol` model.
- Import-resolved (not name-matched) cross-file dependency graph.
- Full repo-tree indexing via the Git Trees API for large-repo dependency
  analysis.
- Containerized (gVisor/firecracker-style) verification sandbox with a
  hard network boundary.
- Streaming agent progress over WebSocket instead of polling.
- PostgreSQL-backed multi-user deployment with auth.
- A proper Vite/CRA-built frontend replacing the CDN/Babel-standalone one.

## 19. Project structure

```
agentic-ai-code-review/
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI app
│   │   ├── config.py               # env-var settings
│   │   ├── logging_config.py
│   │   ├── api/
│   │   │   ├── routes.py                    # existing commit-review endpoints
│   │   │   └── implementation_routes.py     # implementation-feature endpoints
│   │   ├── agents/                 # the 8 review agents + orchestrator + tools
│   │   │   ├── implementation_agent.py       # implementation feature's agentic loop
│   │   │   └── implementation_tools.py       # its repo-browsing tools
│   │   ├── analysis/                # change_detection.py, dependency.py
│   │   ├── parsing/                 # base.py, python_ast.py
│   │   ├── implementation/          # implementation feature's own pipeline
│   │   │   ├── context.py                    # ImplementationContext (shared state)
│   │   │   ├── file_discovery.py              # relevance scoring
│   │   │   ├── context_builder.py             # hierarchical context assembly
│   │   │   ├── implementation_planner.py      # plan normalization
│   │   │   ├── suggestion_engine.py           # suggestion normalization/filtering
│   │   │   ├── diff_builder.py                # unified diff construction/validation
│   │   │   └── patch_generator.py             # combines model patch + new-file diffs
│   │   ├── github/
│   │   │   ├── client.py                     # low-level GitHub REST wrapper
│   │   │   ├── repository_fetcher.py          # ref resolution + full tree
│   │   │   ├── file_fetcher.py                # lazy, cached per-request file content
│   │   │   └── tree_parser.py                 # tree filtering/annotation
│   │   ├── llm/
│   │   │   ├── provider.py, anthropic_provider.py, json_utils.py
│   │   │   └── prompts/implementation_prompt.py
│   │   ├── database/                # models.py, session.py
│   │   ├── schemas/                 # schemas.py, implementation.py, code_context.py, patch.py
│   │   └── services/                # analysis_service.py, implementation_service.py, verification_runner.py
│   ├── tests/
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── app.js                      # CodeReviewApp + ImplementationApp, tab-switched by RootApp
│   └── styles.css
├── scripts/
│   └── create_demo_repo.sh
├── data/                           # SQLite db + demo repo live here (gitignored)
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
└── LICENSE
```

## 20. Relationship to the originally uploaded project

The ZIP originally provided contained a different, smaller research tool
(a GitHub PR-sampling + single-shot AI-verdict pipeline for measuring
review accuracy, with a hard-coded `MAX_DIFF_CHARS = 12000` truncation).
It was inspected in full before any decisions were made; per this
project's much larger requirements (AST-based symbol analysis, multi-
agent pipeline, verification, persistence, dashboard, and explicitly
removing any fixed line-limit), it was used only as reference for the
config/error-handling style and rebuilt from scratch rather than
incrementally patched, since the two systems' architectures do not share
a meaningful amount of reusable code.

## 21. Implementation feature — AI repository code fetching + implementation suggestions

Beyond the commit-review pipeline (§1–20), the system can also take a
**free-text feature request** ("Add JWT authentication to the user API")
against an entire repository at a branch/tag/commit, and produce a
grounded implementation plan, precise suggestions, and a proposed unified
diff — without ever modifying the real GitHub repository.

### 21.1 Pipeline

```mermaid
flowchart LR
    A[POST /implementation/analyze] --> B[RepositoryFetcher\nresolve ref, fetch full tree]
    B --> C[file_discovery\n2-phase relevance scoring]
    C --> D[context_builder\nrepo→files→symbols→deps→tests]
    D --> E[ImplementationAgent\nClaude + repo-browsing tools]
    E --> F[implementation_planner\n+ suggestion_engine]
    E --> G[patch_generator\nmodel diff + deterministic new-file diffs]
    F --> H[(SQLite:\nImplementationRequest,\nPlan, Suggestions, Patch)]
    G --> H
    H --> I[POST /implementation/verify\nVerificationRunner: clone → apply → test]
```

- **Repository fetching** (`github/repository_fetcher.py`, `file_fetcher.py`,
  `tree_parser.py`) — built on the existing `github/client.py` (extended
  with `resolve_ref`/`get_tree`/`list_directory`, reusing all of its
  retry/backoff/rate-limit handling). The full repo tree is fetched once
  via the Git Trees API; individual file content is fetched **lazily and
  cached per request** — only files that are actually relevant (or that
  the agent asks to read) are downloaded.
- **File discovery** (`implementation/file_discovery.py`) — two-phase
  relevance scoring: path/filename token overlap first (cheap, no
  fetches), then a content-aware re-score (imports, defined symbol names)
  for the strongest candidates. Never returns "every file."
- **Context building** (`implementation/context_builder.py`) — indexes
  each relevant file's symbols via the existing AST layer
  (`parsing/python_ast.py`) and reuses `analysis/dependency.py`'s
  `SymbolIndex`/`build_context_bundle` to pull in callers/callees of any
  matched symbol, plus related tests (naming convention) and config
  files — the same hierarchical retrieval strategy the review pipeline
  already uses.
- **Implementation agent** (`agents/implementation_agent.py` +
  `agents/implementation_tools.py`) — the same tool-loop pattern as the
  existing `ReviewAgent`: Claude gets the assembled context plus tools
  (`read_file`, `read_function`, `find_callers`/`callees`, `list_directory`,
  `search_filenames`/`search_content`, `get_tests_for_file`) and decides
  for itself what else to read before returning one structured JSON
  response (system prompt: `llm/prompts/implementation_prompt.py`).
- **Planning/suggestions** (`implementation/implementation_planner.py`,
  `suggestion_engine.py`) — normalize the agent's plan/suggestions into
  a predictable shape, and **drop vague suggestions** (no file, no
  concrete `proposed_change`, or a hand-wavy phrase like "improve
  security") rather than passing them through.
- **Patch generation** (`implementation/diff_builder.py`,
  `patch_generator.py`) — combines the model's own unified diff (for
  modified files) with **deterministically-built** diffs (Python's
  `difflib`, not the LLM) for any `files_to_create` entry the model's
  patch didn't already cover, then structurally validates the result
  before it's ever handed to `git apply`.
- **Verification** — reuses `services/verification_runner.py` **as-is**:
  it already clones into an isolated temp dir, checks out a ref, applies
  a patch, and runs the repo's real test suite with the same safety
  bounds (timeout, minimal env, allowlisted commands) documented in §7.
  The real GitHub repository is never written to.

### 21.2 API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/repositories/{owner}/{repo}/tree` | Full filtered repo tree at a ref |
| GET | `/api/repositories/{owner}/{repo}/files` | Flat file listing (no content) |
| GET | `/api/repositories/{owner}/{repo}/files/{path}` | One file's content at a ref |
| POST | `/api/implementation/analyze` | Run the full pipeline for a feature request |
| POST | `/api/implementation/suggest` | Return an existing request's suggestions |
| POST | `/api/implementation/generate-patch` | Reassemble the patch from stored plan output (no new LLM call) |
| POST | `/api/implementation/verify` | Clone/apply/test the patch in an isolated workspace |
| GET | `/api/implementation/{id}` | Full record: request + plan + suggestions + latest patch + latest verification |
| GET | `/api/implementation?repository_id=` | List past implementation requests |

### 21.3 Example

```bash
curl -X POST http://localhost:8000/api/implementation/analyze \
  -H 'Content-Type: application/json' \
  -d '{
    "owner": "your-org", "repo": "your-repo",
    "request": "Add JWT authentication to the user API"
  }'
```

```json
{
  "id": 1, "status": "PATCHED", "summary": "Add JWT auth to the user API.",
  "plan": {
    "objective": "Add JWT authentication",
    "files_to_modify": ["backend/app/api/users.py"],
    "files_to_create": ["backend/app/auth/jwt_utils.py"],
    "steps": ["Add jwt_utils.py with encode/decode helpers", "..."],
    "expected_behavior": "Requests without a valid JWT return HTTP 401."
  },
  "suggestions": [{
    "file": "backend/app/api/users.py", "symbol": "get_user",
    "change_type": "required",
    "proposed_change": "Add the existing authentication dependency to this endpoint.",
    "reason": "The endpoint currently has no authentication dependency.",
    "expected_behavior": "Requests without a valid JWT should return HTTP 401."
  }],
  "latest_patch": { "is_syntactically_valid": true, "patch_text": "--- a/backend/app/api/users.py\n..." }
}
```

### 21.4 Frontend

`frontend/app.js` now renders a top-level "Code Review" / "Implementation"
tab switcher (`RootApp`); the Implementation tab (`ImplementationApp`) is
plain vanilla-React-via-Babel-CDN, matching the existing frontend's stack
exactly (no separate `.jsx` build, since this project doesn't use one) —
repository/ref/request fields → Analyze → tabbed panel for Plan / Files /
Suggestions / Patch (with a "Regenerate patch" action) / Verification
(with a "Run verification" action).

### 21.5 Running its tests

```bash
pytest backend/tests/test_tree_parser.py backend/tests/test_file_discovery.py \
       backend/tests/test_diff_builder.py backend/tests/test_patch_generator.py \
       backend/tests/test_suggestion_engine.py backend/tests/test_implementation_planner.py \
       backend/tests/test_context_builder.py backend/tests/test_implementation_tools.py \
       backend/tests/test_implementation_agent.py backend/tests/test_implementation_api.py
```

The pure-logic suites (tree parsing, file discovery, diff building, patch
generation, suggestion filtering, plan normalization, context building,
tool executor, and the agent's tool loop) were additionally executed
directly against real interpreter behavior in this environment. The
API-level tests (`test_implementation_api.py`) mock `GitHubClient` and
`AnthropicProvider` the same way `test_api.py` does for the existing
pipeline, so run them with `pip install -r requirements.txt` in an
environment with outbound network access.

### 21.6 Limitations specific to this feature

- Relevance scoring is heuristic (token overlap + AST symbol/import
  matches), not semantic/embedding-based — an unusually-named file for a
  request's vocabulary may need the agent's own `search_filenames`/
  `search_content` tools to find it rather than showing up in the initial
  shortlist.
- Cross-file symbol resolution (`analysis/dependency.py`) matches by
  *name*, not real import-graph resolution — inherited from the existing
  review pipeline's documented simplification (§17).
- The agent's patch must apply cleanly with `git apply` against the exact
  file content it was given; if the repository changes between context
  building and verification, the patch may not apply (verification will
  correctly report `UNABLE_TO_VERIFY` rather than falsely claiming
  success).
- Verification requires a recognizable Python test setup in the target
  repository (same allowlisted-command behavior as §7); other languages'
  test suites are not executed.
