import test from "node:test";
import assert from "node:assert/strict";
import { renderPopup, renderUnmapped, renderYearStrip } from "../static/js/home.js";

test("year strip has All and marks the selected year", () => {
  const html = renderYearStrip([{ year: 2005, count: 2 }, { year: 2006, count: 1 }], 2005);
  assert.match(html, />All</);
  assert.match(html, /is-selected" href="#\/\?year=2005" aria-current="true"[^>]*>2005</);
  assert.doesNotMatch(html, /is-selected" href="#\/\?year=2006"/);
  assert.doesNotMatch(html, /is-selected" href="#\/"/);
  assert.match(html, /title="2 shows"/);
});

test("All is selected when no year is", () => {
  const html = renderYearStrip([{ year: 2005, count: 1 }], null);
  assert.match(html, /is-selected" href="#\/" aria-current="true"/);
});

test("unmapped note", () => {
  assert.equal(renderUnmapped(0), "");
  assert.match(renderUnmapped(1), /1 show not on the map/);
  assert.match(renderUnmapped(3), /3 shows not on the map/);
});

test("popup escapes and links shows", () => {
  const html = renderPopup({ name: "<b>Hall</b>", city: "Oakland" },
    [{ id: 7, date: "2009-06-20", precision: "day", billing: "A & B" }]);
  assert.ok(!html.includes("<b>Hall"));
  assert.match(html, /href="#\/show\/7"/);
  assert.match(html, /Jun 20, 2009 · A &amp; B/);
});
