// Import screens (Task 5): #/admin/import (choose a CSV, upload), #/admin/import/<id> (review a pending batch, tick
// or untick rows, edit acts, apply or discard) and the admin card "Imports waiting". The server does the matching
// and the applying (imports.py); this only shows the batch and sends the editor's choices. No DOM at import.
import { ApiError, get, send } from "./api.js";
import { esc, fmtDate } from "./core.js";
import { registerAdminCard } from "./registry.js";

const ROLES = [["headliner", "Headliner"], ["coheadliner", "Co-headliner"], ["opener", "Opener"], ["performer", "Performer"]];
const GROUPS = [
  ["new", "New"],
  ["maybe", "Check these"],
  ["have", "Already have"],
  ["skip", "Skipped"],
];

const message = e => (e instanceof ApiError ? e.error : "Something went wrong. Try again.");
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const renderFlag = f => `<span class="tag import-flag">${esc(f)}</span>`;
const renderFlags = flags => (flags?.length ? flags.map(renderFlag).join(" ") : "");
const renderSelected = on => (on ? " selected" : "");
const renderChecked = on => (on ? " checked" : "");
const renderOption = (value, label, chosen) => `<option value="${esc(value)}"${renderSelected(chosen === value)}>${esc(label)}</option>`;

const renderRoleOptions = chosen => ROLES.map(([v, l]) => renderOption(v, l, chosen)).join("");

function renderAct(a, i, editable) {
  if (!editable) return `<li>${esc(a.name)} <span class="muted">${esc(a.role)}${esc(a.confidence === "likely" ? ", likely" : "")}</span></li>`;
  return `<li class="import-act" data-i="${esc(i)}"><span class="import-act-name">${esc(a.name)}</span>
<select class="import-role" aria-label="Role of ${esc(a.name)}">${renderRoleOptions(a.role)}</select>
<label class="import-likely"><input type="checkbox" class="import-likely-box"${renderChecked(a.confidence === "likely")}> likely</label></li>`;
}

const renderActs = (acts, editable) => acts.map((a, i) => renderAct(a, i, editable)).join("");

const renderMatch = m => (m ? `<p class="import-match">You have: <a href="#/show/${esc(m.id)}">${esc(m.billing)}</a>
<span class="muted">${fmtDate(m.date)} · ${esc(m.venue_label)}</span></p>` : "");
const renderDiffers = d => (d?.length ? `<p class="import-differs">Differs: ${esc(d.join(", "))}</p>` : "");

const renderTick = row => (row.verdict === "new" || row.verdict === "maybe"
  ? `<label class="import-tick"><input type="checkbox" class="import-include"${renderChecked(row.include)} aria-label="Include row ${esc(row.row_no)}"> Include</label>`
  : "");
const renderPlace = p => esc([p.venue, p.city, p.region || p.country].filter(Boolean).join(", "));
const renderWhen = p => `${fmtDate(p.date)}${p.date && (p.venue || p.city) ? " · " : ""}${renderPlace(p)}`;
const renderActList = (p, editable) => ((p.acts ?? []).length ? `<ul class="import-acts">${renderActs(p.acts, editable)}</ul>` : "");
const renderReason = r => (r ? `<p class="muted import-reason">${esc(r)}</p>` : "");

// One row of the review. New and maybe rows have the include tick and editable acts; have and skip rows are read-only.
export function renderImportRow(row) {
  const p = row.parsed ?? {};
  const editable = row.verdict === "new" || row.verdict === "maybe";
  return `<li class="import-row verdict-${esc(row.verdict)}" data-row-id="${esc(row.id)}" data-verdict="${esc(row.verdict)}">
<div class="import-head"><strong class="import-billing">${esc(p.billing || "(no billing)")}</strong> ${renderFlags(p.flags)}${renderTick(row)}</div>
<p class="muted import-when">${renderWhen(p)}</p>
${renderActList(p, editable)}${renderReason(row.reason)}${renderMatch(row.match)}${renderDiffers(row.differs)}
<p class="error import-row-msg" role="status"></p></li>`;
}

const renderRows = rows => rows.map(renderImportRow).join("");

const renderGroup = ([verdict, title], rows) => (rows.length
  ? `<section class="import-group" data-verdict="${esc(verdict)}"><h2>${esc(title)} (${esc(rows.length)})</h2>
<ul class="import-list">${renderRows(rows)}</ul></section>` : "");

export const renderGroups = rows => GROUPS.map(g => renderGroup(g, rows.filter(r => r.verdict === g[0]))).join("");

// ---------------------------------------------------------------- #/admin/import
export async function renderImportPage(ctx) {
  const { view, session } = ctx;
  if (!session.editor) {
    view.innerHTML = `<h1>Import</h1><div class="notice"><p>Editors only.</p></div>`;
    return;
  }
  view.innerHTML = `<h1>Import</h1>
<p class="muted">Choose a CSV of shows. Nothing is added until you review the rows and apply them.</p>
<form class="import-form"><input type="file" name="file" accept=".csv,text/csv" aria-label="CSV file" required>
<button class="btn btn-primary" type="submit">Upload</button></form>
<p class="error import-msg" role="status"></p>`;
  const form = view.querySelector(".import-form");
  const msg = view.querySelector(".import-msg");
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const file = form.elements.file.files[0];
    if (!file) {
      msg.textContent = "Choose a file first.";
      return;
    }
    const btn = form.querySelector("button");
    btn.disabled = true;
    msg.textContent = "";
    try {
      const data = new FormData();
      data.append("file", file);
      const { id } = await send("POST", "/admin/api/imports/csv", data);
      location.hash = `#/admin/import/${id}`;
    } catch (err) {
      if (!ctx.alive()) return;
      btn.disabled = false;
      msg.textContent = message(err);
    }
  });
}

