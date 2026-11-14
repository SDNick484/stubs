// DOM-free helpers of static/js/venueedit.js (Task 20).
import assert from "node:assert/strict";
import { test } from "node:test";

import { changedFields, renderConfirmRemove, renderNameRow, renderNames, renderShowCount, renderVenueForm }
  from "../static/js/venueedit.js";

test("renderShowCount", () => {
  assert.equal(renderShowCount(0), "0 shows");
  assert.equal(renderShowCount(1), "1 show");
  assert.equal(renderShowCount(3), "3 shows");
});

test("a name nothing uses can be removed; a used one cannot, and the current one is tagged", () => {
  const free = renderNameRow({ name: "Old Armory", current: false, shows: 0 });
  assert.match(free, /data-act="remove"/);
  assert.match(free, /0 shows on the ticket/);
  const used = renderNameRow({ name: "Granite Hall", current: false, shows: 2 });
  assert.doesNotMatch(used, /data-act="remove"/);
  const cur = renderNameRow({ name: "Basalt Arena", current: true, shows: 0 });
  assert.match(cur, /current/);
});

test("names are escaped", () => {
  const html = renderNames([{ name: "<b>Bad</b> \"Hall\"", current: false, shows: 0 }]);
  assert.doesNotMatch(html, /<b>/);
  assert.match(html, /&lt;b&gt;/);
  assert.doesNotMatch(renderConfirmRemove("<i>"), /<i>/);
  assert.match(renderConfirmRemove("Old"), /Remove\? .*Yes.*No/s);
});

test("the form shows the venue fields", () => {
  const html = renderVenueForm({ name: "Granite Hall", city: "Oakland", region: null, country: "US" });
  assert.match(html, /name="name" value="Granite Hall"/);
  assert.match(html, /name="region" value=""/);
});

test("changedFields sends only what differs, blank as null, country upper-cased", () => {
  const v = { name: "Granite Hall", city: "Oakland", region: "CA", country: "US" };
  assert.deepEqual(changedFields(v, { name: "Granite Hall", city: "Oakland", region: "CA", country: "US" }), {});
  assert.deepEqual(changedFields(v, { name: " Basalt Arena ", city: "Oakland", region: "", country: "us" }),
    { name: "Basalt Arena", region: null });
  assert.deepEqual(changedFields({ ...v, country: null }, { ...v, country: "ca" }), { country: "CA" });
});
