import { useState, type FormEvent } from 'react';
import { Eye, EyeOff, LockKeyhole, ShieldCheck, TrainFront, User } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import apiClient from '../services/apiClient';
import toast from 'react-hot-toast';
import logo from '../assets/pune-metro-logo.png';

const LoginPage = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const { login } = useAuth();

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsLoading(true);
    try {
      const formData = new URLSearchParams({ username, password });
      const response = await apiClient.post('/auth/login', formData, { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
      if (response.data.access_token) { toast.success('Welcome to the command centre'); login(response.data.access_token); }
    } catch (error) {
      console.error(error);
    } finally {
      setIsLoading(false);
    }
  };

  return <main className="login-shell">
    <section className="login-story">
      <div className="login-brand"><span><img src={logo} alt="Pune Metro" /></span><div><strong>Pune Metro</strong><small>Passenger Care AI</small></div></div>
      <div className="login-message"><p className="eyebrow light"><span className="live-dot" /> Service intelligence</p><h1>Every passenger voice,<br/><em>handled with care.</em></h1><p>Monitor conversations, understand passenger intent and resolve service requests from one secure workspace.</p></div>
      <div className="login-features"><div><TrainFront/><span><strong>Chat + calling</strong><small>One unified timeline</small></span></div><div><ShieldCheck/><span><strong>Secure operations</strong><small>Administrator access only</small></span></div></div>
    </section>
    <section className="login-form-side"><div className="login-card">
      <div className="mobile-logo"><img src={logo} alt="Pune Metro" /></div>
      <p className="eyebrow">Admin workspace</p><h2>Welcome back</h2><p className="login-subtitle">Sign in to manage passenger support.</p>
      <form onSubmit={handleSubmit}>
        <label>Username<div className="input-shell"><User/><input value={username} onChange={e => setUsername(e.target.value)} placeholder="Enter username" required disabled={isLoading}/></div></label>
        <label>Password<div className="input-shell"><LockKeyhole/><input type={showPassword ? 'text' : 'password'} value={password} onChange={e => setPassword(e.target.value)} placeholder="Enter password" required disabled={isLoading}/><button type="button" onClick={() => setShowPassword(value => !value)} aria-label="Toggle password visibility">{showPassword ? <EyeOff/> : <Eye/>}</button></div></label>
        <button className="login-submit" disabled={isLoading}>{isLoading ? 'Signing in…' : 'Sign in securely'}</button>
      </form>
      <div className="secure-note"><ShieldCheck/> Protected administrator portal</div>
    </div></section>
  </main>;
};

export default LoginPage;
