// Task 17 frontend helpers: node --test tests/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { groupImages, renderPictures } from "../static/js/images.js";

const poster = { id: 1, kind: "poster", url: "/images/1-poster-aaa.png", caption: "Tour poster" };
const photo = { id: 2, kind: "photo", url: "/images/1-photo-bbb.jpg", caption: null };
const stub = { id: 3, kind: "stub", url: "/admin/images/1-stub-ccc.jpg", caption: "Row 12" };

test("groupImages splits by kind", () => {
  assert.deepEqual(groupImages([photo, stub, poster]), { poster, photos: [photo], stubs: [stub] });
  assert.deepEqual(groupImages([]), { poster: null, photos: [], stubs: [] });
});

test("renderPictures for a visitor has no stub markup and no controls", () => {
  const html = renderPictures({ poster, photos: [photo], stubs: [stub] }, false);
  assert.match(html, /1-poster-aaa\.png/);
  assert.match(html, /1-photo-bbb\.jpg/);
  assert.doesNotMatch(html, /stub/i);
  assert.doesNotMatch(html, /Row 12/);
  assert.doesNotMatch(html, /<form|<button|type="file"/);
});

test("renderPictures for an editor shows stubs and the upload form", () => {
  const html = renderPictures({ poster, photos: [photo], stubs: [stub] }, true);
  assert.match(html, /1-stub-ccc\.jpg/);
  assert.match(html, /type="file"/);
  assert.match(html, /data-act="delete"/);
});

test("renderPictures says nothing when a visitor has nothing to see", () => {
  assert.equal(renderPictures({ poster: null, photos: [], stubs: [] }, false), "");
});

test("renderPictures escapes captions and urls", () => {
  const evil = { id: 9, kind: "photo", url: "/images/a\"onerror=\"x.jpg", caption: "<script>x</script>" };
  const html = renderPictures({ poster: null, photos: [evil], stubs: [] }, false);
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /"onerror="/);
  assert.match(html, /&lt;script&gt;/);
});
