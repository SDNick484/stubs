// Song counts (post-v1 #44): #/song/<id> ("Heard 4 times · first … · last …", the shows with their set and notes),
// and on the artist page a "Songs heard" section (top 10, "All songs" expands) and "Never heard" (the catalog's songs
// with no play). Editors paste the catalog on the artist page, remove a never-heard song from it, and merge a song
// into another of the same act ("Merge 'X' into 'Y'? 3 plays move." with Yes / No). Pages under /admin read
// /admin/api/, the rest /api/. No DOM at import, so node --test can load the renderers.
import { get, send } from "./api.js";
import { esc, fmtDate } from "./core.js";

const apiBase = () => (location.pathname.startsWith("/admin") ? "/admin/api" : "/api");
const TOP = 10;

// ---------------------------------------------------------------- wording
const HEARD = { 0: "Not heard yet", 1: "Heard once", 2: "Heard twice" };
export const heardLabel = n => HEARD[n] ?? `Heard ${n} times`;
export const playsMove = n => (n === 1 ? "1 play moves." : `${n} plays move.`);

const renderPart = html => "<span>" + html + "</span>";
const renderDated = (word, date) => (date ? renderPart(`${esc(word)} ${fmtDate(date)}`) : "");

// "Merge 'Lantern Lite' into 'Lantern Light'? 3 plays move." (escaped: it goes into markup).
export function renderMergeConfirm(drop, keep, plays) {
  return `Merge '${esc(drop)}' into '${esc(keep)}'? ${esc(playsMove(plays))}`;
}

// ---------------------------------------------------------------- the artist page
const renderSongItem = s =>
  `<li><a href="#/song/${esc(s.id)}">${esc(s.title)}</a> <span class="muted">${esc(heardLabel(s.plays))}</span></li>`;

// One song of an artist's list (artist_songs' shape): its title as a link to its page, and how often it was heard.
export function renderSongSummary(s) {
  return renderSongItem(s);
}

const renderSongItems = rows => rows.map(renderSongItem).join("");

const renderMore = (total, all) =>
  total > TOP
    ? `<p><button type="button" class="btn" data-act="${esc(all ? "top" : "all")}">${esc(all ? "Top " + TOP : "All songs (" + total + ")")}</button></p>`
    : "";

// "Songs heard": the songs with plays (the list is most played first); the top 10 unless `all`.
export function renderHeard(rows, all = false) {
  const heard = rows.filter(r => r.plays > 0);
  if (!heard.length) return `<h2>Songs heard</h2><p class="muted">No setlists stored for this act yet.</p>`;
  const shown = all ? heard : heard.slice(0, TOP);
  return `<h2>Songs heard</h2><ol class="song-list">${renderSongItems(shown)}</ol>${renderMore(heard.length, all)}`;
}

const renderNeverItem = (s, editor) =>
  `<li><a href="#/song/${esc(s.id)}">${esc(s.title)}</a>${renderRemove(s, editor)}</li>`;
const renderNeverItems = (rows, editor) => rows.map(s => renderNeverItem(s, editor)).join("");
const renderNeverList = (rows, editor) => `<ul class="song-list">${renderNeverItems(rows, editor)}</ul>`;
const renderRemove = (s, editor) =>
  editor ? ` <button type="button" class="btn song-remove" data-act="remove" data-id="${esc(s.id)}">Remove</button>` : "";

const renderCatalogForm = () => `<form class="song-catalog-form" data-slot="catalog">
<label>Songs this act has, one per line
<textarea name="text" rows="6" spellcheck="false"></textarea></label>
<p class="small muted">Paste an album or a song list. Numbers go; "Album: …" and heading lines are skipped. Pasting again adds only what is new.</p>
<p><button type="submit" class="btn btn-primary">Add to catalog</button></p>
<p class="error" role="alert" data-slot="error" hidden></p>
</form>`;

const renderNeverBody = (never, editor) =>
  never.length ? renderNeverList(never, editor) : `<p class="muted">You have heard every song in the catalog.</p>`;

const renderPaste = editor => (editor ? renderCatalogForm() : "");

