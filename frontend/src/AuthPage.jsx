import { useEffect, useRef, useState } from "react";
import * as auth from "./auth.js";

const OTP_LEN = 6;

const maskPhone = (p) => `+91 ${p.slice(0, 2)}••••••${p.slice(-2)}`;

function PhoneField({ value, onChange, autoFocus }) {
  return (
    <label>Mobile number
      <span className="phone">
        <span className="cc" aria-hidden="true">+91</span>
        <input type="tel" inputMode="numeric" autoComplete="tel-national" placeholder="10-digit number"
               value={value} onChange={(e) => onChange(auth.cleanPhone(e.target.value))}
               pattern="[6-9][0-9]{9}" maxLength={16} title="A 10-digit Indian mobile number" autoFocus={autoFocus} required />
      </span>
    </label>
  );
}

const Svg = ({ children, size = 20, sw = 1.9 }) => (
  <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">{children}</svg>
);
const LockIcon = (p) => <Svg {...p}><rect x="5" y="11" width="14" height="9" rx="2.5" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></Svg>;
const MessageIcon = (p) => <Svg {...p}><path d="M5 5h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-6l-5 4v-4H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z" /><path d="M8 10h8M8 13h5" /></Svg>;

const POINTS = [
  ["Upload PDF, Word, PowerPoint, HTML or text files", <path key="a" d="M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6" />],
  ["Every answer cites the document and page", <path key="b" d="M9 7H6a2 2 0 0 0-2 2v3h5V7ZM9 12c0 3-1 4-3 5M19 7h-3a2 2 0 0 0-2 2v3h5V7ZM19 12c0 3-1 4-3 5" />],
  ["Your chats and files stay in your own workspace", <g key="c"><rect x="5" y="11" width="14" height="9" rx="2.5" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></g>],
];

function LogoMark() {
  return (
    <svg viewBox="0 0 32 32" width="34" height="34" aria-hidden="true" fill="none" strokeLinecap="round" strokeLinejoin="round">
      <defs><linearGradient id="al" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#1f9bff" /><stop offset="1" stopColor="#6a5cff" /></linearGradient></defs>
      <path d="M17 27H9a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v6" stroke="url(#al)" strokeWidth="2.2" />
      <path d="M11 11h6M11 15h4" stroke="url(#al)" strokeWidth="2.2" />
      <circle cx="21" cy="21" r="4.5" stroke="url(#al)" strokeWidth="2.2" />
      <path d="m24.5 24.5 3.5 3.5" stroke="url(#al)" strokeWidth="2.2" />
    </svg>
  );
}

