import React, { useEffect, useRef, useState } from 'react';
import { Settings, LogOut, User } from 'lucide-react';

export default function ProfileMenu({ user, onOpenSettings, onLogout }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      if (!ref.current) return;
      if (!ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const initials = (user?.name || user?.address || '?')
    .split(/\s+/)
    .map((s) => s[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase();

  const isAdmin = user?.role === 'admin';

  return (
    <div className="profile-menu" ref={ref}>
      <button
        className="profile-avatar"
        onClick={() => setOpen((v) => !v)}
        aria-label="Open profile menu"
        title={user?.name || user?.address}
      >
        {initials || <User size={16} />}
      </button>
      {open && (
        <div className="profile-dropdown">
          <div className="profile-info">
            <div className="profile-name">{user?.name || '—'}</div>
            <div className="profile-meta">{user?.address}</div>
            <div className="profile-role">{user?.role}</div>
          </div>
          <div className="profile-divider" />
          {isAdmin && (
            <button
              className="profile-item"
              onClick={() => { setOpen(false); onOpenSettings(); }}
            >
              <Settings size={16} /> Settings
            </button>
          )}
          <button
            className="profile-item profile-item-danger"
            onClick={() => { setOpen(false); onLogout(); }}
          >
            <LogOut size={16} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
