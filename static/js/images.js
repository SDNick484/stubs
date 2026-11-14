// The "Pictures" panel of the show page (registered in main.js): the poster as a hero, then photos; editors also see
// stubs and can upload, caption, reorder and delete. Visitors get photos (and the poster when the server shows it);
// the server decides what each person may see, this file only draws what it is given. renderPictures and groupImages
// touch no DOM, so Node can test them (tests/images.test.mjs).
import { esc } from "./core.js";
import { get, send } from "./api.js";

// [{id, kind, url, caption}] -> { poster, photos, stubs }, each in the order given.
export function groupImages(list) {
  const out = { poster: null, photos: [], stubs: [] };
  for (const image of list) {
    if (image.kind === "poster") out.poster = image;
    else if (image.kind === "photo") out.photos.push(image);
    else if (image.kind === "stub") out.stubs.push(image);
  }
  return out;
}

const KIND = { poster: "poster", photo: "photo", stub: "ticket stub" };
const altText = image => (image.caption ? image.caption : `Show ${KIND[image.kind] ?? "picture"}`);

const renderImage = image => `<img src="${esc(image.url)}" alt="${esc(altText(image))}" loading="lazy">`;
const renderCaption = image => (image.caption ? `<figcaption>${esc(image.caption)}</figcaption>` : "");

const renderConfirm = image => `<span class="pic-confirm">Delete? <button class="btn" type="button" data-act="delete-yes" data-id="${esc(image.id)}">Yes</button> <button class="btn" type="button" data-act="delete-no">No</button></span>`;

const renderMoves = image => `<button class="btn" type="button" data-act="up" data-id="${esc(image.id)}" aria-label="Move earlier">&uarr;</button> <button class="btn" type="button" data-act="down" data-id="${esc(image.id)}" aria-label="Move later">&darr;</button> `;

const renderButtons = (image, movable) => `<div class="pic-actions">${renderMovesIf(image, movable)}<button class="btn" type="button" data-act="caption" data-id="${esc(image.id)}">Caption</button> <button class="btn" type="button" data-act="delete" data-id="${esc(image.id)}">Delete</button></div>`;
const renderMovesIf = (image, movable) => (movable ? renderMoves(image) : "");

const renderControls = (image, editor, state, movable) => {
  if (!editor) return "";
  if (state.confirm === image.id) return `<div class="pic-actions">${renderConfirm(image)}</div>`;
  return renderButtons(image, movable);
};

const renderCaptionForm = image => `<form class="pic-caption-form" data-act="caption-save" data-id="${esc(image.id)}">
  <label>Caption <input name="caption" maxlength="200" value="${esc(image.caption)}"></label>
  <button class="btn btn-primary" type="submit">Save</button> <button class="btn" type="button" data-act="caption-cancel">Cancel</button>
</form>`;

const renderFigure = (image, editor, state, movable) => `<figure class="pic pic-${esc(image.kind)}">
  ${renderImage(image)}
  ${renderCaption(image)}
  ${renderControls(image, editor, state, movable)}
  ${renderCaptionEditor(image, state)}
</figure>`;

const renderCaptionEditor = (image, state) => (state.captioning === image.id ? renderCaptionForm(image) : "");

const renderGroup = (title, items, editor, state, movable) => (items.length
  ? `<h3>${esc(title)}</h3><div class="pic-grid">${renderFigures(items, editor, state, movable)}</div>`
  : "");
const renderFigures = (items, editor, state, movable) => items.map(i => renderFigure(i, editor, state, movable)).join("");

const renderUpload = () => `<form class="pic-upload" data-act="upload">
  <h3>Add a picture</h3>
  <label>Kind <select name="kind"><option value="photo">Photo</option><option value="poster">Poster (replaces the current one)</option><option value="stub">Ticket stub</option></select></label>
  <label>File <input type="file" name="file" accept="image/jpeg,image/png,image/webp" required></label>
  <label>Caption <input name="caption" maxlength="200"></label>
  <p class="error" role="alert" hidden></p>
  <button class="btn btn-primary" type="submit">Upload</button>
</form>`;

