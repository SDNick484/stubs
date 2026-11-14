// #/admin: the editor's dashboard, made of the admin cards feature tasks register (registry.js), in their order.
// Cards are laid out first and then drawn together, so a slow one doesn't hold the rest back. A card whose render
// returns false has nothing to show and is removed; with no card left the page says NOTHING.
import { adminCards } from "./registry.js";
import { esc } from "./core.js";

export const NOTHING = "Nothing needs you.";

const renderEditorsOnly = () => `<h1>Admin</h1>
<div class="notice"><p>Editors only.</p><p><a class="btn" href="/admin#/admin">Sign in</a></p></div>`;
const renderCardFailed = message => `<p class="error">${esc(message)}</p>`;

export async function renderAdmin(ctx) {
  const { view, session } = ctx;
  if (!session.editor) {
    view.innerHTML = renderEditorsOnly();
    return;
  }
  view.innerHTML = `<h1>Admin</h1><div class="cards"></div><p class="muted empty" hidden>${esc(NOTHING)}</p>`;
  const list = view.querySelector(".cards");
  const empty = view.querySelector(".empty");
  const drawn = adminCards().map(card => {
    const el = document.createElement("section");
    el.className = "card";
    el.dataset.card = card.name;
    list.append(el);
    return Promise.resolve()
      .then(() => card.render(el))
      .then(result => {
        if (result === false) el.remove();
      })
      .catch(e => {
        el.innerHTML = renderCardFailed(e?.error ?? "This card couldn't load. Reload the page.");
      });
  });
  await Promise.all(drawn);
  if (ctx.alive()) empty.hidden = list.children.length > 0;
}
