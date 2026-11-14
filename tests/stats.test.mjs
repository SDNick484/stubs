import assert from "node:assert/strict";
import { test } from "node:test";

import { cityHref, renderBars } from "../static/js/stats.js";

test("renderBars scales the longest bar to 100%", () => {
  const html = renderBars([{ year: 2019, count: 2 }, { year: 2020, count: 4 }, { year: 2021, count: 1 }]);
  assert.match(html, /data-pct="100"/);
  assert.match(html, /data-pct="50"/);
  assert.match(html, /data-pct="25"/);
  assert.equal((html.match(/data-pct="100"/g) || []).length, 1);
});

test("renderBars with nothing", () => assert.equal(renderBars([]), ""));

test("cityHref encodes parts and fills a missing region", () => {
  assert.equal(cityHref({ country: "FR", region: null, city: "Lyon" }), "#/city/FR/-/Lyon");
  assert.equal(cityHref({ country: "US", region: "CA", city: "San José" }), "#/city/US/CA/San%20Jos%C3%A9");
});
