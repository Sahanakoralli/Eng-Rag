import { useEffect, useState } from "react";
import * as auth from "./auth.js";

export default function AuthPage({ onAuthed, notice = "", initialMode = "login", onBack }) {
  const [mode, setMode] = useState(initialMode);
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [needKey, setNeedKey] = useState(false);
  const [key, setKey] = useState(auth.getAccessKey());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(notice);

  const login = mode === "login";
  useEffect(() => { auth.needsAccessKey().then(setNeedKey); }, []);

  const switchMode = (m) => { setMode(m); setError(""); };

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      if (needKey) auth.setAccessKey(key.trim());
      const user = login
        ? await auth.login(username.trim(), password)
        : await auth.signup(email.trim(), username.trim(), password);
      onAuthed(user); // this page unmounts now, so there is nothing left to reset
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return (
    <main className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        {onBack && <button type="button" className="auth-back" onClick={onBack}>Back to the project page</button>}
        <h1>Chat with your documents</h1>
        <p className="hint">{login ? "Log in to see your chats and files." : "Create an account to keep your chats and files private."}</p>

        <div className="auth-tabs" role="tablist" aria-label="Log in or sign up">
          <button type="button" role="tab" aria-selected={login} className={login ? "on" : ""} onClick={() => switchMode("login")}>Log in</button>
          <button type="button" role="tab" aria-selected={!login} className={!login ? "on" : ""} onClick={() => switchMode("signup")}>Sign up</button>
        </div>

        {!login && (
          <label>Email
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
          </label>
        )}
        <label>{login ? "Username or email" : "Username"}
          <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required autoFocus
                 pattern={login ? undefined : "[A-Za-z0-9_.\-]{3,32}"}
                 title={login ? undefined : "3 to 32 letters, digits, dots, dashes or underscores"} />
        </label>
        <label>Password
          <span className="pw">
            <input type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)}
                   autoComplete={login ? "current-password" : "new-password"} required minLength={login ? undefined : 8} maxLength={128} />
            <button type="button" className="pw-toggle" aria-pressed={show} onClick={() => setShow((s) => !s)}>{show ? "Hide" : "Show"}</button>
          </span>
          {!login && <span className="hint">At least 8 characters.</span>}
        </label>
        {needKey && (
          <label>Server access key
            <input type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" required />
          </label>
        )}

        <div className="auth-error" role="alert">{error}</div>
        <button className="primary" type="submit" disabled={busy}>{busy ? "Please wait…" : login ? "Log in" : "Create account"}</button>
      </form>
    </main>
  );
}
