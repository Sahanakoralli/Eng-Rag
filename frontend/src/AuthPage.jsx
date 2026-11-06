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
      <main className="auth-page">
        <form className="auth-card" onSubmit={(e) => { e.preventDefault(); verify(code); }}>
          <button type="button" className="auth-back" onClick={backToForm}>Change number</button>
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
      </main>
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
    <main className="auth-page">
      <form className="auth-card" onSubmit={submitForm}>
        {onBack && <button type="button" className="auth-back" onClick={onBack}>Back to the project page</button>}
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
    </main>
  );
}
