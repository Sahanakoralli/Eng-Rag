import { createContext, Fragment, useCallback, useContext, useEffect, useState } from "react";
import AuthPage from "./AuthPage.jsx";
import * as auth from "./auth.js";

const Ctx = createContext({ user: null, logout: () => {} });
export const useAuth = () => useContext(Ctx);

// Shows the login/signup page until the user is logged in, then renders the app.
// The app is remounted for every new user, so one person's chats are never left on screen for the next.
export default function AuthGate({ children }) {
  const [state, setState] = useState({ status: auth.getToken() ? "loading" : "out", user: null, notice: "" });

  useEffect(() => {
    auth.installFetchAuth();
    let alive = true;
    if (auth.getToken()) {
      auth.fetchMe()
        .then((user) => alive && setState({ status: "in", user, notice: "" }))
        .catch(() => alive && setState({ status: "out", user: null, notice: "" }));
    }
    const expired = () => setState({ status: "out", user: null, notice: "Your session expired. Log in again." });
    window.addEventListener("engrag:logout", expired);
    return () => { alive = false; window.removeEventListener("engrag:logout", expired); };
  }, []);

  const logout = useCallback(async () => {
    await auth.logout();
    setState({ status: "out", user: null, notice: "" });
  }, []);

  if (state.status === "loading") return <div className="auth-loading">Loading…</div>;
  if (state.status === "out") return <AuthPage notice={state.notice} onAuthed={(user) => setState({ status: "in", user, notice: "" })} />;
  return (
    <Ctx.Provider value={{ user: state.user, logout }}>
      <Fragment key={state.user.id}>{children}</Fragment>
    </Ctx.Provider>
  );
}
