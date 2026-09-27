const { useState, useEffect, useCallback } = React;

const API_BASE = (window.API_BASE || "/api");

async function api(path, options) {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.detail || `Request failed (${resp.status})`);
  }
  return data;
}

const AGENT_STEPS = [
  ["repository_explorer", "Repository loaded"],
  ["change_analyzer", "Changes identified"],
  ["dependency_analyzer", "Dependencies analyzed"],
  ["code_reviewer", "AI review completed"],
  ["debugging_agent", "Findings double-checked"],
  ["fix_generator", "Fix generated"],
  ["verification_agent", "Verification completed"],
  ["report_generator", "Report compiled"],
];

function ProgressList({ trace }) {
  const doneAgents = new Set((trace || []).map((t) => t.agent));
  return (
    <ul className="progress-list">
      {AGENT_STEPS.map(([agent, label]) => (
        <li key={agent} className={doneAgents.has(agent) ? "done" : ""}>
          <span>{doneAgents.has(agent) ? "\u2713" : "\u25CB"}</span>
          <span className="agent-tag">{agent}</span>
          <span>{label}</span>
        </li>
      ))}
    </ul>
  );
}

function IssueCard({ issue }) {
  const [tab, setTab] = useState("overview");
  return (
    <div className="issue-card">
      <div className="issue-title">{issue.title}</div>
      <div className="issue-meta">
        <span className={`badge ${issue.severity}`}>{issue.severity}</span>
        <span className="badge status-APPROVED_WITH_SUGGESTIONS">{issue.category}</span>
        <span className="muted">{issue.file}{issue.function ? ` :: ${issue.function}` : ""}{issue.line ? `:${issue.line}` : ""}</span>
        <span className="muted">confidence {Math.round((issue.confidence || 0) * 100)}%</span>
        {issue.verification_status && (
          <span className={`badge status-${issue.verification_status}`}>{issue.verification_status}</span>
        )}
      </div>

      <div className="tabs">
        {["overview", "code", "patch"].map((t) => (
          <div key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t === "overview" ? "Overview" : t === "code" ? "Code" : "Patch"}
          </div>
        ))}
      </div>

      {tab === "overview" && (
        <div>
          <div className="issue-section"><b>Description:</b> {issue.description}</div>
          <div className="issue-section"><b>Evidence:</b> {issue.evidence}</div>
          <div className="issue-section"><b>Impact:</b> {issue.impact}</div>
          {issue.suggested_fix_explanation && (
            <div className="issue-section"><b>Suggested fix:</b> {issue.suggested_fix_explanation}</div>
          )}
          {issue.tests_required && (
            <div className="issue-section"><b>Tests required:</b> {issue.tests_required}</div>
          )}
        </div>
      )}

      {tab === "code" && (
        <div>
          <div className="issue-section"><b>Existing code</b></div>
          <pre className="code-block">{issue.existing_code_for_fix || "(not available)"}</pre>
          <div className="issue-section"><b>Suggested code</b></div>
          <pre className="code-block">{issue.suggested_code || "(not available)"}</pre>
        </div>
      )}

      {tab === "patch" && (
        <div>
          <pre className="code-block">{issue.suggested_patch || "No patch generated (confidence below threshold, or not needed)."}</pre>
          {issue.verification_details && (
            <>
              <div className="issue-section" style={{ marginTop: 10 }}><b>Verification output</b></div>
              <pre className="code-block">{issue.verification_details}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ReviewPanel({ review }) {
  if (!review) return null;
  return (
    <div className="panel">
      <h2>
        Review <span className={`badge status-${review.status}`}>{review.status}</span>
      </h2>
      <div className="summary-line">{review.summary}</div>
      <div className="muted">Overall confidence: {Math.round((review.confidence || 0) * 100)}%</div>

      <h3 style={{ marginTop: 20 }}>Agent progress</h3>
      <ProgressList trace={review.agent_trace} />

      <h3 style={{ marginTop: 20 }}>Issues ({review.issues.length})</h3>
      {review.issues.length === 0 && <div className="empty-state">No issues found.</div>}
      {review.issues.map((issue) => (
        <IssueCard key={issue.id} issue={issue} />
      ))}
    </div>
  );
}

function CodeReviewApp() {
  const [owner, setOwner] = useState("");
  const [repoName, setRepoName] = useState("");
  const [branch, setBranch] = useState("");
  const [repo, setRepo] = useState(null);
  const [commits, setCommits] = useState([]);
  const [selectedSha, setSelectedSha] = useState(null);
  const [review, setReview] = useState(null);
  const [loadingRepo, setLoadingRepo] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState(null);
  const [runVerification, setRunVerification] = useState(true);

  const loadRepo = useCallback(async () => {
    setError(null);
    setLoadingRepo(true);
    try {
      const record = await api("/repositories", {
        method: "POST",
        body: JSON.stringify({ owner, name: repoName, branch: branch || undefined }),
      });
      setRepo(record);
      const commitList = await api(`/repositories/${record.id}/github-commits`);
      setCommits(commitList);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingRepo(false);
    }
  }, [owner, repoName, branch]);

  const analyze = useCallback(async (sha) => {
    if (!repo) return;
    setError(null);
    setSelectedSha(sha);
    setReview(null);
    setAnalyzing(true);
    try {
      const result = await api(`/commits/${sha}/analyze`, {
        method: "POST",
        body: JSON.stringify({ owner: repo.owner, repo: repo.name, sha, run_verification: runVerification }),
      });
      setReview(result);
    } catch (e) {
      setError(e.message);
    } finally {
      setAnalyzing(false);
    }
  }, [repo, runVerification]);

  return (
    <div className="app">
      <div className="sidebar">
        <div className="brand">
          <span>Agentic Code Review</span>
          <span className="brand-badge">AI</span>
        </div>

        <div className="field">
          <label>Repository owner</label>
          <input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="e.g. octocat" />
        </div>
        <div className="field">
          <label>Repository name</label>
          <input value={repoName} onChange={(e) => setRepoName(e.target.value)} placeholder="e.g. Hello-World" />
        </div>
        <div className="field">
          <label>Branch (optional)</label>
          <input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="default branch" />
        </div>
        <button disabled={!owner || !repoName || loadingRepo} onClick={loadRepo}>
          {loadingRepo ? "Loading..." : "Load repository"}
        </button>

        <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <input type="checkbox" checked={runVerification} onChange={(e) => setRunVerification(e.target.checked)} style={{ width: "auto" }} />
          <span>Run verification (execute tests)</span>
        </label>

        {repo && (
          <div>
            <h3 style={{ fontSize: 13, color: "var(--muted)", marginBottom: 8 }}>
              Commits on {repo.owner}/{repo.name}
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: "50vh", overflowY: "auto" }}>
              {commits.map((c) => (
                <div key={c.sha} className="commit-row" onClick={() => analyze(c.sha)}>
                  <div className="commit-sha">{c.sha.slice(0, 7)}</div>
                  <div>{c.message}</div>
                  <div className="muted">{c.author}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="main">
        {error && <div className="error-banner">{error}</div>}

        {!repo && (
          <div className="empty-state">
            Enter a GitHub repository on the left and click "Load repository" to begin.
            <br />
            Analysis calls the real GitHub and Anthropic APIs configured on the server (.env).
          </div>
        )}

        {repo && !selectedSha && (
          <div className="empty-state">Select a commit from the list to analyze it.</div>
        )}

        {selectedSha && analyzing && (
          <div className="panel">
            <h2>Analyzing {selectedSha.slice(0, 7)}...</h2>
            <div className="muted">Running the 8-agent pipeline. This can take a while for large commits.</div>
          </div>
        )}

        {review && <ReviewPanel review={review} />}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ //
// Implementation feature: AI repository code fetching + implementation
// suggestions (POST /api/implementation/analyze and friends).
// ------------------------------------------------------------------ //

function ChangeTypeBadge({ type }) {
  return <span className={`badge change-${type}`}>{type}</span>;
}

function SuggestionCard({ s }) {
  return (
    <div className="issue-card">
      <div className="issue-title">
        {s.file}{s.symbol ? ` :: ${s.symbol}` : ""}
      </div>
      <div className="issue-meta">
        <ChangeTypeBadge type={s.change_type} />
        {s.location && <span className="muted">{s.location}</span>}
      </div>
      {s.current_behavior && (
        <div className="issue-section"><b>Current behavior:</b> {s.current_behavior}</div>
      )}
      <div className="issue-section"><b>Proposed change:</b> {s.proposed_change}</div>
      <div className="issue-section"><b>Reason:</b> {s.reason}</div>
      {s.expected_behavior && (
        <div className="issue-section"><b>Expected behavior:</b> {s.expected_behavior}</div>
      )}
    </div>
  );
}

function ImplementationPanel({ record, onGeneratePatch, onVerify, patching, verifying }) {
  const [tab, setTab] = useState("plan");
  if (!record) return null;
  const plan = record.plan;
  const patch = record.latest_patch;
  const verification = record.latest_verification;

  return (
    <div className="panel">
      <h2>
        Implementation <span className={`badge status-${record.status}`}>{record.status}</span>
      </h2>
      {record.summary && <div className="summary-line">{record.summary}</div>}
      {record.error_message && <div className="error-banner">{record.error_message}</div>}

      <div className="tabs">
        {["plan", "files", "suggestions", "patch", "verification"].map((t) => (
          <div key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t[0].toUpperCase() + t.slice(1)}
          </div>
        ))}
      </div>

      {tab === "plan" && plan && (
        <div>
          <div className="issue-section"><b>Objective:</b> {plan.objective}</div>
          {plan.expected_behavior && (
            <div className="issue-section"><b>Expected behavior:</b> {plan.expected_behavior}</div>
          )}
          {plan.assumptions.length > 0 && (
            <div className="issue-section"><b>Assumptions:</b> {plan.assumptions.join("; ")}</div>
          )}
          {plan.steps.length > 0 && (
            <div className="issue-section">
              <b>Steps:</b>
              <ol>{plan.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
            </div>
          )}
          <div className="issue-section"><b>Files to modify:</b> {plan.files_to_modify.join(", ") || "(none)"}</div>
          <div className="issue-section"><b>Files to create:</b> {plan.files_to_create.join(", ") || "(none)"}</div>
          {plan.tests.length > 0 && (
            <div className="issue-section"><b>Tests to add:</b> {plan.tests.join("; ")}</div>
          )}
          {plan.risks.length > 0 && (
            <div className="issue-section"><b>Risks:</b> {plan.risks.join("; ")}</div>
          )}
        </div>
      )}
      {tab === "plan" && !plan && <div className="empty-state">No plan produced.</div>}

      {tab === "files" && (
        <div>
          {(record.relevant_files || []).map((f) => (
            <div key={f.path} className="commit-row" style={{ cursor: "default" }}>
              <div className="commit-sha">{f.path}</div>
              <div className="muted">score {f.score}{f.is_test ? " · test" : ""}{f.is_config ? " · config" : ""}</div>
              {f.reasons && f.reasons.length > 0 && <div className="muted">{f.reasons.join("; ")}</div>}
            </div>
          ))}
          {(!record.relevant_files || record.relevant_files.length === 0) && (
            <div className="empty-state">No relevant files recorded.</div>
          )}
        </div>
      )}

      {tab === "suggestions" && (
        <div>
          {(record.suggestions || []).length === 0 && <div className="empty-state">No suggestions.</div>}
          {(record.suggestions || []).map((s) => <SuggestionCard key={s.id} s={s} />)}
        </div>
      )}

      {tab === "patch" && (
        <div>
          <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
            <button className="secondary" disabled={patching} onClick={onGeneratePatch}>
              {patching ? "Regenerating..." : "Regenerate patch"}
            </button>
          </div>
          {patch ? (
            <>
              <div className="muted" style={{ marginBottom: 6 }}>
                {patch.is_syntactically_valid ? "Syntactically valid diff" : "Diff has validation issues"}
                {patch.new_files_added_deterministically.length > 0 &&
                  ` · new files added deterministically: ${patch.new_files_added_deterministically.join(", ")}`}
              </div>
              {patch.validation_errors.length > 0 && (
                <div className="error-banner">{patch.validation_errors.join(" | ")}</div>
              )}
              <pre className="code-block">{patch.patch_text || "(empty patch)"}</pre>
            </>
          ) : (
            <div className="empty-state">No patch generated yet.</div>
          )}
        </div>
      )}

      {tab === "verification" && (
        <div>
          <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
            <button disabled={verifying || !patch} onClick={onVerify}>
              {verifying ? "Verifying..." : "Run verification"}
            </button>
          </div>
          {verification ? (
            <div>
              <div className="issue-meta">
                <span className={`badge status-${verification.status}`}>{verification.status}</span>
                <span className="muted">patch applied: {verification.patch_applied ? "yes" : "no"}</span>
                <span className="muted">{verification.tests_passed} passed / {verification.tests_failed} failed</span>
              </div>
              {verification.command_run && <div className="muted">command: {verification.command_run}</div>}
              <pre className="code-block">{verification.details || "(no output)"}</pre>
            </div>
          ) : (
            <div className="empty-state">Not verified yet. Verification clones the real repository into an isolated temporary workspace and runs its test suite -- it never modifies your actual GitHub repository.</div>
          )}
        </div>
      )}
    </div>
  );
}

function ImplementationApp() {
  const [owner, setOwner] = useState("");
  const [repoName, setRepoName] = useState("");
  const [ref, setRef] = useState("");
  const [requestText, setRequestText] = useState("");
  const [targetFile, setTargetFile] = useState("");
  const [targetSymbol, setTargetSymbol] = useState("");
  const [runVerification, setRunVerification] = useState(false);
  const [record, setRecord] = useState(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [patching, setPatching] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState(null);
  const [history, setHistory] = useState([]);

  const analyze = useCallback(async () => {
    setError(null);
    setAnalyzing(true);
    setRecord(null);
    try {
      const result = await api("/implementation/analyze", {
        method: "POST",
        body: JSON.stringify({
          owner, repo: repoName, ref: ref || undefined, request: requestText,
          target_file: targetFile || undefined, target_symbol: targetSymbol || undefined,
          run_verification: runVerification,
        }),
      });
      setRecord(result);
      setHistory((h) => [result, ...h.filter((r) => r.id !== result.id)].slice(0, 15));
    } catch (e) {
      setError(e.message);
    } finally {
      setAnalyzing(false);
    }
  }, [owner, repoName, ref, requestText, targetFile, targetSymbol, runVerification]);

  const regeneratePatch = useCallback(async () => {
    if (!record) return;
    setPatching(true);
    setError(null);
    try {
      await api("/implementation/generate-patch", {
        method: "POST",
        body: JSON.stringify({ implementation_id: record.id }),
      });
      const refreshed = await api(`/implementation/${record.id}`);
      setRecord(refreshed);
    } catch (e) {
      setError(e.message);
    } finally {
      setPatching(false);
    }
  }, [record]);

  const verify = useCallback(async () => {
    if (!record) return;
    setVerifying(true);
    setError(null);
    try {
      await api("/implementation/verify", {
        method: "POST",
        body: JSON.stringify({ implementation_id: record.id }),
      });
      const refreshed = await api(`/implementation/${record.id}`);
      setRecord(refreshed);
    } catch (e) {
      setError(e.message);
    } finally {
      setVerifying(false);
    }
  }, [record]);

  return (
    <div className="app">
      <div className="sidebar">
        <div className="brand">
          <span>Implementation</span>
          <span className="brand-badge">AI</span>
        </div>

        <div className="field">
          <label>Repository owner</label>
          <input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="e.g. octocat" />
        </div>
        <div className="field">
          <label>Repository name</label>
          <input value={repoName} onChange={(e) => setRepoName(e.target.value)} placeholder="e.g. Hello-World" />
        </div>
        <div className="field">
          <label>Branch / tag / commit SHA (optional)</label>
          <input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="default branch" />
        </div>
        <div className="field">
          <label>Implementation request</label>
          <textarea
            value={requestText}
            onChange={(e) => setRequestText(e.target.value)}
            placeholder="e.g. Add JWT authentication to the user API."
            rows={4}
            style={{ background: "var(--panel-2)", border: "1px solid var(--border)", borderRadius: 8, padding: "8px 10px", color: "var(--text)", fontSize: 14, resize: "vertical" }}
          />
        </div>
        <div className="field">
          <label>Target file (optional)</label>
          <input value={targetFile} onChange={(e) => setTargetFile(e.target.value)} placeholder="e.g. backend/app/api/users.py" />
        </div>
        <div className="field">
          <label>Target function/class (optional)</label>
          <input value={targetSymbol} onChange={(e) => setTargetSymbol(e.target.value)} placeholder="e.g. get_user" />
        </div>

        <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <input type="checkbox" checked={runVerification} onChange={(e) => setRunVerification(e.target.checked)} style={{ width: "auto" }} />
          <span>Run verification immediately after analysis</span>
        </label>

        <button disabled={!owner || !repoName || !requestText || analyzing} onClick={analyze}>
          {analyzing ? "Analyzing..." : "Analyze"}
        </button>

        {history.length > 0 && (
          <div>
            <h3 style={{ fontSize: 13, color: "var(--muted)", marginBottom: 8 }}>Recent requests</h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: "30vh", overflowY: "auto" }}>
              {history.map((r) => (
                <div key={r.id} className="commit-row" onClick={() => setRecord(r)}>
                  <div className="commit-sha">#{r.id} · {r.status}</div>
                  <div className="muted">{r.request_text.slice(0, 60)}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="main">
        {error && <div className="error-banner">{error}</div>}

        {!record && !analyzing && (
          <div className="empty-state">
            Enter a repository and describe the feature to implement, then click "Analyze".
            <br />
            The agent fetches real repository files via GitHub, builds AST-based context, and returns a
            plan, precise suggestions, and a proposed patch -- your real repository is never modified.
          </div>
        )}

        {analyzing && (
          <div className="panel">
            <h2>Analyzing repository...</h2>
            <div className="muted">Fetching relevant files, building context, and running the implementation agent. This can take a while for large requests.</div>
          </div>
        )}

        {record && !analyzing && (
          <ImplementationPanel
            record={record}
            onGeneratePatch={regeneratePatch}
            onVerify={verify}
            patching={patching}
            verifying={verifying}
          />
        )}
      </div>
    </div>
  );
}

function RootApp() {
  const [tab, setTab] = useState("review");
  return (
    <div>
      <div className="top-nav">
        <div className={`top-nav-item ${tab === "review" ? "active" : ""}`} onClick={() => setTab("review")}>
          Code Review
        </div>
        <div className={`top-nav-item ${tab === "implementation" ? "active" : ""}`} onClick={() => setTab("implementation")}>
          Implementation
        </div>
      </div>
      {tab === "review" ? <CodeReviewApp /> : <ImplementationApp />}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<RootApp />);
