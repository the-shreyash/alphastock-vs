import api from "./api";

// Google OAuth initiation.
//
// The authorization URL and the CSRF `state` are generated server-side: the
// backend plants `state` in a short-lived httponly cookie and returns the URL
// to redirect to. We never build the Google URL on the client, so the state is
// bound to the browser and cannot be forged. `withCredentials` ensures the
// state cookie is stored on this request and replayed on the callback exchange.
export async function startGoogleLogin() {
  const redirectUri = window.location.origin + "/auth/google/callback";
  const { data } = await api.get("/auth/google/login-url", {
    params: { redirect_uri: redirectUri },
    withCredentials: true,
  });
  if (!data?.url) {
    throw new Error("Google sign-in is unavailable right now.");
  }
  window.location.href = data.url;
}