function AuthArt() {
  return (
    <svg className="auth-illus" viewBox="0 0 460 400" role="img" aria-label="A robot guarding your documents behind a lock, next to a chat with a cited answer">
      <defs>
        <radialGradient id="aGlow" cx=".5" cy=".5" r=".5"><stop offset="0" stopColor="#4fb3ff" stopOpacity=".55" /><stop offset="1" stopColor="#4fb3ff" stopOpacity="0" /></radialGradient>
        <linearGradient id="aWhite" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#ffffff" /><stop offset="1" stopColor="#c3d6ff" /></linearGradient>
        <linearGradient id="aScreen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#0a1236" /><stop offset="1" stopColor="#1b2670" /></linearGradient>
        <linearGradient id="aBlue" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#2fb4ff" /><stop offset="1" stopColor="#6a7bff" /></linearGradient>
      </defs>
      <circle cx="235" cy="215" r="180" fill="url(#aGlow)" />

      {/* chat window with a cited answer */}
      <g transform="translate(16 46)">
        <rect width="236" height="216" rx="20" fill="#fff" stroke="#cfe3f5" strokeWidth="2" />
        <circle cx="22" cy="20" r="4.5" fill="#ff8aa0" /><circle cx="38" cy="20" r="4.5" fill="#ffd36e" /><circle cx="54" cy="20" r="4.5" fill="#7fdca5" />
        <rect x="70" y="42" width="150" height="34" rx="17" fill="url(#aBlue)" />
        <g stroke="#fff" strokeWidth="5" strokeLinecap="round"><path d="M90 59h60" /><path d="M160 59h40" /></g>
        <rect x="16" y="90" width="196" height="70" rx="16" fill="#eaf4ff" />
        <g stroke="#a9c8ee" strokeWidth="5" strokeLinecap="round"><path d="M32 108h150" /><path d="M32 124h164" /><path d="M32 140h96" /></g>
        <rect x="16" y="172" width="104" height="28" rx="14" fill="#e3f1fd" stroke="#1f6fe0" strokeWidth="1.5" />
        <text x="68" y="191" textAnchor="middle" fontSize="13" fontWeight="700" fill="#1f6fe0" fontFamily="Lato, system-ui, sans-serif">[1] page 42</text>
      </g>

      {/* PDF sheet */}
      <g transform="translate(34 300) rotate(-7)">
        <rect width="88" height="100" rx="11" fill="#fff" stroke="#cfe3f5" strokeWidth="2" />
        <g stroke="#b9d3f0" strokeWidth="5" strokeLinecap="round"><path d="M46 26h26" /><path d="M16 66h56" /><path d="M16 82h40" /></g>
        <rect x="-12" y="22" width="60" height="26" rx="8" fill="#ff5b78" />
        <text x="18" y="41" textAnchor="middle" fontSize="15" fontWeight="800" fill="#fff" fontFamily="Lato, system-ui, sans-serif">PDF</text>
      </g>

      {/* robot */}
      <g transform="translate(278 128) scale(.8)">
        <rect x="26" y="140" width="140" height="100" rx="50" fill="url(#aWhite)" />
        <rect x="-14" y="50" width="26" height="54" rx="13" fill="#5b8cff" />
        <rect x="180" y="50" width="26" height="54" rx="13" fill="#5b8cff" />
        <rect x="0" y="0" width="192" height="146" rx="64" fill="url(#aWhite)" />
        <rect x="18" y="20" width="156" height="102" rx="46" fill="url(#aScreen)" />
        <g stroke="#7fe8ff" strokeWidth="9" strokeLinecap="round" fill="none"><path d="M52 68q15-20 30 0" /><path d="M110 68q15-20 30 0" /><path d="M80 92q16 15 32 0" /></g>
        <ellipse cx="44" cy="92" rx="9" ry="5" fill="#ff8fb4" opacity=".55" /><ellipse cx="148" cy="92" rx="9" ry="5" fill="#ff8fb4" opacity=".55" />
      </g>

      {/* shield with lock */}
      <g transform="translate(350 318)">
        <path d="M0-44 38-30V8C38 32 18 48 0 56-18 48-38 32-38 8V-30Z" fill="url(#aBlue)" stroke="#fff" strokeWidth="4" />
        <rect x="-13" y="-6" width="26" height="22" rx="5" fill="#fff" />
        <path d="M-8-6v-7a8 8 0 0 1 16 0v7" stroke="#fff" strokeWidth="4" fill="none" strokeLinecap="round" />
        <circle cx="0" cy="5" r="3" fill="#6a7bff" />
      </g>

      <g stroke="#7fb0ff" strokeWidth="5" strokeLinecap="round"><path d="M405 70 415 52" /><path d="M424 92 446 84" /><path d="M418 66 432 60" /></g>
    </svg>
  );
}

function Shell({ children }) {
  return (
    <main className="auth-page">
      <aside className="auth-art">
        <div className="auth-brand"><LogoMark />RAG QA</div>
        <h2>Answers that point to the page.</h2>
        <p>Upload your documents, ask in plain language, and check every answer against its source.</p>
        <AuthArt />
        <ul className="auth-points">
          {POINTS.map(([text, icon]) => (
            <li key={text}><span><Svg size={18}>{icon}</Svg></span>{text}</li>
          ))}
        </ul>
      </aside>
      <div className="auth-side">{children}</div>
    </main>
  );
}

