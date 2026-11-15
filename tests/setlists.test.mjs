import assert from "node:assert/strict";
import { test } from "node:test";

import {
  countText, groupEntries, renderAct, renderDeleteAsk, renderForm, renderPanelBody, renderPreview, renderSets,
  setlistText,
} from "../static/js/setlists.js";

const setlist = {
  sets: [
    { name: null, encore: false, songs: [
      { song_id: 1, title: "Lantern Light", note: null, segue: true },
      { song_id: 2, title: "Paper <b>Boats</b>", note: "with a \"guest\"", segue: false },
    ] },
    { name: "Acoustic Set", encore: false, songs: [{ song_id: 3, title: "Low Tide Hymn", note: null, segue: false }] },
    { name: null, encore: true, songs: [{ song_id: 4, title: "Night Ferry", note: "cover; extended", segue: false }] },
    { name: "Encore 2", encore: true, songs: [{ song_id: 5, title: "Candlewick", note: null, segue: false }] },
  ],
};

const show = {
  status: "attended", version: 3,
  lineup: [
    { id: 7, name: "Marigold Static", setlist: null },
    { id: 8, name: "The Paper Lanterns", setlist: { ...setlist, source_label: "Fan <i>club</i>", source_url: "javascript:alert(1)" } },
  ],
};

test("sets are grouped with their headings, segues and notes", () => {
  const html = renderSets(setlist.sets);
  assert.equal((html.match(/<ol class="setlists-songs">/g) || []).length, 4);
  assert.match(html, /<h4 class="setlists-set-name">Acoustic Set<\/h4>/);
  assert.match(html, /<h4 class="setlists-set-name">Encore<\/h4>/);
  assert.match(html, /<h4 class="setlists-set-name">Encore 2<\/h4>/);
  assert.equal((html.match(/is-encore/g) || []).length, 2);
  assert.match(html, /Lantern Light<\/a> <span class="setlists-segue"[^>]*>›<\/span>/);
  assert.match(html, /<small class="setlists-note">cover; extended<\/small>/);
  assert.doesNotMatch(html, /setlists-new/); // only the preview marks new songs
});

test("titles and notes are escaped", () => {
  const html = renderSets(setlist.sets);
  assert.match(html, /Paper &lt;b&gt;Boats&lt;\/b&gt;/);
  assert.match(html, /with a &quot;guest&quot;/);
  assert.doesNotMatch(html, /<b>/);
});

test("the preview groups the parsed entries and tags the songs new to the act", () => {
  const entries = [
    { title: "Lantern Light", set_name: null, encore: false, note: null, segue: true, song_id: 1 },
    { title: "Ruth's <Song>", set_name: null, encore: false, note: "tease", segue: false, song_id: null },
    { title: "ruth's <song>", set_name: null, encore: false, note: null, segue: false, song_id: null },
    { title: "Night Ferry", set_name: null, encore: true, note: null, segue: false, song_id: null },
  ];
  assert.deepEqual(groupEntries(entries).map(s => [s.name, s.encore, s.songs.length]), [[null, false, 3], [null, true, 1]]);
  const html = renderPreview(entries, "The <Paper> Lanterns");
  assert.equal((html.match(/setlists-new/g) || []).length, 3);
  assert.match(html, /4 songs · 2 new to The &lt;Paper&gt; Lanterns/);
  assert.match(html, /Ruth&#39;s &lt;Song&gt;/);
  assert.doesNotMatch(html, /<Song>|<Paper>/);
  assert.match(renderPreview([], "Cold Harbor"), /No songs found/);
  assert.equal(countText([entries[0]], "Cold Harbor"), "1 song · none new to Cold Harbor");
});

test("a stored setlist goes back to text the parser reads the same way", () => {
  assert.equal(setlistText(setlist), [
    "Lantern Light >",
    "Paper <b>Boats</b> (with a \"guest\")",
    "",
    "Acoustic Set:",
    "Low Tide Hymn",
    "",
    "Encore:",
    "Night Ferry (cover; extended)",
    "",
    "Encore 2:",
    "Candlewick",
  ].join("\n"));
  assert.equal(setlistText(null), "");
});

test("the public sees only the acts with a setlist, and never the source", () => {
  const html = renderPanelBody(show, false);
  assert.match(html, /The Paper Lanterns/);
  assert.doesNotMatch(html, /Marigold Static/);
  assert.doesNotMatch(html, /<button|club|javascript/);
  assert.equal(renderPanelBody({ ...show, lineup: [show.lineup[0]] }, false), "");
});

test("editors get a tool per act, an escaped source and no unsafe link", () => {
  const html = renderPanelBody(show, true, {});
  assert.match(html, /data-lineup="7"[\s\S]*No setlist yet[\s\S]*data-act="edit">Add setlist/);
  assert.match(html, /data-lineup="8"[\s\S]*data-act="edit">Edit[\s\S]*data-act="delete">Delete/);
  assert.match(html, /From Fan &lt;i&gt;club&lt;\/i&gt;/);
  assert.doesNotMatch(html, /href="javascript/);
  assert.match(renderPanelBody({ ...show, status: "upcoming" }, true), /Add a setlist after the show/);
});

test("delete asks inline, and the form escapes what it is given", () => {
  assert.match(renderDeleteAsk(true), /Delete\? .*data-act="delete-yes">Yes.*data-act="delete-no">No/);
  assert.match(renderAct(show.lineup[1], true, { asking: 8 }), /Delete\?/);
  const form = renderForm({ text: "</textarea><b>x</b>", label: "\"><b>", url: "https://example.com/a?b=1&c=2" });
  assert.match(form, /&lt;\/textarea&gt;&lt;b&gt;x&lt;\/b&gt;<\/textarea>/);
  assert.match(form, /value="&quot;&gt;&lt;b&gt;"/);
  assert.match(form, /value="https:\/\/example.com\/a\?b=1&amp;c=2"/);
  assert.match(renderAct(show.lineup[1], true, { editing: 8, draft: { text: "", label: "", url: "" } }), /<form class="setlists-form"/);
});
