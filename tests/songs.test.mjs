import assert from "node:assert/strict";
import { test } from "node:test";

import {
  heardLabel, renderArtistSongs, renderHeard, renderMergeConfirm, renderNeverHeard, renderPlay, renderSong,
  renderSongSummary, renderSongSummaryLine,
} from "../static/js/songs.js";
import { renderStats, renderTopSongs } from "../static/js/stats.js";
import { renderSets } from "../static/js/setlists.js";

const song = (id, title, plays, in_catalog = false) => ({ id, title, plays, shows: plays, first: null, last: null, in_catalog });

test("heardLabel: once, twice and n times", () => {
  assert.equal(heardLabel(0), "Not heard yet");
  assert.equal(heardLabel(1), "Heard once");
  assert.equal(heardLabel(3), "Heard 3 times");
});

test("renderSongSummary links the title and says how often it was heard, escaped", () => {
  const html = renderSongSummary(song(7, "<b>Low</b> & \"Tide\"", 3));
  assert.match(html, /href="#\/song\/7"/);
  assert.match(html, /Heard 3 times/);
  assert.ok(!html.includes("<b>"));
  assert.match(renderSongSummary(song(8, "Night Ferry", 1)), /Heard once/);
});

test("renderSongSummaryLine has the count, first and last", () => {
  const html = renderSongSummaryLine({ plays: 4, first: "2019-05-04", last: "2023-07-06" });
  assert.match(html, /Heard 4 times/);
  assert.match(html, /first May 4, 2019/);
  assert.match(html, /last Jul 6, 2023/);
  assert.doesNotMatch(renderSongSummaryLine({ plays: 1, first: null, last: null }), /first/);
});

test("renderHeard shows the top 10 and expands with All songs", () => {
  const rows = Array.from({ length: 12 }, (_, i) => song(i + 1, `Song ${i + 1}`, 12 - i));
  const top = renderHeard(rows);
  assert.equal((top.match(/<li>/g) || []).length, 10);
  assert.match(top, /All songs \(12\)/);
  const all = renderHeard(rows, true);
  assert.equal((all.match(/<li>/g) || []).length, 12);
  assert.match(all, /data-act="top"/);
  assert.doesNotMatch(renderHeard(rows.slice(0, 3)), /All songs/);
});

test("renderHeard ignores catalog songs with no plays, and says so when nothing was heard", () => {
  assert.match(renderHeard([song(1, "Moth Orchard", 0, true)]), /No setlists stored/);
});

test("renderNeverHeard lists catalog songs with no plays", () => {
  const rows = [song(1, "Lantern Light", 3, true), song(2, "Moth Orchard", 0, true), song(3, "Candlewick", 1)];
  const html = renderNeverHeard(rows);
  assert.match(html, /Moth Orchard/);
  assert.doesNotMatch(html, /Lantern Light|Candlewick/);
  assert.doesNotMatch(html, /Remove|<form/); // the public gets no tools
  assert.match(renderNeverHeard(rows, true), /data-act="remove" data-id="2"/);
});

test("renderNeverHeard with no catalog says so, and only editors get the paste", () => {
  assert.match(renderNeverHeard([song(1, "Lantern Light", 3)]), /No song catalog/);
  assert.doesNotMatch(renderNeverHeard([song(1, "Lantern Light", 3)]), /<form/);
  assert.match(renderNeverHeard([], true), /<form class="song-catalog-form"/);
  assert.match(renderNeverHeard([song(1, "Moth Orchard", 3, true)]), /heard every song/);
});

test("renderNeverHeard escapes titles", () => {
  const html = renderNeverHeard([song(1, "<img src=x onerror=1>", 0, true)], true);
  assert.ok(!html.includes("<img"));
  assert.match(html, /&lt;img/);
});

test("renderArtistSongs puts Songs heard before Never heard", () => {
  const html = renderArtistSongs([song(1, "Lantern Light", 2, true)]);
  assert.ok(html.indexOf("Songs heard") < html.indexOf("Never heard"));
});

test("renderMergeConfirm names both songs and the plays that move", () => {
  assert.equal(renderMergeConfirm("Lantern Lite", "Lantern Light", 3), "Merge 'Lantern Lite' into 'Lantern Light'? 3 plays move.");
  assert.match(renderMergeConfirm("A", "B", 1), /1 play moves\./);
  assert.ok(!renderMergeConfirm("<i>x</i>", "y", 2).includes("<i>"));
});

test("renderPlay has the date, the venue, the set and the note, escaped", () => {
  const html = renderPlay({ id: 5, date: "2021-06-05", precision: "day", billing: "The Paper Lanterns", venue: { label: "Granite Hall" },
    set_name: "Set 2", encore: false, note: "with <strings>" });
  assert.match(html, /href="#\/show\/5"/);
  assert.match(html, /Set 2 · with &lt;strings&gt;/);
  assert.match(renderPlay({ id: 5, date: "2021", precision: "year", billing: "x", venue: {}, set_name: null, encore: true, note: null }), /Encore/);
});

test("renderSong gives the editor the merge tools and the public none", () => {
  const s = { id: 1, title: "Lantern Light", artist: { id: 2, name: "The Paper Lanterns" }, plays: 2, first: "2019-05-04", last: "2021-06-05",
    in_catalog: true, aliases: ["lantern lite"], shows: [] };
  assert.match(renderSong(s, true), /Merge into…/);
  assert.match(renderSong(s, true), /Remove from catalog/);
  assert.doesNotMatch(renderSong(s, false), /Merge into|button/);
  assert.match(renderSong(s, false), /Also known as lantern lite/);
  assert.match(renderSong(s, false), /href="#\/artist\/2"/);
});

test("renderTopSongs lists the most heard songs and is empty with none", () => {
  assert.equal(renderTopSongs([]), "");
  assert.equal(renderTopSongs(undefined), "");
  const html = renderTopSongs([{ song_id: 3, title: "Lantern Light", artist_id: 1, artist: "The Paper Lanterns", count: 5 }]);
  assert.match(html, /Most heard songs/);
  assert.match(html, /href="#\/song\/3"/);
  assert.match(html, /Lantern Light · The Paper Lanterns/);
});

test("renderStats includes the songs list", () => {
  const s = { totals: { shows: 1, artists: 1, venues: 1, cities: 1 }, per_year: [], top_artists: [], top_venues: [], top_cities: [],
    top_songs: [{ song_id: 3, title: "Lantern Light", artist_id: 1, artist: "X", count: 5 }] };
  assert.match(renderStats(s), /Most heard songs/);
});

test("a stored setlist song links to its song page, a new one does not", () => {
  const html = renderSets([{ name: null, encore: false, songs: [
    { song_id: 4, title: "Lantern Light", note: null, segue: false }, { song_id: null, title: "Moth Orchard", note: null, segue: false }] }], true);
  assert.match(html, /<a class="setlists-title" href="#\/song\/4">Lantern Light<\/a>/);
  assert.match(html, /<span class="setlists-title">Moth Orchard<\/span>/);
});
