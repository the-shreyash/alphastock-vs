import axios from "axios";

const API_URL = process.env.REACT_APP_BACKEND_URL;

const api = axios.create({
  baseURL: `${API_URL}/api`,
  headers: { "Content-Type": "application/json" },
});

// Add token from localStorage as fallback
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Track refresh state to prevent infinite loops
let isRefreshing = false;
let refreshFailed = false;

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const originalRequest = error.config;
    const url = originalRequest?.url || "";

    // Never retry auth endpoints themselves
    if (url.includes("/auth/me") || url.includes("/auth/refresh") || url.includes("/auth/login") || url.includes("/auth/register") || url.includes("/auth/google")) {
      return Promise.reject(error);
    }

    if (error.response?.status === 401 && !originalRequest._retry && !refreshFailed) {
      originalRequest._retry = true;

      if (!isRefreshing) {
        isRefreshing = true;
        try {
          await api.post("/auth/refresh");
          isRefreshing = false;
          return api(originalRequest);
        } catch {
          isRefreshing = false;
          refreshFailed = true;
          localStorage.removeItem("token");
          // Don't redirect here - let AuthContext handle it
          return Promise.reject(error);
        }
      }
    }
    return Promise.reject(error);
  }
);

// Reset refreshFailed on successful login
export function resetRefreshState() {
  refreshFailed = false;
  isRefreshing = false;
}

export default api;
