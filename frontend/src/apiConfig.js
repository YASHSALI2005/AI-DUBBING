/** Backend origin (no trailing slash). Set VITE_API_ORIGIN in .env / CI for production builds. */
const raw = import.meta.env.VITE_API_ORIGIN || 'http://localhost:8000';
export const API_ORIGIN = raw.replace(/\/$/, '');
export const API_BASE = `${API_ORIGIN}/api`;
