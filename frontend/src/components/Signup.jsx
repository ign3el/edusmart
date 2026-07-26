import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { useAuth } from '../context/AuthContext';
import Mascot from './Mascot';
import SocialAuthButtons from './SocialAuthButtons';
import './Auth.css';

// A validation utility function to check form data
const validate = (formData) => {
  const errors = {};
  if (!formData.username) {
    errors.username = 'Username is required';
  } else if (formData.username.length < 3) {
    errors.username = 'Username must be at least 3 characters';
  } else if (formData.username.length > 50) {
    errors.username = 'Username must be 50 characters or less';
  } else if (!/^[a-zA-Z0-9_]+$/.test(formData.username)) {
    errors.username = 'Username can only contain letters, numbers, and underscores';
  }
  
  if (!formData.email) {
    errors.email = 'Email is required';
  } else if (!/\S+@\S+\.\S+/.test(formData.email)) {
    errors.email = 'Email address is invalid';
  }

  if (!formData.password) {
    errors.password = 'Password is required';
  } else if (formData.password.length < 8) {
    errors.password = 'Password must be at least 8 characters';
  } else if (!/[A-Z]/.test(formData.password)) {
    errors.password = 'Password must contain at least one uppercase letter';
  } else if (!/[a-z]/.test(formData.password)) {
    errors.password = 'Password must contain at least one lowercase letter';
  } else if (!/\d/.test(formData.password)) {
    errors.password = 'Password must contain at least one digit';
  }

  if (!formData.passwordConfirm) {
    errors.passwordConfirm = 'Please confirm your password';
  } else if (formData.password !== formData.passwordConfirm) {
    errors.passwordConfirm = 'Passwords do not match';
  }

  return errors;
};