// "Never heard": the catalog's songs with no play. Without a catalog it says so (and editors get the paste).
export function renderNeverHeard(rows, editor = false) {
  const catalog = rows.filter(r => r.in_catalog);
  if (!catalog.length) return `<h2>Never heard</h2><p class="muted">No song catalog for this act yet.</p>${renderPaste(editor)}`;
  const never = catalog.filter(r => r.plays === 0);
  return `<h2>Never heard</h2>${renderNeverBody(never, editor)}${renderPaste(editor)}`;
}

export const renderArtistSongs = (rows, { editor = false, all = false } = {}) =>
  `${renderHeard(rows, all)}${renderNeverHeard(rows, editor)}<p class="song-msg small muted" data-slot="msg" hidden></p>`;

// The artist page (Task 8's showArtist draws it) plus the songs sections, placed under its summary.
export async function showArtistWithSongs(showArtist, ctx) {
  await showArtist(ctx);
  if (!ctx.alive()) return;
  const id = encodeURIComponent(ctx.params.id);
  const editor = Boolean(ctx.session?.editor) && location.pathname.startsWith("/admin");
  let rows;
  try {
    rows = await get(`${apiBase()}/artists/${id}/songs`);
  } catch {
    return;
  }
  if (!ctx.alive()) return;
  const section = document.createElement("section");
  section.className = "artist-songs";
  const anchor = ctx.view.querySelector(".role-counts") ?? ctx.view.querySelector(".summary");
  if (anchor) anchor.after(section);
  else ctx.view.append(section);
  const state = { all: false };
  const paint = () => {
    section.innerHTML = renderArtistSongs(rows, { editor, all: state.all });
  };
  paint();
  const fail = (form, err) => {
    const p = form?.querySelector('[data-slot="error"]');
    if (!p) return;
    p.textContent = err?.error ?? "Something went wrong. Try again.";
    p.hidden = false;
  };
  const reload = async () => {
    rows = await get(`${apiBase()}/artists/${id}/songs`);
    paint();
  };
  section.addEventListener("click", async e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    if (btn.dataset.act === "all" || btn.dataset.act === "top") {
      state.all = btn.dataset.act === "all";
      paint();
    } else if (btn.dataset.act === "remove") {
      try {
        await send("DELETE", `/admin/api/songs/${encodeURIComponent(btn.dataset.id)}/catalog`);
        await reload();
      } catch (err) {
        const p = section.querySelector('[data-slot="msg"]');
        p.textContent = err?.error ?? "Something went wrong. Try again.";
        p.hidden = false;
      }
    }
  });
  section.addEventListener("submit", async e => {
    const form = e.target.closest("form[data-slot=catalog]");
    if (!form) return;
    e.preventDefault();
    form.querySelector('[data-slot="error"]').hidden = true;
    try {
      await send("POST", `/admin/api/artists/${id}/catalog`, { text: form.elements.text.value });
      await reload();
    } catch (err) {
      fail(form, err);
    }
  });
}

// ---------------------------------------------------------------- the song page
const renderArtistLink = a => `<p class="aka"><a href="#/artist/${esc(a.id)}">${esc(a.name)}</a></p>`;
const renderAliases = aliases => (aliases.length ? `<p class="aka muted">Also known as ${esc(aliases.join(", "))}</p>` : "");

export function renderSongSummaryLine(song) {
  return `<p class="summary">${renderPart(esc(heardLabel(song.plays)))}${renderDated("first", song.first)}${renderDated("last", song.last)}</p>`;
}

const renderPlayMeta = p => {
  const bits = [p.set_name || (p.encore ? "Encore" : ""), p.note].filter(Boolean);
  return bits.length ? `<small class="song-play-meta muted">${esc(bits.join(" · "))}</small>` : "";
};

// One play: the night (date, billing, venue) as a link to the show, then the set it was in and the entry's note.
export function renderPlay(p) {
  const v = p.venue ?? {};
  return `<li class="show-row song-play"><a class="show-link" href="#/show/${esc(p.id)}">
    <span class="show-date">${fmtDate(p.date, p.precision)}</span>
    <span class="show-billing">${esc(p.billing)}</span>
    <span class="show-place">${esc(v.label)}</span>
  </a>${renderPlayMeta(p)}</li>`;
}