// ---------------------------------------------------------------- #/admin/import/<id>
const renderSummary = b => `<h1>${esc(b.label)}</h1><p class="muted">${esc(plural(b.counts.new, "new show", "new shows"))},
${esc(b.counts.maybe)} to check, ${esc(b.counts.have)} already have, ${esc(b.counts.skip)} skipped.</p>`;

const renderActions = pending => (!pending ? "" : `<div class="import-actions">
<button class="btn btn-primary" type="button" data-act="apply">Apply</button>
<button class="btn" type="button" data-act="discard">Discard</button></div>
<div class="import-confirm" hidden><span>Discard this import?</span>
<button class="btn" type="button" data-act="discard-yes">Yes</button>
<button class="btn" type="button" data-act="discard-no">No</button></div>`);

export async function renderImportReview(ctx) {
  const { view, session, params } = ctx;
  if (!session.editor) {
    view.innerHTML = `<h1>Import</h1><div class="notice"><p>Editors only.</p></div>`;
    return;
  }
  const base = `/admin/api/imports/${encodeURIComponent(params.id)}`;
  let batch = await get(base);
  if (!ctx.alive()) return;
  const pending = batch.status === "pending";
  view.innerHTML = `<div class="import-summary"></div><div class="import-status" role="status"></div>
${renderActions(pending)}<div class="import-groups"></div>`;
  const summary = view.querySelector(".import-summary");
  const groups = view.querySelector(".import-groups");
  const status = view.querySelector(".import-status");
  const draw = () => {
    summary.innerHTML = renderSummary(batch);
    groups.innerHTML = renderGroups(batch.rows);
  };
  draw();
  if (!pending) status.textContent = `This import was ${batch.status}.`;
  const apply = view.querySelector("[data-act=apply]");
  const discard = view.querySelector("[data-act=discard]");
  const confirmBox = view.querySelector(".import-confirm");
  const rowOf = el => {
    const li = el.closest(".import-row");
    return li ? batch.rows.find(r => r.id === Number(li.dataset.rowId)) : null;
  };
  const patch = async (li, row, body) => {
    const note = li.querySelector(".import-row-msg");
    note.textContent = "";
    try {
      const fresh = await send("PATCH", `/admin/api/import-rows/${row.id}`, body);
      if (!ctx.alive()) return;
      const at = batch.rows.findIndex(r => r.id === row.id);
      batch.rows[at] = fresh;
      batch = { ...batch, counts: countOf(batch.rows) };
      draw();
    } catch (err) {
      if (!ctx.alive()) return;
      draw();
      const again = groups.querySelector(`[data-row-id="${row.id}"] .import-row-msg`);
      if (again) again.textContent = message(err);
    }
  };
  groups.addEventListener("change", e => {
    const li = e.target.closest(".import-row");
    const row = rowOf(e.target);
    if (!row) return;
    if (e.target.classList.contains("import-include")) {
      patch(li, row, { include: e.target.checked });
      return;
    }
    const actEl = e.target.closest(".import-act");
    if (!actEl) return;
    const acts = row.parsed.acts.map(a => ({ ...a }));
    const act = acts[Number(actEl.dataset.i)];
    act.role = actEl.querySelector(".import-role").value;
    act.confidence = actEl.querySelector(".import-likely-box").checked ? "likely" : "confirmed";
    patch(li, row, { parsed: { ...row.parsed, acts } });
  });
  view.addEventListener("click", async e => {
    const act = e.target.closest?.("[data-act]")?.dataset.act;
    if (act === "discard") confirmBox.hidden = false;
    else if (act === "discard-no") confirmBox.hidden = true;
    else if (act === "discard-yes") await finish(`${base}/discard`, () => "Discarded this import.");
    else if (act === "apply") await finish(`${base}/apply`, r => renderAdded(r.created));
  });
  // Apply and Discard share this: the buttons stay disabled while the request is in flight, and after it succeeds or
  // the server says the batch is no longer pending (409). Any other failure re-enables them.
  async function finish(path, renderDone) {
    apply.disabled = true;
    discard.disabled = true;
    confirmBox.hidden = true;
    status.textContent = "";
    try {
      const result = await send("POST", path);
      if (!ctx.alive()) return;
      status.innerHTML = renderDone(result);
      groups.innerHTML = "";
    } catch (err) {
      if (!ctx.alive()) return;
      status.textContent = message(err);
      if (!(err instanceof ApiError && err.status === 409)) {
        apply.disabled = false;
        discard.disabled = false;
      }
    }
  }
}

const countOf = rows => {
  const c = { new: 0, have: 0, maybe: 0, skip: 0 };
  for (const r of rows) c[r.verdict] += 1;
  return c;
};

const renderAdded = n => `<p class="import-added">Added ${esc(plural(n, "show", "shows"))}.
<a href="#/">See them</a></p>`;

// ---------------------------------------------------------------- the admin card
const renderPending = b => `<li><a href="#/admin/import/${esc(b.id)}">${esc(b.label)}</a>
<span class="muted">${esc(b.counts.new)} new, ${esc(b.counts.maybe)} to check</span></li>`;

const renderPendingList = batches => batches.map(renderPending).join("");

export async function renderImportsCard(el) {
  const { batches } = await get("/admin/api/imports");
  if (!batches.length) return false;
  el.innerHTML = `<h2>Imports waiting (${esc(batches.length)})</h2><ul class="import-pending">${renderPendingList(batches)}</ul>`;
  return true;
}

registerAdminCard("Imports waiting", { order: 30, render: renderImportsCard });
