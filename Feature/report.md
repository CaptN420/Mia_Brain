# Dogfood QA Report

**Target:** http://localhost:8501 (Captn Dashboard)
**Date:** 2026-08-15
**Scope:** Full dashboard — project loading, analysis pipeline, AI fallback config, workflow buttons, log viewer, file listing
**Tester:** Hermes Agent (automated exploratory QA)

---

## Executive Summary

| Severity | Count |
|----------|-------|
| 🔴 Critical | 0 |
| 🟠 High | 3 |
| 🟡 Medium | 4 |
| 🔵 Low | 4 |
| **Total** | **11** |

**Overall Assessment:** The dashboard loads and renders correctly, but the core workflow (Analyze → Generate Fix → Apply) is non-functional due to broken async pipeline handling. The UI gives false impressions of progress (e.g., "Analysis complete" after 2 seconds with no actual analysis). Several configuration controls are cosmetic only.

---

## Issues

### Issue #1: Analyze Button Produces No Real Output — Fake Completion After 2s

| Field | Value |
|-------|-------|
| **Severity** | 🟠 High |
| **Category** | Functional |
| **URL** | http://localhost:8501 |

**Description:**
Clicking "Analyze" triggers `run_analysis_pipeline()` which dispatches workers via the MessageBus. However, after a hardcoded `time.sleep(2)`, the UI sets `workflow_state["analysis_complete"] = True` regardless of whether any worker actually responded. No analysis report is generated, no findings are shown, and the log viewer remains empty. The button appears to work but produces nothing.

**Steps to Reproduce:**
1. Mount a project (e.g., `C:/Users/edoud/Documents/Hermes-Home/workspace/CaptN-BRAIN`)
2. Click "Analyze"
3. Wait 2 seconds
4. Observe that "Analysis Report & Diff Viewer" still shows "No analysis report found"

**Expected Behavior:**
The pipeline should wait for worker responses, synthesize findings via the Thinker, and display results in the Analysis Report panel. A loading indicator should show during processing.

**Actual Behavior:**
After 2 seconds the button re-enables (or stays enabled) but no analysis report appears. The log viewer shows no new entries.

**Console Errors:** None (silent failure — the most dangerous kind)

---

### Issue #2: Generate Fix Button Stays Disabled Forever

| Field | Value |
|-------|-------|
| **Severity** | 🟠 High |
| **Category** | Functional |
| **URL** | http://localhost:8501 |

**Description:**
The "Generate Fix" button requires `workflow_state["analysis_complete"] == True AND workflow_state["fix_generated"] == False`. While `analysis_complete` is set to True after the fake 2-second Analyze, the Generate Fix button still remains disabled. This is because the button's enable condition checks `generate_fix_enabled` which depends on `analysis_complete`, but clicking it sets `fix_generated = True` — however the button never becomes enabled in the first place because the state update from the async pipeline doesn't properly propagate to the UI.

**Steps to Reproduce:**
1. Mount a project
2. Click "Analyze" and wait 2 seconds
3. Observe that "Generate Fix" remains disabled

**Expected Behavior:**
After analysis completes, "Generate Fix" should become enabled.

**Actual Behavior:**
Button stays disabled indefinitely. The entire fix generation pipeline is inaccessible.

---

### Issue #3: Apply Fix and Rollback Buttons Permanently Disabled

| Field | Value |
|-------|-------|
| **Severity** | 🟠 High |
| **Category** | Functional |
| **URL** | http://localhost:8501 |

**Description:**
"Apply Fix" requires `fix_generated AND fix_validated`. "Rollback" requires `fix_generated`. Since Generate Fix never completes, neither button ever becomes enabled. The user has no path to apply or undo any fixes — the core value proposition of the tool is blocked.

**Steps to Reproduce:**
1. Mount a project
2. Observe that "Apply Fix" and "Rollback" are disabled from the start and never enable

**Expected Behavior:**
These buttons should become available after a fix is generated and validated.

**Actual Behavior:**
Permanently disabled. No user action can enable them.

---

### Issue #4: Restart Runtime Button Is Cosmetic Only

| Field | Value |
|-------|-------|
| **Severity** | 🟡 Medium |
| **Category** | Functional |
| **URL** | http://localhost:8501 |

**Description:**
The "Restart Runtime" button (ref e31) only calls `st.warning("Restarting runtime...")` and does nothing else. It does not restart the MessageBus, reinitialize the StateStore, reload plugins, or reset workflow state. The user is told something is happening but nothing changes.

**Steps to Reproduce:**
1. Load a project
2. Click "Restart Runtime"
3. Observe only a warning toast appears; project remains loaded, state unchanged

**Expected Behavior:**
Either actually restart the runtime (reinitialize bus, store, plugins) or disable the button until implemented.

**Actual Behavior:**
No-op — shows a warning message but the runtime continues running with identical state.