const renderPlayItems = shows => shows.map(renderPlay).join("");
const renderPlays = shows =>
  shows.length ? `<h2>Shows</h2><ul class="show-list">${renderPlayItems(shows)}</ul>` : `<p class="muted">Not heard yet.</p>`;

const renderTools = song =>
  `<p class="song-tools"><button type="button" class="btn" data-act="merge">Merge into…</button>${renderUncatalog(song)}</p>
<div class="song-merge" data-slot="merge" hidden>
  <label class="merge-label">Merge into
    <input type="search" class="merge-search" autocomplete="off" placeholder="Search this act's songs"></label>
  <ul class="merge-picks"></ul>
  <p class="song-confirm small" data-slot="confirm" hidden></p>
</div>
<p class="error" role="alert" data-slot="error" hidden></p>`;
const renderUncatalog = song =>
  song.in_catalog ? ` <button type="button" class="btn" data-act="uncatalog">Remove from catalog</button>` : "";

const renderEditorTools = (song, editor) => (editor ? renderTools(song) : "");

export function renderSongPick(item) {
  return `<li><button type="button" class="merge-pick" data-id="${esc(item.id)}" data-title="${esc(item.title)}">
  <span class="merge-pick-name">${esc(item.title)}</span><span class="merge-pick-meta">${esc(heardLabel(item.plays))}</span>
</button></li>`;
}

const renderPicks = items => (items.length ? items.map(renderSongPick).join("") : `<li class="muted small">No match.</li>`);

const renderConfirm = (song, pick) =>
  `${renderMergeConfirm(song.title, pick.title, song.plays)} <button type="button" class="btn btn-primary" data-act="merge-yes">Yes</button> <button type="button" class="btn" data-act="merge-no">No</button>`;

export function renderSong(song, editor) {
  return `<h1>${esc(song.title)}</h1>
${renderArtistLink(song.artist)}
${renderSongSummaryLine(song)}
${renderAliases(song.aliases ?? [])}
${renderPlays(song.shows)}
${renderEditorTools(song, editor)}`;
}

export async function showSong(ctx) {
  const id = encodeURIComponent(ctx.params.id);
  const song = await get(`${apiBase()}/songs/${id}`);
  if (!ctx.alive()) return;
  const editor = Boolean(ctx.session?.editor) && location.pathname.startsWith("/admin");
  ctx.view.innerHTML = renderSong(song, editor);
  if (!editor) return;
  const view = ctx.view;
  const slot = name => view.querySelector(`[data-slot="${name}"]`);
  const say = msg => {
    const p = slot("error");
    p.textContent = msg ?? "";
    p.hidden = !msg;
  };
  let pick = null;
  const search = async q => {
    const rows = await get(`/admin/api/artists/${encodeURIComponent(song.artist.id)}/songs?q=${encodeURIComponent(q)}`);
    if (!ctx.alive()) return;
    view.querySelector(".merge-picks").innerHTML = renderPicks(rows.filter(r => r.id !== song.id));
  };
  view.addEventListener("input", e => {
    if (!e.target.matches(".merge-search")) return;
    search(e.target.value.trim()).catch(err => say(err?.error));
  });
  view.addEventListener("click", async e => {
    const choice = e.target.closest(".merge-pick");
    if (choice) {
      pick = { id: Number(choice.dataset.id), title: choice.dataset.title };
      const c = slot("confirm");
      c.innerHTML = renderConfirm(song, pick);
      c.hidden = false;
      return;
    }
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    say("");
    try {
      if (btn.dataset.act === "merge") {
        slot("merge").hidden = false;
        view.querySelector(".merge-search").focus();
        await search("");
      } else if (btn.dataset.act === "merge-no") {
        pick = null;
        slot("confirm").hidden = true;
      } else if (btn.dataset.act === "merge-yes" && pick) {
        await send("POST", "/admin/api/songs/merge", { keep: pick.id, drop: song.id });
        location.hash = `#/song/${pick.id}`;
      } else if (btn.dataset.act === "uncatalog") {
        await send("DELETE", `/admin/api/songs/${encodeURIComponent(song.id)}/catalog`);
        if (song.plays > 0) ctx.rerender();
        else location.hash = `#/artist/${song.artist.id}`;
      }
    } catch (err) {
      say(err?.error ?? "Something went wrong. Try again.");
    }
  });
}
