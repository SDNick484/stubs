// DOM-free helpers of static/js/venues.js (Task 16).
import assert from "node:assert/strict";
import { test } from "node:test";

import { renderProgress, parseCoord } from "../static/js/venues.js";

test("renderProgress", () => assert.equal(renderProgress(4, 12), "4 of 12…"));

test("parseCoord reads a number in range or says null", () => {
  assert.equal(parseCoord("37.5", -90, 90), 37.5);
  assert.equal(parseCoord(" -122.25 ", -180, 180), -122.25);
  assert.equal(parseCoord("91", -90, 90), null);
  assert.equal(parseCoord("", -90, 90), null);
  assert.equal(parseCoord("abc", -90, 90), null);
});
