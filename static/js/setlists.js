// The Setlists show panel (post-v1 #43): the owner's own setlists, one per act, as the show detail carries them
// (lineup[i].setlist: {sets: [{name, encore, songs: [{song_id, title, note, segue}]}]}, plus source_label and
// source_url for editors). Sets and encores are grouped, a segue is "›", notes are small. Editors get "Add setlist" /
// "Edit" per act: paste or type, Preview (which songs are new to that act), a source, Save, and an inline
// "Delete? Yes / No". Nothing here talks to setlist.fm: that is the Setlist panel's job (setlist.js). No DOM at import.
import { send } from "./api.js";
import { esc } from "./core.js";

const safeUrl = url => (/^https?:\/\//i.test(String(url ?? "")) ? String(url) : "");

// ---------------------------------------------------------------- the songs
const renderNote = note => (note ? ` <small class="setlists-note">${esc(note)}</small>` : "");
const renderSegue = segue => (segue ? ` <span class="setlists-segue" title="Straight into the next song">›</span>` : "");
const renderNew = song => (song.song_id === null ? ` <span class="tag setlists-new">new</span>` : "");
const renderMarks = (song, marking) => (marking ? renderNew(song) : "");

// A stored song links to its page (#44); one with no song_id yet (the preview's new songs) is just text.
const renderTitle = song =>
  song.song_id === null || song.song_id === undefined
    ? `<span class="setlists-title">${esc(song.title)}</span>`
    : `<a class="setlists-title" href="#/song/${esc(song.song_id)}">${esc(song.title)}</a>`;

function renderSong(song, marking) {
  return `<li>${renderTitle(song)}${renderSegue(song.segue)}${renderNote(song.note)}${renderMarks(song, marking)}</li>`;
}

const renderSongItems = (songs, marking) => songs.map(s => renderSong(s, marking)).join("");

// A set's heading: its name, or "Encore" for an unnamed encore; none for an unnamed main set.
export const setTitle = set => set.name || (set.encore ? "Encore" : "");

const renderSetName = set => (setTitle(set) ? `<h4 class="setlists-set-name">${esc(setTitle(set))}</h4>` : "");

function renderSet(set, marking) {
  return `<div class="setlists-set${esc(set.encore ? " is-encore" : "")}">${renderSetName(set)}<ol class="setlists-songs">${renderSongItems(set.songs, marking)}</ol></div>`;
}

// Sets as the server groups them; `marking` adds a "new" tag to songs with no song_id (the preview).
export function renderSets(sets, marking = false) {
  return (sets ?? []).map(s => renderSet(s, marking)).join("");
}

// Flat entries (the preview's) -> sets: a new set wherever set_name or encore changes, as the server groups them.
export function groupEntries(entries) {
  const sets = [];
  for (const e of entries ?? []) {
    const name = e.set_name ?? null;
    const encore = Boolean(e.encore);
    const last = sets[sets.length - 1];
    if (!last || last.name !== name || last.encore !== encore) sets.push({ name, encore, songs: [] });
    sets[sets.length - 1].songs.push({ song_id: e.song_id ?? null, title: e.title, note: e.note ?? null, segue: Boolean(e.segue) });
  }
  return sets;
}

// A stored setlist back as text the parser reads the same way: a heading line per named set or encore ("Set II:",
// "Encore:", "Encore 2:"), one song per line, " (note)" and " >" for a segue.
export function setlistText(setlist) {
  const lines = [];
  for (const set of setlist?.sets ?? []) {
    const title = setTitle(set);
    if (title) {
      if (lines.length) lines.push("");
      lines.push(`${title}:`);
    }
    for (const s of set.songs) lines.push(`${s.title}${s.note ? ` (${s.note})` : ""}${s.segue ? " >" : ""}`);
  }
  return lines.join("\n");
}

// ---------------------------------------------------------------- the preview
const distinctNew = entries => new Set(entries.filter(e => e.song_id === null).map(e => e.title.toLowerCase())).size;

// "12 songs · 3 new to The Paper Lanterns": new songs counted once each, however often they come up.
export function countText(entries, actName) {
  const n = entries.length;
  const fresh = distinctNew(entries);
  return (n === 1 ? "1 song" : n + " songs") + " · " + (fresh ? fresh + " new" : "none new") + " to " + actName;
}

const renderCount = (entries, actName) => `<p class="small setlists-count">${esc(countText(entries, actName))}</p>`;

// The parsed paste ({entries} from .../setlist/preview), grouped, with the songs new to this act tagged "new".
export function renderPreview(entries, actName) {
  if (!entries || !entries.length) return `<p class="muted">No songs found in that text.</p>`;
  return `${renderCount(entries, actName)}${renderSets(groupEntries(entries), true)}`;
}

// ---------------------------------------------------------------- one act
const renderSourceLink = (label, href) =>
  (href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : esc(label));

function renderSource(setlist) {
  if (!setlist.source_label) return "";
  return `<p class="small muted setlists-source">From ${renderSourceLink(setlist.source_label, safeUrl(setlist.source_url))}</p>`;
}

// An inline Yes / No, never window.confirm.
export function renderDeleteAsk(asking) {
  if (!asking) return `<button type="button" class="btn" data-act="delete">Delete</button>`;
  return `<span class="setlists-confirm">Delete? <button type="button" class="btn" data-act="delete-yes">Yes</button> <button type="button" class="btn" data-act="delete-no">No</button></span>`;
}

function renderTools(entry, asking) {
  if (!entry.setlist) return `<p class="setlists-tools"><button type="button" class="btn" data-act="edit">Add setlist</button></p>`;
  return `<p class="setlists-tools"><button type="button" class="btn" data-act="edit">Edit</button>${renderDeleteAsk(asking)}</p>`;
}

export function renderForm(draft) {
  return `<form class="setlists-form" data-slot="form">
<label>Songs, one per line
<textarea name="text" rows="10" spellcheck="false">${esc(draft.text)}</textarea></label>
<p class="small muted">Paste from a band or fan-club site, or type. "Set 2", "Encore" and lines ending in ":" start a set; "A &gt; B" is a segue; (notes) go in brackets.</p>
<label>Source <input name="source_label" maxlength="200" placeholder="a site's name, typed, my notes" value="${esc(draft.label)}"></label>
<label>Link (optional) <input name="source_url" type="url" maxlength="2000" placeholder="https://" value="${esc(draft.url)}"></label>
<p class="setlists-tools"><button type="button" class="btn" data-act="preview">Preview</button><button type="submit" class="btn btn-primary">Save</button><button type="button" class="btn" data-act="cancel">Cancel</button></p>
<p class="error" role="alert" data-slot="error" hidden></p>
<div class="setlists-preview" data-slot="preview"></div>
</form>`;
}

function renderActBody(entry, editor, state) {
  if (editor && state.editing === entry.id) return renderForm(state.draft);
  if (!editor) return renderSets(entry.setlist?.sets); // the source is the editor's (the public is never sent it)
  const sets = entry.setlist ? `${renderSets(entry.setlist.sets)}${renderSource(entry.setlist)}` : `<p class="muted">No setlist yet.</p>`;
  return `${renderRaw(sets)}${renderTools(entry, state.asking === entry.id)}`;
}

// Identity: markup it is given was escaped where it was built (a marker the escaping scan can see).
const renderRaw = markup => markup;

export function renderAct(entry, editor, state = {}) {
  return `<section class="setlists-act" data-lineup="${esc(entry.id)}"><h3 class="setlists-act-name">${esc(entry.name)}</h3>${renderActBody(entry, editor, state)}</section>`;
}

// The whole panel: the public sees the acts that have a setlist (and nothing, "", when none has); editors see every
// act with its tools, except on an upcoming show.
export function renderPanelBody(show, editor, state = {}) {
  const lineup = show.lineup ?? [];
  if (!editor) return lineup.filter(e => e.setlist).map(e => renderAct(e, false)).join("");
  if (show.status === "upcoming") return `<p class="muted">Add a setlist after the show.</p>`;
  if (!lineup.length) return `<p class="muted">Add an act to the lineup first.</p>`;
  return lineup.map(e => renderAct(e, true, state)).join("");
}

// ---------------------------------------------------------------- the panel
export async function renderSetlistsPanel(el, show) {
  const editor = Boolean(show.editor);
  const state = { editing: null, draft: null, asking: null };
  const section = el.closest ? el.closest(".show-panel") : null;
  const paint = () => {
    const markup = renderPanelBody(show, editor, state);
    el.innerHTML = renderRaw(markup);
    if (section) section.hidden = markup === "";
  };
  paint();
  if (!editor) return;

  const entryOf = id => (show.lineup ?? []).find(e => String(e.id) === String(id));
  const path = id => `/admin/api/lineup/${encodeURIComponent(id)}/setlist`;
  const fail = (form, err) => {
    const p = form?.querySelector('[data-slot="error"]');
    if (!p) return;
    p.textContent = err?.error ?? "Something went wrong. Try again.";
    p.hidden = false;
  };
  const readForm = form => ({
    text: form.elements.text.value,
    label: form.elements.source_label.value.trim(),
    url: form.elements.source_url.value.trim(),
  });
  const parse = async (form, entry) => {
    const { entries } = await send("POST", `${path(entry.id)}/preview`, { text: readForm(form).text });
    form.querySelector('[data-slot="preview"]').innerHTML = renderPreview(entries, entry.name);
    return entries;
  };

  el.addEventListener("click", async e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const holder = btn.closest("[data-lineup]");
    const entry = holder && entryOf(holder.dataset.lineup);
    if (!entry) return;
    const form = holder.querySelector("form");
    const act = btn.dataset.act;
    try {
      if (act === "edit") {
        const sl = entry.setlist;
        state.editing = entry.id;
        state.asking = null;
        state.draft = { text: setlistText(sl), label: sl?.source_label ?? "", url: sl?.source_url ?? "" };
        paint();
        el.querySelector(`[data-lineup="${CSS.escape(String(entry.id))}"] textarea`)?.focus();
      } else if (act === "cancel") {
        state.editing = null;
        paint();
      } else if (act === "preview") {
        form.querySelector('[data-slot="error"]').hidden = true;
        await parse(form, entry);
      } else if (act === "delete" || act === "delete-no") {
        state.asking = act === "delete" ? entry.id : null;
        paint();
      } else if (act === "delete-yes") {
        const res = await send("DELETE", path(entry.id), { version: show.version });
        show.version = res.version;
        entry.setlist = null;
        state.asking = null;
        paint();
      }
    } catch (err) {
      if (form) fail(form, err);
      else holder.querySelector(".setlists-tools")?.replaceChildren(document.createTextNode(err?.error ?? "Something went wrong."));
    }
  });

  el.addEventListener("submit", async e => {
    const form = e.target.closest("form[data-slot=form]");
    if (!form) return;
    e.preventDefault();
    const entry = entryOf(form.closest("[data-lineup]").dataset.lineup);
    const { label, url } = readForm(form);
    const submit = form.querySelector('button[type="submit"]');
    form.querySelector('[data-slot="error"]').hidden = true;
    if (!label) {
      fail(form, { error: "Add a source: a site's name, “typed” or “my notes”." });
      return;
    }
    submit.disabled = true;
    try {
      const entries = await parse(form, entry);
      if (!entries.length) throw { error: "No songs found in that text." };
      const res = await send("PUT", path(entry.id), { entries, source_label: label, source_url: url || null, version: show.version });
      show.version = res.version;
      entry.setlist = res.setlist;
      state.editing = null;
      paint();
    } catch (err) {
      submit.disabled = false;
      fail(form, err);
    }
  });
}
