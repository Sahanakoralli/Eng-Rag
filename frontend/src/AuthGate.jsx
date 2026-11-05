import { createContext, Fragment, useCallback, useContext, useEffect, useState } from "react";
import AuthPage from "./AuthPage.jsx";
import HomePage from "./HomePage.jsx";
import * as auth from "./auth.js";

const Ctx = createContext({ user: null, logout: () => {} });
export const useAuth = () => useContext(Ctx);

// Logged out: the project page, with Log in / Create account leading to the login form.
// Logged in: the app. The project page is one click away at #about, and the app keeps its state while you read it.
// The app is remounted for every new user, so one person's chats are never left on screen for the next.
export default function AuthGate({ children }) {
  const [state, setState] = useState({ status: auth.getToken() ? "loading" : "out", user: null, notice: "" });
  const [view, setView] = useState("home"); // while logged out: "home" | "login" | "signup"
  const [hash, setHash] = useState(() => window.location.hash);

  useEffect(() => {
    auth.installFetchAuth();
    let alive = true;
    if (auth.getToken()) {
      auth.fetchMe()
        .then((user) => alive && setState({ status: "in", user, notice: "" }))
        .catch(() => alive && setState({ status: "out", user: null, notice: "" }));
    }
    const expired = () => {
      setView("login");
      setState({ status: "out", user: null, notice: "Your session expired. Log in again." });
    };
    const onHash = () => setHash(window.location.hash);
    window.addEventListener("engrag:logout", expired);
    window.addEventListener("hashchange", onHash);
    return () => {
      alive = false;
      window.removeEventListener("engrag:logout", expired);
      window.removeEventListener("hashchange", onHash);
    };
  }, []);

  const logout = useCallback(async () => {
    await auth.logout();
    setView("home");
    setState({ status: "out", user: null, notice: "" });
  }, []);

  const openApp = () => {
    window.history.pushState(null, "", window.location.pathname + window.location.search);
    setHash("");
  };

  if (state.status === "loading") return <div className="auth-loading">Loading…</div>;

  if (state.status === "out") {
    if (view === "home") return <HomePage onLogin={() => setView("login")} onSignup={() => setView("signup")} />;
    return (
      <AuthPage
        initialMode={view}
        notice={state.notice}
        onBack={() => { setView("home"); setState((s) => ({ ...s, notice: "" })); }}
        onAuthed={(user) => setState({ status: "in", user, notice: "" })}
      />
    );
  }

  const about = hash === "#about";
  return (
    <Ctx.Provider value={{ user: state.user, logout }}>
      <Fragment key={state.user.id}>
        {about && <HomePage user={state.user} onOpenApp={openApp} />}
        <div style={{ display: about ? "none" : "contents" }}>{children}</div>
      </Fragment>
    </Ctx.Provider>
  );
}
