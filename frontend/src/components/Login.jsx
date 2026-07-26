import { useState } from 'react';
import { motion } from 'framer-motion';
import { useAuth } from '../context/AuthContext';
import ForgotPassword from './ForgotPassword';
import Mascot from './Mascot';
import SocialAuthButtons from './SocialAuthButtons';
import './Auth.css';

export default function Login({ onSwitchToSignup, onSuccess }) {
  const [formData, setFormData] = useState({ emailOrUsername: '', password: '' });
  const [apiError, setApiError] = useState('');
  const [loading, setLoading] = useState(false);
  const [showForgotPassword, setShowForgotPassword] = useState(false);
  const [showResendVerification, setShowResendVerification] = useState(false);
  const [resendEmail, setResendEmail] = useState('');
  const [resendLoading, setResendLoading] = useState(false);
  const [resendMessage, setResendMessage] = useState('');
  const { login, resendVerificationEmail } = useAuth();

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
    setApiError('');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setApiError('');

    if (!formData.emailOrUsername || !formData.password) {
      setApiError('Email/Username and password are required');
      return;
    }

    setLoading(true);
    try {
      await login(formData.emailOrUsername, formData.password);
      if (onSuccess) onSuccess();
    } catch (err) {
      if (err.response) {
        const detail = err.response.data?.detail || '';
        switch (err.response.status) {
          case 401:
            setApiError('Incorrect username or password');
            break;
          case 403:
            if (detail.includes('not verified')) {
              setApiError('Email not verified - check your inbox');
              setShowResendVerification(true);
              // Try to extract email if it was used
              if (formData.emailOrUsername.includes('@')) {
                setResendEmail(formData.emailOrUsername);
              }
            } else {
              setApiError(detail);
            }
            break;
          default:
            setApiError(detail || 'An unexpected error occurred');
        }
      } else {
        setApiError('Could not connect to the server. Please try again later');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleResendVerification = async () => {
    if (!resendEmail) {
      setApiError('Please enter your email address');
      return;
    }

    setResendLoading(true);
    setResendMessage('');
    setApiError('');
    
    try {
      await resendVerificationEmail(resendEmail);
      setResendMessage('Verification email sent! Please check your inbox.');
      setShowResendVerification(false);
    } catch (err) {
      if (err.response?.status === 429) {
        const detail = err.response.data?.detail || '';
        const match = detail.match(/(\d+) seconds/);
        if (match) {
          setApiError(`Please wait ${match[1]} seconds before resending email`);
        } else {
          setApiError('Please wait before resending email');
        }
      } else {
        setApiError(err.response?.data?.detail || 'Failed to resend verification email');
      }
    } finally {
      setResendLoading(false);
    }
  };

  if (showForgotPassword) {
    return <ForgotPassword onBack={() => setShowForgotPassword(false)} />;
  }

  return (
    <motion.div
      className="auth-container"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
    >
      <div className="auth-box">
        <div className="auth-mascot">
          <Mascot mood={loading ? 'thinking' : 'idle'} size={84} trackPointer />
        </div>
        <h2>Welcome Back</h2>
        <p className="auth-subtitle">Ollie kept your stories warm. Let's carry on.</p>
        
        <form onSubmit={handleSubmit} noValidate>
          <div className="input-group">
            <input
              type="text"
              name="emailOrUsername"
              placeholder="Email or Username"
              autoComplete="username"
              value={formData.emailOrUsername}
              onChange={handleChange}
              required
            />
          </div>
          
          <div className="input-group">
            <input
              type="password"
              name="password"
              placeholder="Password"
              autoComplete="current-password"
              value={formData.password}
              onChange={handleChange}
              required
            />
          </div>

          <div className="auth-forgot-password">
            <button
              type="button"
              className="link-button"
              onClick={() => setShowForgotPassword(true)}
            >
              Forgot password?
            </button>
          </div>
          
          {showResendVerification && (
            <div className="resend-verification-box">
              <p>Need to resend verification email?</p>
              <div className="resend-input-group">
                <input
                  type="email"
                  id="resend-email"
                  name="resend-email"
                  placeholder="Enter your email"
                  value={resendEmail}
                  onChange={(e) => setResendEmail(e.target.value)}
                />
                <button
                  type="button"
                  onClick={handleResendVerification}
                  disabled={resendLoading}
                  className="resend-button"
                >
                  {resendLoading ? 'Sending...' : 'Resend'}
                </button>
              </div>
            </div>
          )}

          {apiError && <p className="auth-error">{apiError}</p>}
          {resendMessage && <p className="auth-success">{resendMessage}</p>}
          
          <button type="submit" disabled={loading} className="auth-button">
            {loading ? 'Logging In...' : 'Login'}
          </button>
        </form>

        <SocialAuthButtons mode="login" />
        
        <p className="auth-switch">
          Don't have an account?{' '}
          <button type="button" className="link-button" onClick={onSwitchToSignup}>
            Sign Up
          </button>
        </p>
      </div>
    </motion.div>
  );
}