---

### Issue #5: Log Viewer Is Static — No Auto-Refresh

| Field | Value |
|-------|-------|
| **Severity** | 🟡 Medium |
| **Category** | UX |
| **URL** | http://localhost:8501 |

**Description:**
The "Live Precise Program Log" panel shows a static snapshot of the log file at render time. The caption says "Use the browser's auto-refresh or click 'Restart Runtime' to see live logs" — but Restart Runtime doesn't actually refresh anything meaningful. There is no polling mechanism, no WebSocket, and no Streamlit auto-refresh configured. Users must manually reload the entire page to see new log entries.

**Steps to Reproduce:**
1. Mount a project
2. Click "Analyze"
3. Observe log panel does not update

**Expected Behavior:**
Logs should update automatically (e.g., every 2-3 seconds via Streamlit rerun or polling).

**Actual Behavior:**
Log panel is frozen at initial render. No automatic updates.

---

### Issue #6: Model Dropdown Shows Confusing Placeholder

| Field | Value |
|-------|-------|
| **Severity** | 🟡 Medium |
| **Category** | UX |
| **URL** | http://localhost:8501 |

**Description:**
The Model selectbox defaults to "Please detect models first..." which is an internal instruction leaked into the UI. It's not a valid model selection and confuses users about what to do. The placeholder should either be removed or replaced with a user-friendly default like "gpt-4o-mini" (the hardcoded default in code).

**Steps to Reproduce:**
1. Scroll to "AI Fallback (Last-Resort LLM)" section
2. Observe Model dropdown shows "Please detect models first..."

**Expected Behavior:**
Default to the configured model ("gpt-4o-mini") or show a neutral placeholder like "Select model...".

**Actual Behavior:**
Shows an internal system message as the only option until user clicks "Detect Models".

---

### Issue #7: OpenAI Config Changes Don't Actually Restart LLM Provider

| Field | Value |
|-------|-------|
| **Severity** | 🟡 Medium |
| **Category** | Functional |
| **URL** | http://localhost:8501 |

**Description:**
Lines 475-490 of `app.py` update `st.session_state.openai_config` and recreate the `OpenAIProvider` and `FallbackLLM` when config changes. However, this only happens on rerun (e.g., after an interaction). If the user changes the API key and immediately clicks "Test Connection" on the same render, the old provider is still used. The config update and the test button are in the same block but the test uses the locally-scoped `openai_model`/`openai_api_key` variables which may not reflect the just-updated session state.

**Steps to Reproduce:**
1. Enter a new API key
2. Immediately click "Test Connection" without any other interaction
3. The test may use stale config

**Expected Behavior:**
Config changes should be applied before any subsequent action uses them.

**Actual Behavior:**
Race condition between config update and button handler on the same rerun.

---

### Issue #8: No Loading Indicator During Analysis

| Field | Value |
|-------|-------|
| **Severity** | 🟡 Medium |
| **Category** | UX |
| **URL** | http://localhost:8501 |

**Description:**
When "Analyze" is clicked, there is no spinner, progress bar, or any visual feedback that work is in progress. The button remains clickable and the UI is completely static for 2+ seconds. Users may click repeatedly thinking it didn't register.

**Steps to Reproduce:**
1. Mount a project
2. Click "Analyze"
3. Observe no loading state, button stays same appearance

**Expected Behavior:**
Show a spinner or "Analyzing..." message while the pipeline runs.

**Actual Behavior:**
No visual feedback during the 2-second sleep.

---

### Issue #9: Workspace Files List Renders `__init__.py` as Bold "init"

| Field | Value |
|-------|-------|
| **Severity** | 🔵 Low |
| **Category** | Visual |
| **URL** | http://localhost:8501 |

**Description:**
In the "Workspace Files" section, `__init__.py` files are rendered with `__init` in bold and `.py` as plain text (e.g., "**init**.py"). This is because Streamlit's `st.write(f"- {f}")` renders `__init__.py` and the markdown parser interprets the double underscores as bold markup.

**Steps to Reproduce:**
1. Mount any project with `__init__.py` files
2. Scroll to "Workspace Files"
3. Observe `__init__.py` rendered as **init**.py

**Expected Behavior:**
Files should display as plain text: `__init__.py`

**Actual Behavior:**
Rendered as **init**.py due to markdown interpretation of double underscores.

---

### Issue #10: Help Buttons Have Generic Accessibility Labels

| Field | Value |
|-------|-------|
| **Severity** | 🔵 Low |
| **Category** | Accessibility |
| **URL** | http://localhost:8501 |

**Description:**
The help icon buttons (e.g., "Help for OpenAI API Key", "Help for Model") have generic accessibility labels that don't clearly indicate they open a tooltip or popover. Screen reader users would not know what clicking these does.