export default function AuthPage({ onAuthed, notice = "", initialMode = "login", onBack }) {
  const [mode, setMode] = useState(initialMode);      // "login" | "signup"
  const [method, setMethod] = useState("password");   // login only: "password" | "otp"
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [needKey, setNeedKey] = useState(false);
  const [key, setKey] = useState(auth.getAccessKey());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(notice);
  const [info, setInfo] = useState("");

  // step 2: the one-time code. null = still on the form.
  const [otpStep, setOtpStep] = useState(null); // { kind: "signup" | "login", phone, details? }
  const [code, setCode] = useState("");
  const [cooldown, setCooldown] = useState(0);
  const [expiresIn, setExpiresIn] = useState(300);
  const codeRef = useRef(null);

  const login = mode === "login";
  const viaOtp = login && method === "otp";

  useEffect(() => { auth.needsAccessKey().then(setNeedKey); }, []);

  // resend countdown
  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  useEffect(() => { if (otpStep) codeRef.current?.focus(); }, [otpStep]);

  const switchMode = (m) => { setMode(m); setError(""); };
  const switchMethod = (m) => { setMethod(m); setError(""); };

  const startOtp = (step, sent) => {
    setOtpStep(step);
    setCooldown(Number(sent?.resend_in) || 30);
    setExpiresIn(Number(sent?.expires_in) || 300);
    setCode("");
    setError("");
    setInfo("");
  };

  const submitForm = async (e) => {
    e.preventDefault();
    if (busy) return;
    setError("");
    if (!login || viaOtp) {
      if (!auth.isValidPhone(phone)) return setError("Enter a valid 10-digit mobile number.");
    }
    setBusy(true);
    try {
      if (needKey) auth.setAccessKey(key.trim());
      if (login && !viaOtp) {
        onAuthed(await auth.login(username.trim(), password));
        return;
      }
      if (viaOtp) {
        const sent = await auth.requestLoginOtp(phone);
        startOtp({ kind: "login", phone }, sent);
      } else {
        const details = { email: email.trim(), username: username.trim(), password, phone };
        const sent = await auth.requestSignupOtp(details);
        startOtp({ kind: "signup", phone, details }, sent);
      }
      setBusy(false);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  const verify = async (value) => {
    if (busy || value.length !== OTP_LEN) return;
    setBusy(true);
    setError("");
    try {
      const user = otpStep.kind === "signup"
        ? await auth.signup(otpStep.details, value)
        : await auth.verifyLoginOtp(otpStep.phone, value);
      onAuthed(user);
    } catch (err) {
      setError(err.message);
      setCode("");
      setBusy(false);
      codeRef.current?.focus();
    }
  };

  const onCode = (e) => {
    const v = e.target.value.replace(/\D/g, "").slice(0, OTP_LEN);
    setCode(v);
    if (v.length === OTP_LEN) verify(v); // submit as soon as the last digit is typed or pasted
  };

  const resend = async () => {
    if (cooldown > 0 || busy) return;
    setError("");
    setInfo("");
    try {
      const sent = otpStep.kind === "signup"
        ? await auth.requestSignupOtp(otpStep.details)
        : await auth.requestLoginOtp(otpStep.phone);
      setCooldown(Number(sent?.resend_in) || 30);
      setExpiresIn(Number(sent?.expires_in) || 300);
      setInfo("A new code has been sent.");
    } catch (err) {
      setError(err.message);
    }
  };

  const backToForm = () => { setOtpStep(null); setCode(""); setError(""); setInfo(""); };

  /* ------------------------------------------------------------ step 2: enter the code */
  if (otpStep) {
    const mins = Math.max(1, Math.round(expiresIn / 60));
    return (
      <Shell>
        <form className="auth-card" onSubmit={(e) => { e.preventDefault(); verify(code); }}>
          <button type="button" className="auth-back" onClick={backToForm}>Change number</button>
          <span className="auth-badge"><MessageIcon size={24} /></span>
          <h1>Enter your code</h1>
          <p className="otp-sent">
            We texted a {OTP_LEN}-digit code to <b>{maskPhone(otpStep.phone)}</b>. It expires in {mins} minute{mins === 1 ? "" : "s"}.
          </p>
          <label>One-time code
            <input ref={codeRef} className="otp-input" type="text" inputMode="numeric" pattern="[0-9]*" maxLength={OTP_LEN}
                   autoComplete="one-time-code" value={code} onChange={onCode} placeholder="······" disabled={busy} required />
          </label>
          <div className="otp-actions">
            <span>Didn’t get it?</span>
            <button type="button" onClick={resend} disabled={cooldown > 0 || busy}>
              {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend code"}
            </button>
          </div>
          <div className="auth-ok" role="status">{info}</div>
          <div className="auth-error" role="alert">{error}</div>
          <button className="primary" type="submit" disabled={busy || code.length !== OTP_LEN}>
            {busy ? "Verifying…" : otpStep.kind === "signup" ? "Verify and create account" : "Verify and log in"}
          </button>
        </form>
      </Shell>
    );
  }

  /* ------------------------------------------------------------ step 1: details */
  const hint = !login
    ? "Create an account. We’ll text a code to your mobile number to verify it."
    : viaOtp
      ? "Enter the mobile number on your account and we’ll text you a login code."
      : "Log in to see your chats and files.";
  const submitLabel = busy ? "Please wait…" : login && !viaOtp ? "Log in" : "Send me a code";

  return (
    <Shell>
      <form className="auth-card" onSubmit={submitForm}>
        {onBack && <button type="button" className="auth-back" onClick={onBack}>Back to the project page</button>}
        <span className="auth-badge"><LockIcon size={24} /></span>
        <h1>Chat with your documents</h1>
        <p className="hint">{hint}</p>

        <div className="auth-tabs" role="tablist" aria-label="Log in or sign up">
          <button type="button" role="tab" aria-selected={login} className={login ? "on" : ""} onClick={() => switchMode("login")}>Log in</button>
          <button type="button" role="tab" aria-selected={!login} className={!login ? "on" : ""} onClick={() => switchMode("signup")}>Sign up</button>
        </div>

        {login && (
          <div className="auth-methods" role="group" aria-label="How to log in">
            <button type="button" aria-pressed={!viaOtp} className={!viaOtp ? "on" : ""} onClick={() => switchMethod("password")}>Password</button>
            <button type="button" aria-pressed={viaOtp} className={viaOtp ? "on" : ""} onClick={() => switchMethod("otp")}>Phone OTP</button>
          </div>
        )}

        {/* sign up: email, username, mobile, password */}
        {!login && (
          <>
            <label>Email
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required autoFocus />
            </label>
            <label>Username
              <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required
                     pattern="[A-Za-z0-9_.\-]{3,32}" title="3 to 32 letters, digits, dots, dashes or underscores" />
            </label>
            <PhoneField value={phone} onChange={setPhone} />
          </>
        )}

        {/* log in with password */}
        {login && !viaOtp && (
          <label>Username or email
            <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required autoFocus />
          </label>
        )}

        {/* log in with OTP */}
        {viaOtp && <PhoneField value={phone} onChange={setPhone} autoFocus />}

        {!viaOtp && (
          <label>Password
            <span className="pw">
              <input type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)}
                     autoComplete={login ? "current-password" : "new-password"} required minLength={login ? undefined : 8} maxLength={128} />
              <button type="button" className="pw-toggle" aria-pressed={show} onClick={() => setShow((s) => !s)}>{show ? "Hide" : "Show"}</button>
            </span>
            {!login && <span className="hint">At least 8 characters.</span>}
          </label>
        )}

        {needKey && (
          <label>Server access key
            <input type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" required />
          </label>
        )}

        <div className="auth-error" role="alert">{error}</div>
        <button className="primary" type="submit" disabled={busy}>{submitLabel}</button>
      </form>
    </Shell>
  );
}
