// Login token storage, a fetch wrapper that attaches it, and the account calls.
// Backend routes used:
//   POST /auth/signup/otp   signup step 1: validate details, text a code to the phone
//   POST /auth/signup       signup step 2: details + code -> account + token
//   POST /auth/login        username/email + password -> token
//   POST /auth/otp/request  login by OTP, step 1: text a code to a registered phone
//   POST /auth/otp/verify   login by OTP, step 2: phone + code -> token
//   POST /auth/logout, GET /auth/me, GET /health

import { getKey, setKey } from "./api.js"; // the optional server access key (API_KEY) is stored by api.js

const TOKEN_KEY = "engrag_token";

// Leave empty when the page and the API share one origin (the built app, or a Vite dev proxy).
// Set VITE_API_URL=http://127.0.0.1:8000 in frontend/.env if the dev server calls the API directly
// (then the backend also needs CORS enabled for the Authorization header).
export const API_BASE = (import.meta.env?.VITE_API_URL || "").replace(/\/+$/, "");

export const getToken = () => localStorage.getItem(TOKEN_KEY) || "";
const setToken = (t) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);
export const setAccessKey = setKey;
export const getAccessKey = getKey;

function authHeaders() {
  const h = {};
  const token = getToken();
  const key = getKey();
  if (token) h.Authorization = `Bearer ${token}`;
  if (key) h["X-API-Key"] = key;
  return h;
}

// Turn a FastAPI error body into one readable sentence.
export function errorText(data, fallback) {
  const d = data && data.detail;
  if (Array.isArray(d)) {
    return d.map((i) => {
      const field = Array.isArray(i.loc) && i.loc.length ? i.loc[i.loc.length - 1] + ": " : "";
      return field + String(i.msg).replace(/^Value error, /, "");
    }).join("; ");
  }
  return d || fallback;
}

const urlOf = (input) => (typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
const isApiUrl = (u) => (u.startsWith("/") && !u.startsWith("//")) || (API_BASE !== "" && u.startsWith(API_BASE));

let installed = false;
// Makes every existing fetch("/ask"), fetch("/files"), ... send the login token, and sends the user back to the
// login page when the server says the token is no longer valid. Requests to other sites are left alone.
export function installFetchAuth() {
  if (installed) return;
  installed = true;
  const original = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = urlOf(input);
    if (!isApiUrl(url)) return original(input, init);
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
    for (const [k, v] of Object.entries(authHeaders())) if (!headers.has(k)) headers.set(k, v);
    const hadToken = Boolean(getToken());
    const res = await original(input, { ...init, headers });
    if (res.status === 401 && hadToken && !/\/auth\/(login|signup|otp)/.test(url)) {
      // Only an invalid/expired login ends the session. A wrong X-API-Key is also a 401 but must not log anyone out.
      let detail = "";
      try { detail = (await res.clone().json()).detail; } catch { /* not JSON */ }
      if (detail === "Please log in.") {
        clearToken();
        window.dispatchEvent(new Event("engrag:logout"));
      }
    }
    return res;
  };
}

async function call(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(API_BASE + path, {
      method,
      headers: { ...authHeaders(), ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("Can't reach the server. Is it running?");
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty or non-JSON body */ }
  if (!res.ok) throw new Error(errorText(data, `Request failed (${res.status})`));
  if (data === null) {
    // A 200 that is not JSON: in dev this is Vite's index.html because "/auth" is missing from the proxy in vite.config.js
    throw new Error('The server did not answer like the API. In dev, add "/auth" to the proxy in vite.config.js.');
  }
  return data;
}

// Keep the token and hand back the user.
function finish(r) {
  setToken(r.access_token);
  return r.user;
}

/* ---------------------------------------------------------------- phone numbers */

// Reduce whatever was typed or pasted ("+91 98765 43210", "09876543210") to at most 10 digits.
export function cleanPhone(v) {
  let d = String(v || "").replace(/\D/g, "");
  if (d.length > 10 && d.startsWith("91")) d = d.slice(2);
  else if (d.length > 10 && d.startsWith("0")) d = d.slice(1);
  return d.slice(0, 10);
}

export const isValidPhone = (p) => /^[6-9]\d{9}$/.test(p);

/* ---------------------------------------------------------------- password login */

export async function login(identifier, password) {
  return finish(await call("/auth/login", { method: "POST", body: { identifier, password } }));
}

/* ---------------------------------------------------------------- OTP login */

// Texts a code to a number that already has an account. Resolves to { sent, expires_in, resend_in }.
export const requestLoginOtp = (phone) => call("/auth/otp/request", { method: "POST", body: { phone } });

export async function verifyLoginOtp(phone, code) {
  return finish(await call("/auth/otp/verify", { method: "POST", body: { phone, code } }));
}

/* ---------------------------------------------------------------- signup (phone verified by OTP) */

// Step 1: the server checks email/username/phone are valid and free, then texts a code.
export const requestSignupOtp = ({ email, username, password, phone }) =>
  call("/auth/signup/otp", { method: "POST", body: { email, username, password, phone } });

// Step 2: the same details plus the code create the account and log in.
export async function signup({ email, username, password, phone }, code) {
  return finish(await call("/auth/signup", { method: "POST", body: { email, username, password, phone, code } }));
}

/* ---------------------------------------------------------------- session */

export async function logout() {
  const token = getToken();
  clearToken(); // forget it locally first, so a failing request can never look like an expired session
  if (!token) return;
  try {
    await fetch(API_BASE + "/auth/logout", { method: "POST", headers: { ...authHeaders(), Authorization: `Bearer ${token}` } });
  } catch { /* server unreachable: the local token is already gone */ }
}

export const fetchMe = () => call("/auth/me");

export async function needsAccessKey() {
  try { return Boolean((await call("/health")).auth_required); } catch { return false; }
}
