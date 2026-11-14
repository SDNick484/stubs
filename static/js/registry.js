// The plug-in points feature tasks use, so they don't edit each other's screens. No DOM access.
//
//   registerShowPanel(name, {order, editorOnly, render})  a section of the show page (#/show/<id>), drawn in `order`
//                                                          below the lineup; render(el, show) fills `el`. `show` is
//                                                          the show detail with `editor: bool` added.
//   registerAdminCard(name, {order, render})               a card on #/admin, drawn in `order`; render(el) fills
//                                                          `el`, or returns false to say it has nothing to show.
//
// Registering a name again replaces the earlier entry. Equal orders keep registration order.

const panels = new Map();
const cards = new Map();

function check(kind, name, { order, render }) {
  if (typeof name !== "string" || !name) throw new TypeError(`${kind}: a name is required`);
  if (!Number.isFinite(order)) throw new TypeError(`${kind} ${name}: order must be a number`);
  if (typeof render !== "function") throw new TypeError(`${kind} ${name}: render must be a function`);
}

let seq = 0;

export function registerShowPanel(name, { order, editorOnly = false, render } = {}) {
  check("show panel", name, { order, render });
  panels.set(name, { name, order, editorOnly: Boolean(editorOnly), render, seq: seq++ });
}

export function registerAdminCard(name, { order, render } = {}) {
  check("admin card", name, { order, render });
  cards.set(name, { name, order, render, seq: seq++ });
}

const byOrder = (a, b) => a.order - b.order || a.seq - b.seq;

// The show panels this viewer may see, in order.
export function showPanels({ editor = false } = {}) {
  return [...panels.values()].filter(p => editor || !p.editorOnly).sort(byOrder);
}

// Every admin card, in order.
export function adminCards() {
  return [...cards.values()].sort(byOrder);
}
