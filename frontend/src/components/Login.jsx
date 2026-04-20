import React, { useState } from 'react';
import { LogIn } from 'lucide-react';

const Login = ({ onLogin }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    // Fixed dummy credentials
    if (username === 'admin' && password === 'Vrfilms@2026') {
      onLogin();
    } else {
      setError('Invalid username or password');
    }
  };

  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh', width: '100%' }}>
      <div className="glass-card" style={{ maxWidth: '400px', width: '100%' }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <h2 style={{ 
            fontSize: '2rem', 
            fontWeight: 800, 
            marginBottom: '0.5rem', 
            background: 'linear-gradient(to right, #ffffff, #a1a1aa)', 
            WebkitBackgroundClip: 'text', 
            color: 'transparent' 
          }}>
            Welcome 
          </h2>
          <p style={{ color: 'var(--text-muted)' }}>Sign in to access VR FILMS Dubbing Tool</p>
        </div>
        
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label htmlFor="username">Username</label>
            <input 
              type="text" 
              id="username" 
              value={username} 
              onChange={(e) => setUsername(e.target.value)} 
              placeholder="Enter username"
              required 
            />
          </div>
          <div className="form-group">
            <label htmlFor="password">Password</label>
            <input 
              type="password" 
              id="password" 
              value={password} 
              onChange={(e) => setPassword(e.target.value)} 
              placeholder="Enter password"
              required 
            />
          </div>
          
          {error && <p style={{ color: '#ef4444', marginBottom: '1rem', fontSize: '0.9rem', textAlign: 'center' }}>{error}</p>}
          
          <button type="submit" className="btn" style={{ width: '100%', justifyContent: 'center' }}>
            <LogIn size={20} />
            Sign In
          </button>
        </form>
      </div>
    </div>
  );
};

export default Login;