**Steps to Reproduce:**
1. Navigate with a screen reader
2. Focus on any help button (e.g., ref e34)
3. Observe label is "Help for OpenAI API Key" — doesn't indicate it's a tooltip trigger

**Expected Behavior:**
Labels should include "tooltip" or "info" to indicate the interaction pattern.

**Actual Behavior:**
Generic labels that don't convey the tooltip behavior.

---

### Issue #11: No Way to Clear/Reset Workflow State Without Page Reload

| Field | Value |
|-------|-------|
| **Severity** | 🔵 Low |
| **Category** | UX |
| **URL** | http://localhost:8501 |

**Description:**
Once a project is loaded and the workflow state advances (e.g., `analysis_complete = True`), there's no "Clear" or "Reset" button to return to the initial state. The user must reload the entire page. The broken "Restart Runtime" button (Issue #4) was presumably intended for this but doesn't work.

**Steps to Reproduce:**
1. Load a project, click Analyze
2. Try to load a different project — old state persists
3. No reset mechanism available

**Expected Behavior:**
A "Clear State" or "New Project" button should reset `workflow_state` and allow starting fresh.

**Actual Behavior:**
Must manually reload the page to reset.

---

## Issues Summary Table

| # | Title | Severity | Category | URL |
|---|-------|----------|----------|-----|
| 1 | Analyze Button Produces No Real Output — Fake Completion After 2s | 🟠 High | Functional | http://localhost:8501 |
| 2 | Generate Fix Button Stays Disabled Forever | 🟠 High | Functional | http://localhost:8501 |
| 3 | Apply Fix and Rollback Buttons Permanently Disabled | 🟠 High | Functional | http://localhost:8501 |
| 4 | Restart Runtime Button Is Cosmetic Only | 🟡 Medium | Functional | http://localhost:8501 |
| 5 | Log Viewer Is Static — No Auto-Refresh | 🟡 Medium | UX | http://localhost:8501 |
| 6 | Model Dropdown Shows Confusing Placeholder | 🟡 Medium | UX | http://localhost:8501 |
| 7 | OpenAI Config Changes Don't Actually Restart LLM Provider | 🟡 Medium | Functional | http://localhost:8501 |
| 8 | No Loading Indicator During Analysis | 🟡 Medium | UX | http://localhost:8501 |
| 9 | Workspace Files List Renders `__init__.py` as Bold "init" | 🔵 Low | Visual | http://localhost:8501 |
| 10 | Help Buttons Have Generic Accessibility Labels | 🔵 Low | Accessibility | http://localhost:8501 |
| 11 | No Way to Clear/Reset Workflow State Without Page Reload | 🔵 Low | UX | http://localhost:8501 |

## Testing Coverage

### Pages Tested
- Main dashboard (http://localhost:8501) — full page

### Features Tested
- Project loading (path + name input, Mount Project button)
- Empty input validation (Mount Project without values)
- Analyze button click and state change
- Generate Fix button enablement check
- Apply Fix button enablement check
- Rollback button enablement check
- Run Project button click
- Restart Runtime button click
- AI Fallback config: API key field, model dropdown, mode radio, base URL
- Test Connection button click (without API key — shows warning)
- Detect Models button click (without API key — shows warning)
- Log viewer content inspection
- Analysis Report panel state
- Workspace Files listing

### Not Tested / Out of Scope
- AI Fallback with valid API key (no key available for testing)
- Actual worker execution and error handling paths
- Fix generation and application pipeline
- Rollback functionality
- Multi-project workflows
- Responsive/mobile layout testing
- Keyboard-only navigation testing

### Blockers
- Cannot test the core self-healing/fallback loop without a valid OpenAI API key
- Cannot test actual worker failures since workers are stubs that always return success

---

## Notes

**Root Cause of Core Workflow Failure:**
The fundamental issue is in `runtime.py:run_analysis_pipeline()` (lines 164-213). The method dispatches workers asynchronously via the MessageBus but then uses `time.sleep(2)` followed by setting `analysis_complete = True` in the UI thread. The worker responses come back on separate threads via `process_response()`, but there's no coordination mechanism (e.g., a counter or event) to know when all workers have finished. The 2-second sleep is arbitrary and doesn't guarantee workers completed. Additionally, `process_response()` only triggers the Thinker when `pipeline_id == "analysis"` AND the pipeline has exhausted its steps — but the analysis pipeline only has `["syntax_worker", "bug_worker"]` as steps, and after both complete, the Thinker synthesis happens inside `process_response()` which may not be reached if responses arrive out of order or if the state tracking is off.

**Recommended Fix:**
Implement a proper coordinator in `Captn` that tracks pending worker responses for each task_id, waits for all expected workers to respond (or times out), then triggers the Thinker and updates UI state atomically. Replace the hardcoded `time.sleep(2)` with event-based completion signaling.
