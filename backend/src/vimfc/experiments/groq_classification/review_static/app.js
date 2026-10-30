"use strict";
const $ = id => document.getElementById(id);
let session, index = 0, article, dirty = false, auditDirty = false, loading = false, saving = false, navigationSequence = 0;
const descriptions = {
  IN_TAXONOMY: ["IN · Thuộc taxonomy", "Chọn primary group có căn cứ"],
  OUT_OF_TAXONOMY: ["OUT · Ngoài taxonomy", "Đủ thông tin, ngoài cả tám nhóm"],
  INSUFFICIENT_EVIDENCE: ["INSUFFICIENT · Chưa đủ evidence", "Thiếu thông tin để quyết định"],
  AMBIGUOUS: ["AMBIGUOUS · Chưa phân giải", "Còn nhiều cách hiểu có thể phù hợp"]
};
function message(text, error = false) { $("message").textContent = text; $("message").className = error ? "error" : ""; $("message").hidden = false; }
async function api(path, options = {}) {
  const response = await fetch(path, {cache: "no-store", ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function post(path, data) { return api(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)}); }
function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function options(select, values) { for (const value of values) { const opt = element("option", value); opt.value = value; select.append(opt); } }
function checked(name) { return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map(n => n.value); }
function draftKey() { return `vimfc-review-draft:${session.manifest_id}:${article.reference.review_id}`; }
function auditDraftKey() { return `${draftKey()}:audit:${article.current?.revision || 0}`; }
function lockUI(value) {
  saving = value;
  for (const control of document.querySelectorAll('button,input,textarea,select')) control.disabled = value;
  if (!value) { $("previous").disabled = index === 0; $("next").disabled = index === session.order.length - 1; }
}
function readForm() {
  return {membership: checked("membership")[0] || null, primary_group: $("primary").value || null,
    evidence_sentence_ids: checked("sentence").map(Number), confidence: checked("confidence")[0] || null,
    notes: $("notes").value, reviewer: $("reviewer").value.trim(), review_timestamp: new Date().toISOString()};
}
function showMembership() {
  const inside = checked("membership")[0] === "IN_TAXONOMY";
  $("primary-field").hidden = !inside;
  if (!inside) $("primary").value = "";
}
function fillForm(value) {
  for (const field of ["membership", "confidence"]) for (const input of document.querySelectorAll(`input[name="${field}"]`)) input.checked = input.value === value?.[field];
  $("primary").value = value?.primary_group || "";
  for (const input of document.querySelectorAll('input[name="sentence"]')) input.checked = value?.evidence_sentence_ids?.includes(Number(input.value)) || false;
  $("notes").value = value?.notes || "";
  $("reviewer").value = value?.reviewer || localStorage.getItem("vimfc-reviewer") || "";
  $("timestamp").value = value?.review_timestamp || "Ghi khi Save";
  showMembership();
}
function keepDraft() {
  if (loading || saving || !article) return;
  showMembership(); dirty = true;
  localStorage.setItem(draftKey(), JSON.stringify({base_revision: article.current?.revision || 0, payload: readForm()}));
  $("draft-status").textContent = "Bản nháp được giữ trong trình duyệt · chưa Submit";
}
async function refreshProgress() {
  session = await api("/api/session");
  const p = session.progress;
  $("progress-text").textContent = `${p.reviewed} / ${p.total} đã review`;
  $("progress-percent").textContent = `${Math.round(100 * p.reviewed / p.total)}%`;
  $("progress").value = p.reviewed; $("progress").max = p.total;
  for (const option of $("article-nav").options) option.textContent = `${p.reviewed_ids.includes(option.value) ? "✓ " : ""}${option.value}`;
}
async function go(nextIndex, force = false) {
  if (nextIndex < 0 || nextIndex >= session.order.length) return;
  if (saving && !force) return;
  if (!force && (dirty || auditDirty) && !window.confirm("Có thay đổi chưa Submit. Bản nháp được giữ trong trình duyệt. Chuyển bài?")) { $("article-nav").value = session.order[index]; return; }
  const sequence = ++navigationSequence;
  loading = true;
  try {
    const loaded = await api(`/api/articles/${session.order[nextIndex]}`);
    if (sequence !== navigationSequence) return;
    article = loaded; index = nextIndex; dirty = false; auditDirty = false;
    const ref = article.reference;
    localStorage.setItem(`vimfc-review-position:${session.manifest_id}`, ref.review_id);
    $("article-nav").value = ref.review_id;
    $("article-index").textContent = `ARTICLE ${String(index + 1).padStart(2, "0")} / ${session.order.length} · ${ref.review_id}`;
    $("title").textContent = ref.title;
    $("lead").textContent = ref.lead_text;
    $("provenance").textContent = `Page ${ref.page_id} · Revision ${ref.revision_id} · ${ref.revision_timestamp || "timestamp unavailable"} · Snapshot ${ref.snapshot}`;
    $("provenance").title = `Input fingerprint: ${ref.input_fingerprint}`;
    $("exposure").hidden = !article.comparison_exposed;
    $("blind-status").textContent = article.comparison_exposed ? "COMPARISON ĐÃ MỞ" : "BLIND REVIEW";
    $("sentences").replaceChildren();
    for (const sentence of ref.sentences) {
      const label = element("label", undefined, "sentence"), input = document.createElement("input");
      input.type = "checkbox"; input.name = "sentence"; input.value = sentence.id;
      const text = element("span"); text.append(element("span", `[${sentence.id}]`, "sent-id"), document.createTextNode(sentence.text));
      label.append(input, text); $("sentences").append(label);
    }
    fillForm(article.current?.payload);
    $("revision").textContent = article.current ? `Đã lưu · Revision ${article.current.revision}` : "Chưa lưu";
    $("draft-status").textContent = article.current ? `Saved · ${article.current.received_at}` : "Chưa có nhãn người";
    const rawDraft = localStorage.getItem(draftKey());
    if (rawDraft) {
      const draft = JSON.parse(rawDraft);
      if (draft.base_revision === (article.current?.revision || 0)) { fillForm(draft.payload); dirty = true; $("draft-status").textContent = "Đã khôi phục bản nháp · chưa Submit"; }
      else message("Có bản nháp từ revision cũ. Đã giữ nhãn mới nhất trên server; lịch sử không bị ghi đè.");
    }
    $("previous").disabled = index === 0; $("next").disabled = index === session.order.length - 1;
    $("after-save").hidden = !article.current;
    $("comparison").hidden = true; $("history-panel").hidden = true; $("metrics-panel").hidden = true;
  } finally { if (sequence === navigationSequence) loading = false; }
}
function showComparison(data) {
  const c = data.comparison, e = data.historical_evidence;
  $("comparison-cards").replaceChildren();
  for (const [title, label, note] of [
    ["HUMAN · SEMANTIC", article.current.payload.primary_group || article.current.payload.membership, `Reviewer: ${article.current.payload.reviewer}`],
    ["GROQ · SUGGESTION", c.model_abstain ? "ABSTAIN" : c.predicted_group, `${c.groq_reason} · Evidence [${c.evidence_sentence_ids.join(", ")}]`],
    ["QID-5 · HISTORICAL", c.baseline_primary || c.baseline_outcome, c.classification_reason || "Original verified primary"]
  ]) { const card = element("div", undefined, "comparison-card"); card.append(element("h4", title), element("strong", label), element("p", note)); $("comparison-cards").append(card); }
  $("availability").textContent = `Historical evidence: ${e.availability}. Đây là trạng thái evidence gốc; không tự xác minh nhãn Groq hoặc nhãn người.`;
  $("evidence-json").textContent = JSON.stringify(e, null, 2);
  $("audit-refs").replaceChildren();
  for (const ref of e.evidence_refs) {
    const label = element("label"), input = document.createElement("input"); input.type = "checkbox"; input.name = "audit-ref"; input.value = ref; label.append(input, document.createTextNode(` ${ref}`)); $("audit-refs").append(label);
  }
  let a = article.audit?.payload;
  const auditDraft = localStorage.getItem(auditDraftKey());
  if (auditDraft) { a = JSON.parse(auditDraft); auditDirty = true; }
  $("verification").value = a?.verification || ""; $("verified-group").value = a?.verified_group || "";
  $("audit-notes").value = a?.notes || ""; $("auditor").value = a?.reviewer || $("reviewer").value;
  for (const input of document.querySelectorAll('input[name="audit-ref"]')) input.checked = a?.evidence_refs.includes(input.value) || false;
  for (const input of document.querySelectorAll('input[name="error-tag"]')) input.checked = a?.error_tags.includes(input.value) || false;
  $("audit-stale").hidden = !article.audit || article.audit.semantic_revision === article.current.revision;
  $("audit-status").textContent = article.audit ? `Audit revision ${article.audit.revision} · semantic revision ${article.audit.semantic_revision}` : "Chưa audit";
  $("comparison").hidden = false;
}
async function start() {
  session = await api("/api/session");
  options($("article-nav"), session.order); options($("primary"), session.taxonomy.classification.precedence);
  options($("verified-group"), session.taxonomy.classification.precedence); options($("verification"), session.verifications);
  $("guide").textContent = session.guideline;
  $("precedence").textContent = session.taxonomy.classification.precedence.join(" > ");
  for (const value of session.memberships) {
    const label = element("label", undefined, "choice"), input = document.createElement("input"); input.type = "radio"; input.name = "membership"; input.value = value;
    const text = element("span"); text.append(element("strong", descriptions[value][0]), element("small", descriptions[value][1])); label.append(input, text); $("membership-options").append(label);
  }
  for (const value of session.error_tags) { const label = element("label"), input = document.createElement("input"); input.type = "checkbox"; input.name = "error-tag"; input.value = value; label.append(input, document.createTextNode(` ${value}`)); $("error-tags").append(label); }
  $("semantic-form").addEventListener("input", keepDraft); $("sentences").addEventListener("change", keepDraft);
  $("semantic-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (saving) return;
    const rid = article.reference.review_id, key = draftKey();
    const payload = readForm(), expectedRevision = article.current?.revision || 0;
    lockUI(true);
    try {
      await post(`/api/articles/${rid}/semantic`, {expected_revision: expectedRevision, payload});
      localStorage.setItem("vimfc-reviewer", payload.reviewer); localStorage.removeItem(key);
      dirty = false; await refreshProgress(); await go(index, true); message("Đã lưu nhãn người và revision history. Bạn có thể mở comparison hoặc chuyển bài.");
    } catch (error) { message(error.message, true); } finally { lockUI(false); }
  });
  function readAudit() {
    return {verification: $("verification").value, verified_group: $("verified-group").value || null,
      evidence_refs: checked("audit-ref"), error_tags: checked("error-tag"), notes: $("audit-notes").value,
      reviewer: $("auditor").value.trim(), review_timestamp: new Date().toISOString()};
  }
  $("audit-form").addEventListener("input", () => {
    if (saving || !article.current) return;
    auditDirty = true; localStorage.setItem(auditDraftKey(), JSON.stringify(readAudit()));
  });
  $("audit-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (saving) return;
    const rid = article.reference.review_id, semanticRevision = article.current.revision, key = auditDraftKey();
    const payload = readAudit(), expectedRevision = article.audit?.revision || 0;
    lockUI(true);
    try {
      article.audit = await post(`/api/articles/${rid}/audit`, {expected_revision: expectedRevision, expected_semantic_revision: semanticRevision, payload});
      auditDirty = false; localStorage.removeItem(key);
      $("audit-status").textContent = `Đã lưu audit revision ${article.audit.revision}`; $("audit-stale").hidden = true;
      await refreshProgress(); message("Đã lưu historical evidence audit riêng; corpus giữ nguyên.");
    } catch (error) { message(error.message, true); } finally { lockUI(false); }
  });
  $("reveal").onclick = async () => {
    const rid = article.reference.review_id, revision = article.current.revision, sequence = navigationSequence;
    try {
      const data = await api(`/api/articles/${rid}/comparison`);
      if (sequence !== navigationSequence || article.reference.review_id !== rid || article.current.revision !== revision) return;
      article.comparison_exposed = true; $("exposure").hidden = false; $("blind-status").textContent = "COMPARISON ĐÃ MỞ"; showComparison(data);
    } catch (error) { if (sequence === navigationSequence) message(error.message, true); }
  };
  $("previous").onclick = () => go(index - 1).catch(error => message(error.message, true));
  $("next").onclick = () => go(index + 1).catch(error => message(error.message, true));
  $("article-nav").onchange = () => go(session.order.indexOf($("article-nav").value)).catch(error => message(error.message, true));
  $("resume").onclick = () => go(session.order.indexOf(session.progress.next_pending || session.order[0])).catch(error => message(error.message, true));
  $("history-button").onclick = async () => {
    const sequence = navigationSequence, rid = article.reference.review_id;
    try { const history = await api(`/api/articles/${rid}/history`); if (sequence !== navigationSequence) return; $("history-json").textContent = JSON.stringify(history, null, 2); $("history-panel").hidden = false; } catch (error) { message(error.message, true); }
  };
  $("import-file").onchange = async event => {
    const file = event.target.files[0]; if (!file) return;
    lockUI(true);
    try { const result = await post("/api/import", {format: file.name.toLowerCase().endsWith(".csv") ? "csv" : "jsonl", content: await file.text()}); dirty = false; await refreshProgress(); await go(index, true); message(`Đã import ${result.imported} nhãn người. Mỗi thay đổi có revision riêng.`); }
    catch (error) { message(error.message, true); } finally { event.target.value = ""; lockUI(false); }
  };
  $("evaluate").onclick = async () => {
    try {
      const r = await api("/api/evaluation"); $("metrics-json").textContent = JSON.stringify(r, null, 2); $("metric-summary").replaceChildren(element("p", `${r.status} · ${r.reviewed_count}/${r.selected_count} reviewed`));
      for (const [name, p] of Object.entries(r.partitions)) {
        $("metric-summary").append(element("h4", `${name} · ${p.reviewed_count}/${p.selected_count} reviewed`));
        if (p.rq2) {
          const table = element("table", undefined, "metric-table"); const header = element("tr"); for (const title of ["Approach", "N (human IN)", "Accuracy", "Macro-F1 (8)", "Coverage"]) header.append(element("th", title)); table.append(header);
          for (const [view, m] of Object.entries(p.rq2)) { const row = element("tr"); for (const value of [view, m.denominator, m.accuracy.toFixed(3), m.macro_f1.toFixed(3), m.coverage.toFixed(3)]) row.append(element("td", value)); table.append(row); } $("metric-summary").append(table);
        }
        if (p.rq3) $("metric-summary").append(element("p", `Correct additional suggestions: ${p.rq3.semantic_correct}; historically verified: ${p.rq3.historically_verified_suggestions}.`, "small"));
      }
      $("metrics-panel").hidden = false; $("metrics-panel").scrollIntoView({behavior: "smooth"});
    } catch (error) { message(error.message, true); }
  };
  window.addEventListener("beforeunload", event => { if (dirty || auditDirty || saving) { event.preventDefault(); event.returnValue = ""; } });
  await refreshProgress();
  const last = localStorage.getItem(`vimfc-review-position:${session.manifest_id}`);
  await go(Math.max(0, session.order.indexOf(last || session.progress.next_pending)), true);
}
start().catch(error => message(error.message, true));
