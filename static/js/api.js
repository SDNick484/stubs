// The only place that talks to the server. Every call is same-origin with credentials (the Cloudflare Access
// cookie under /admin) and no caching. A body is sent as JSON, or as-is when it is a FormData (file uploads).
//
// Failures throw an ApiError, an Error with `status` and `error` (the server's {"error"} message, or one of ours
// when the server can't be reached or doesn't answer in JSON): `catch ({ status, error })` works.

export class ApiError extends Error {
  constructor(status, error, data = null) {
    super(error);
    this.name = "ApiError";
    this.status = status;
    this.error = error;
    this.data = data;
  }
}

async function request(method, path, body) {
  const headers = { Accept: "application/json" };
  const init = { method, headers, credentials: "same-origin", cache: "no-store" };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Can't reach the server. Check the connection and try again.");
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (res.ok) {
    if (data === null) throw new ApiError(res.status, "Something went wrong. Reload the page.");
    return data;
  }
  throw new ApiError(res.status, data?.error ?? `Something went wrong (${res.status}). Try again.`, data);
}

export const get = path => request("GET", path);
export const send = (method, path, body) => request(method, path, body);
