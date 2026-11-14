// Upcoming and "Did you go?" (Task 14): two admin cards. "Did you go?" (order 10) lists upcoming shows whose date has
// passed, each with Yes (it becomes an attended show) and No (asks "Delete this show? Yes / No" inline, then deletes
// it). "Coming up" (order 15) lists the shows still ahead. A card with nothing to show returns false. No DOM at
// import, so node --test can load the helpers.
import { get, send } from "./api.js";
import { esc, fmtDate } from "./core.js";

export const NOTHING_TO_CHECK = "Nothing to check";

const when = r => [fmtDate(r.date, r.precision), r.venue_label].filter(Boolean).join(" · ");

const renderLabel = r => `<a href="#/show/${esc(r.id)}">${esc(r.billing)}</a>
<span class="muted">${esc(when(r))}</span>`;

const renderDueRow = r => `<li class="dyg-row" data-id="${esc(r.id)}">
<div class="dyg-label">${renderLabel(r)}</div>
<div class="dyg-actions">
<button type="button" class="btn btn-primary" data-act="yes">Yes</button>
<button type="button" class="btn" data-act="no">No</button>
</div>
<div class="dyg-confirm" hidden>
<span>Delete this show?</span>
<button type="button" class="btn" data-act="delete-yes">Yes</button>
<button type="button" class="btn" data-act="delete-no">No</button>
</div>
<p class="muted small dyg-msg" role="status"></p></li>`;

// The due shows as a list of rows, or "Nothing to check".
const renderDueRows = rows => rows.map(renderDueRow).join("");
export const renderDue = rows => (rows.length ? `<ul class="dyg-list">${renderDueRows(rows)}</ul>` : `<p class="muted">${esc(NOTHING_TO_CHECK)}</p>`);

// The shows still ahead.
const renderUpcomingRow = r => `<li class="dyg-row"><div class="dyg-label">${renderLabel(r)}</div></li>`;
const renderUpcomingRows = rows => rows.map(renderUpcomingRow).join("");
export const renderUpcoming = rows => `<ul class="dyg-list">${renderUpcomingRows(rows)}</ul>`;

export async function renderDidYouGoCard(el) {
  const { shows } = await get("/admin/api/did-you-go");
  if (!shows.length) return false;
  el.innerHTML = `<h2>Did you go?</h2>${renderDue(shows)}`;
  el.addEventListener("click", async ev => {
    const btn = ev.target.closest("button[data-act]");
    const li = btn?.closest(".dyg-row");
    if (!li) return;
    const act = btn.dataset.act;
    const confirm = li.querySelector(".dyg-confirm");
    const actions = li.querySelector(".dyg-actions");
    if (act === "no" || act === "delete-no") {
      confirm.hidden = act === "delete-no";
      actions.hidden = act === "no";
      return;
    }
    const msg = li.querySelector(".dyg-msg");
    btn.disabled = true;
    try {
      await send("POST", `/admin/api/did-you-go/${li.dataset.id}`, { went: act === "yes" });
      li.remove();
      if (!el.querySelector(".dyg-row")) el.innerHTML = `<h2>Did you go?</h2>${renderDue([])}`;
    } catch (e) {
      msg.textContent = e?.error ?? "Something went wrong. Try again.";
      btn.disabled = false;
    }
  });
  return true;
}

export async function renderComingUpCard(el) {
  const { shows } = await get("/admin/api/upcoming");
  if (!shows.length) return false;
  el.innerHTML = `<h2>Coming up (${esc(shows.length)})</h2>${renderUpcoming(shows)}`;
  return true;
}