// { poster, photos, stubs } -> the panel's markup. Without `editor` there are no stubs, buttons or form, and a show
// with nothing to see gives "". `state` is { confirm, captioning }: the image being deleted or captioned, if any.
const renderHero = (poster, editor, state) => (poster
  ? `<div class="pic-hero">${renderFigure(poster, editor, state, false)}</div>`
  : "");
const renderBody = ({ poster, photos, stubs }, editor, state) => `${renderHero(poster, editor, state)}${renderGroup("Photos", photos, editor, state, true)}${renderStubs(stubs, editor, state)}`;
const renderStubs = (stubs, editor, state) => (editor ? renderGroup("Ticket stubs", stubs, editor, state, true) : "");

// { poster, photos, stubs } -> the panel's markup. Without `editor` there are no stubs, buttons or form, and a show
// with nothing to see gives "". `state` is { confirm, captioning }: the image being deleted or captioned, if any.
export function renderPictures(images, editor, state = {}) {
  const body = renderBody(images, editor, state);
  if (!editor) return body ? `<h2>Pictures</h2>${renderRaw(body)}` : "";
  return `<h2>Pictures</h2>${renderRaw(body)}${renderUpload()}`;
}
// Identity: the markup it is given was escaped where it was built (scanner-visible marker for that).
const renderRaw = markup => markup;

// The panel: registered with registerShowPanel("Pictures", { order: 10, render: renderPicturesPanel }).
export async function renderPicturesPanel(el, show) {
  const base = show.editor ? "/admin/api" : "/api";
  const state = { confirm: null, captioning: null };
  let list = await get(`${base}/shows/${show.id}/images`);

  const paint = () => {
    const markup = renderPictures(groupImages(list), show.editor, state);
    el.innerHTML = renderRaw(markup);
    el.hidden = markup === "";
  };
  const reload = async () => {
    list = await get(`${base}/shows/${show.id}/images`);
    state.confirm = null;
    state.captioning = null;
    paint();
  };
  const fail = e => {
    const p = el.querySelector(".pic-upload .error");
    if (p) {
      p.textContent = e?.error ?? "Something went wrong. Try again.";
      p.hidden = false;
    }
  };
  // Moving swaps two neighbours of one kind, then renumbers that kind 1..n so equal positions can't linger.
  const move = async (id, step) => {
    const image = list.find(i => String(i.id) === id);
    const same = list.filter(i => i.kind === image.kind);
    const at = same.indexOf(image);
    const to = at + step;
    if (to < 0 || to >= same.length) return;
    [same[at], same[to]] = [same[to], same[at]];
    for (const [n, item] of same.entries()) await send("PATCH", `/admin/api/images/${item.id}`, { position: n + 1 });
  };

  el.addEventListener("click", async e => {
    const btn = e.target.closest("[data-act]");
    if (!btn || btn.tagName === "FORM") return;
    const act = btn.dataset.act;
    try {
      if (act === "delete") {
        state.confirm = Number(btn.dataset.id);
        paint();
      } else if (act === "delete-no" || act === "caption-cancel") {
        state.confirm = null;
        state.captioning = null;
        paint();
      } else if (act === "delete-yes") {
        await send("DELETE", `/admin/api/images/${btn.dataset.id}`);
        await reload();
      } else if (act === "caption") {
        state.captioning = Number(btn.dataset.id);
        paint();
      } else if (act === "up" || act === "down") {
        await move(btn.dataset.id, act === "up" ? -1 : 1);
        await reload();
      }
    } catch (err) {
      fail(err);
    }
  });
  el.addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target;
    try {
      if (form.dataset.act === "upload") {
        await send("POST", `/admin/api/shows/${show.id}/images`, new FormData(form));
      } else if (form.dataset.act === "caption-save") {
        await send("PATCH", `/admin/api/images/${form.dataset.id}`, { caption: form.elements.caption.value });
      } else {
        return;
      }
      await reload();
    } catch (err) {
      fail(err);
    }
  });
  paint();
}