export default function Signup({ onSwitchToLogin, onSuccess }) {
  const [formData, setFormData] = useState({
    username: '',
    email: '',
    password: '',
    passwordConfirm: '',
  });
  
  const [fieldErrors, setFieldErrors] = useState({});
  const [apiError, setApiError] = useState('');
  const [loading, setLoading] = useState(false);
  const [signupSuccess, setSignupSuccess] = useState(false);
  const [passwordStrength, setPasswordStrength] = useState({ score: 0, text: '', color: '' });
  const { signup } = useAuth();

  // Calculate password strength
  useEffect(() => {
    const password = formData.password;
    if (!password) {
      setPasswordStrength({ score: 0, text: '', color: '' });
      return;
    }

    let score = 0;
    if (password.length >= 8) score++;
    if (password.length >= 12) score++;
    if (/[a-z]/.test(password) && /[A-Z]/.test(password)) score++;
    if (/\d/.test(password)) score++;
    if (/[^a-zA-Z0-9]/.test(password)) score++;

    const strengths = [
      { text: 'Very Weak', color: '#ff4444' },
      { text: 'Weak', color: '#ff8800' },
      { text: 'Fair', color: '#ffbb00' },
      { text: 'Good', color: '#88cc00' },
      { text: 'Strong', color: '#00cc44' },
    ];

    setPasswordStrength({ score, ...strengths[Math.min(score, 4)] });
  }, [formData.password]);

  // Re-validate the form whenever the user types (only after they've started)
  useEffect(() => {
    if (Object.values(formData).some(val => val !== '')) {
      setFieldErrors(validate(formData));
    }
  }, [formData]);

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
    setApiError('');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setApiError('');
    
    const validationErrors = validate(formData);
    setFieldErrors(validationErrors);
    
    if (Object.keys(validationErrors).length > 0) {
      return;
    }

    setLoading(true);
    try {
      await signup(formData.username, formData.email, formData.password, formData.passwordConfirm);
      setSignupSuccess(true);
    } catch (err) {
      // Branch on the status code, never on words inside the message. The old
      // version substring-matched detail for 'email' / 'username', which meant:
      // the 409 branch below it was unreachable; "this email or username already
      // exists" was reported as "Email already exists" even when the email was
      // fine; and any unrelated 400 that happened to contain the word 'email'
      // was rewritten into a false duplicate warning. The backend had to word
      // its other errors around this matcher to avoid tripping it.
      const detail = err.response?.data?.detail;
      if (err.response?.status === 409) {
        setApiError('An account with this email or username already exists');
      } else if (Array.isArray(detail)) {
        setApiError(detail.map(e => e.msg).join(', '));
      } else if (typeof detail === 'string' && detail) {
        setApiError(detail);
      } else {
        setApiError('An unexpected error occurred. Please try again');
      }
    } finally {
      setLoading(false);
    }
  };

  if (signupSuccess) {
    return (
      <motion.div
        className="auth-container"
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
      >
        <div className="auth-box success-box">
          <div className="success-icon">✓</div>
          <h2>Account Created!</h2>
          <p className="success-message">
            We've sent a verification link to <strong>{formData.email}</strong>
          </p>
          <p className="success-submessage">
            Please check your inbox and click the link to verify your email address.
          </p>
          <button
            className="auth-button"
            onClick={onSwitchToLogin}
          >
            Go to Login
          </button>
        </div>
      </motion.div>
    );
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
        <h2>Create Account</h2>
        <p className="auth-subtitle">One account, and every lesson you upload becomes a story.</p>
        
        <form onSubmit={handleSubmit} noValidate>
          <div className="input-group">
            <input
              type="text"
              name="username"
              placeholder="Username"
              value={formData.username}
              onChange={handleChange}
              required
              autoComplete="username"
              className={fieldErrors.username ? 'input-error' : ''}
            />
            {fieldErrors.username && <p className="auth-validation-error">{fieldErrors.username}</p>}
          </div>
          
          <div className="input-group">
            <input
              type="email"
              name="email"
              placeholder="Email"
              value={formData.email}
              onChange={handleChange}
              required
              autoComplete="email"
              className={fieldErrors.email ? 'input-error' : ''}
            />
            {fieldErrors.email && <p className="auth-validation-error">{fieldErrors.email}</p>}
          </div>

          <div className="input-group">
            <input
              type="password"
              name="password"
              placeholder="Password (min. 8 characters)"
              value={formData.password}
              onChange={handleChange}
              required
              autoComplete="new-password"
              className={fieldErrors.password ? 'input-error' : ''}
            />
            {formData.password && (
              <div className="password-strength">
                <div className="password-strength-bar">
                  <div
                    className="password-strength-fill"
                    style={{
                      width: `${(passwordStrength.score / 4) * 100}%`,
                      backgroundColor: passwordStrength.color,
                    }}
                  />
                </div>
                <span style={{ color: passwordStrength.color }}>
                  {passwordStrength.text}
                </span>
              </div>
            )}
            {fieldErrors.password && <p className="auth-validation-error">{fieldErrors.password}</p>}
          </div>

          <div className="input-group">
            <input
              type="password"
              name="passwordConfirm"
              placeholder="Confirm Password"
              value={formData.passwordConfirm}
              onChange={handleChange}
              required
              autoComplete="new-password"
              className={fieldErrors.passwordConfirm ? 'input-error' : ''}
            />
            {fieldErrors.passwordConfirm && <p className="auth-validation-error">{fieldErrors.passwordConfirm}</p>}
          </div>

          <div className="password-requirements">
            <p>Password must contain:</p>
            <ul>
              <li className={formData.password.length >= 8 ? 'valid' : ''}>
                At least 8 characters
              </li>
              <li className={/[A-Z]/.test(formData.password) ? 'valid' : ''}>
                One uppercase letter
              </li>
              <li className={/[a-z]/.test(formData.password) ? 'valid' : ''}>
                One lowercase letter
              </li>
              <li className={/\d/.test(formData.password) ? 'valid' : ''}>
                One number
              </li>
            </ul>
          </div>
          
          {apiError && <p className="auth-error">{apiError}</p>}
          
          <button type="submit" disabled={loading} className="auth-button">
            {loading ? 'Creating Account...' : 'Sign Up'}
          </button>
        </form>

        <SocialAuthButtons mode="signup" />
        
        <p className="auth-switch">
          Already have an account?{' '}
          <button type="button" className="link-button" onClick={onSwitchToLogin}>
            Login
          </button>
        </p>
      </div>
    </motion.div>
  );
}