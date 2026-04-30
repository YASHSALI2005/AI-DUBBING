import React, { useEffect, useState } from 'react';
import axios from 'axios';
import {
  Users as UsersIcon,
  Shield,
  Plus,
  X,
  Eye,
  EyeOff,
  ArrowLeft,
  CheckCircle,
  ShieldCheck,
  Wrench,
} from 'lucide-react';

const ROLE_OPTIONS = [
  { value: 'admin',    label: 'Admin' },
  { value: 'engineer', label: 'Engineer' },
];

export default function Settings({ apiBase, token, currentUser, onBack }) {
  const [tab, setTab]               = useState('users');
  const [users, setUsers]           = useState([]);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState('');
  const [showAddModal, setShowAddModal] = useState(false);

  const authHeader = { Authorization: `Bearer ${token}` };

  const loadUsers = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await axios.get(`${apiBase}/users`, { headers: authHeader });
      setUsers(res.data.users || []);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load users');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (tab === 'users') loadUsers(); /* eslint-disable-next-line */ }, [tab]);

  const handleUserCreated = (newUser) => {
    setUsers((prev) => [...prev, newUser]);
    setShowAddModal(false);
  };

  return (
    <div className="settings-container">
      <div className="settings-header">
        <button className="btn-secondary" onClick={onBack}>
          <ArrowLeft size={16} /> Back
        </button>
        <h2 className="settings-title">Settings</h2>
      </div>

      <div className="settings-tabs">
        <button
          className={`settings-tab ${tab === 'users' ? 'active' : ''}`}
          onClick={() => setTab('users')}
        >
          <UsersIcon size={16} /> Users
        </button>
        <button
          className={`settings-tab ${tab === 'roles' ? 'active' : ''}`}
          onClick={() => setTab('roles')}
        >
          <Shield size={16} /> Roles
        </button>
      </div>

      {tab === 'users' && (
        <div className="settings-panel">
          <div className="settings-panel-head">
            <span className="settings-panel-title">All users ({users.length})</span>
            <button className="btn-primary" onClick={() => setShowAddModal(true)}>
              <Plus size={16} /> Add user
            </button>
          </div>

          {error && <div className="error-banner">{error}</div>}

          <div className="users-table">
            <div className="users-table-head">
              <span>Name</span>
              <span>Email</span>
              <span>Role</span>
              <span>Created</span>
            </div>
            {loading && <div className="users-empty">Loading…</div>}
            {!loading && users.length === 0 && !error && (
              <div className="users-empty">No users yet.</div>
            )}
            {!loading && users.map((u) => (
              <div key={u.id} className="users-row">
                <span>
                  {u.name}
                  {currentUser?.id === u.id && <span className="badge-you"> you</span>}
                </span>
                <span className="users-meta">{u.email}</span>
                <span className={`role-pill role-${u.role}`}>{u.role}</span>
                <span className="users-meta">
                  {u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === 'roles' && (
        <div className="settings-panel">
          <div className="role-grid">
            <div className="role-card">
              <div className="role-card-head">
                <ShieldCheck size={18} />
                <span className="role-card-title">Admin</span>
              </div>
              <ul className="role-card-list">
                <li><CheckCircle size={14} /> Full access to all dubbing pipeline stages</li>
                <li><CheckCircle size={14} /> Can view and manage Settings</li>
                <li><CheckCircle size={14} /> Can add and view users</li>
                <li><CheckCircle size={14} /> Can assign roles</li>
              </ul>
            </div>
            <div className="role-card">
              <div className="role-card-head">
                <Wrench size={18} />
                <span className="role-card-title">Engineer</span>
              </div>
              <ul className="role-card-list">
                <li><CheckCircle size={14} /> Full access to all dubbing pipeline stages</li>
                <li className="role-card-deny">✕ Cannot access or view Settings</li>
                <li className="role-card-deny">✕ Cannot manage users or roles</li>
              </ul>
            </div>
          </div>
        </div>
      )}

      {showAddModal && (
        <AddUserModal
          apiBase={apiBase}
          token={token}
          onClose={() => setShowAddModal(false)}
          onCreated={handleUserCreated}
        />
      )}
    </div>
  );
}

function AddUserModal({ apiBase, token, onClose, onCreated }) {
  const [name, setName]         = useState('');
  const [email, setEmail]       = useState('');
  const [password, setPassword] = useState('');
  const [showPwd, setShowPwd]   = useState(false);
  const [role, setRole]         = useState('engineer');
  const [busy, setBusy]         = useState(false);
  const [error, setError]       = useState('');

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const res = await axios.post(
        `${apiBase}/users`,
        { name, email: email.trim().toLowerCase(), password, role },
        { headers: { Authorization: `Bearer ${token}` } },
      );
      onCreated(res.data);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to create user');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span>Add user</span>
          <button className="btn-icon ghost" onClick={onClose}><X size={16} /></button>
        </div>
        <form onSubmit={submit} className="modal-form">
          <div className="form-group">
            <label htmlFor="u-name">Name</label>
            <input id="u-name" value={name} onChange={(e) => setName(e.target.value)} required autoFocus />
          </div>
          <div className="form-group">
            <label htmlFor="u-email">Email</label>
            <input
              id="u-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="user@example.com (used for login)"
              autoComplete="email"
              required
            />
          </div>
          <div className="form-group">
            <label htmlFor="u-pwd">Password</label>
            <div className="password-field">
              <input
                id="u-pwd"
                type={showPwd ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              <button
                type="button"
                className="password-toggle"
                onClick={() => setShowPwd((v) => !v)}
                title={showPwd ? 'Hide password' : 'Show password'}
              >
                {showPwd ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>
          </div>
          <div className="form-group">
            <label htmlFor="u-role">Role</label>
            <select id="u-role" value={role} onChange={(e) => setRole(e.target.value)}>
              {ROLE_OPTIONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
          </div>

          {error && <div className="error-banner">{error}</div>}

          <div className="modal-actions">
            <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn-primary" disabled={busy}>
              <Plus size={16} /> {busy ? 'Creating…' : 'Create user'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
