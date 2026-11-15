// DOM-free helpers of static/js/musicbrainz.js (post-v1 #45).
import assert from "node:assert/strict";
import { test } from "node:test";

import { mbUrl, renderCandidate, renderMbBlock, renderMergeLink, renderSuggestion } from "../static/js/musicbrainz.js";

const HARBOR = "11111111-1111-4111-8111-111111111111";
const cand = { mbid: HARBOR, name: "Cold Harbor", disambiguation: "UK post-punk band", country: "GB", score: 100 };

test("mbUrl links to the artist's MusicBrainz page", () => assert.equal(mbUrl(HARBOR), `https://musicbrainz.org/artist/${HARBOR}`));

test("renderSuggestion shows the top match with its score, note and Link / Skip", () => {
  const html = renderSuggestion({ candidates: [cand, { ...cand, name: "Cold Harbour", score: 86 }], exact: HARBOR });
  assert.match(html, /Cold Harbor<\/strong>/);
  assert.match(html, /UK post-punk band · GB/);
  assert.match(html, /<span class="tag">100<\/span>/);
  assert.match(html, /exact/);
  assert.match(html, /data-act="link"/);
  assert.match(html, /data-act="skip"/);
  assert.doesNotMatch(html, /Cold Harbour/);
});

test("renderSuggestion without the exact flag or a note", () => {
  const html = renderSuggestion({ candidates: [{ ...cand, disambiguation: null, country: null, score: 90 }], exact: null });
  assert.doesNotMatch(html, /exact/);
  assert.match(html, /<span class="muted"><\/span>/);
});

test("renderSuggestion says so on a miss", () => assert.match(renderSuggestion({ candidates: [], exact: null }), /No match\./));

test("a name from MusicBrainz is escaped", () => {
  const html = renderCandidate({ ...cand, name: `<img src=x onerror="1">`, disambiguation: `"quoted" & <b>` });
  assert.doesNotMatch(html, /<img|<b>/);
  assert.match(html, /&lt;img/);
  assert.match(html, /&quot;quoted&quot; &amp; &lt;b&gt;/);
});

test("renderMbBlock: the public link, and the controls only for editors", () => {
  const a = { id: 3, mbid: HARBOR, mb_disambiguation: "UK post-punk band" };
  const pub = renderMbBlock({ id: 3, mbid: HARBOR }, false);
  assert.match(pub, new RegExp(`href="https://musicbrainz.org/artist/${HARBOR}"`));
  assert.doesNotMatch(pub, /mb-controls/);
  assert.match(renderMbBlock(a, true), /UK post-punk band/);
  assert.match(renderMbBlock(a, true), /data-role="mb-controls"/);
  assert.doesNotMatch(renderMbBlock({ id: 3, mbid: null }, false), /MusicBrainz/);
  assert.match(renderMbBlock({ id: 3, mbid: null }, true), /data-role="mb-controls"/);
});

test("renderMergeLink points at the merge screen with both artists", () =>
  assert.match(renderMergeLink(7, 9), /href="#\/admin\/merge\?keep=7&amp;drop=9"/));
