import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { ChevronLeft, Pencil, Check, X, Lock, LogOut, Sparkles, CreditCard, ExternalLink } from 'lucide-react';
import apiClient, { getBillingBalance, createBillingPortalSession } from '../services/api';
import './UserProfile.css';

function UserProfile({ user, onBack, onLogout, onViewPlans }) {
  const [isEditing, setIsEditing] = useState(false);
  const [username, setUsername] = useState(user?.email?.split('@')[0] || '');
  const [isChangingPassword, setIsChangingPassword] = useState(false);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [storageUsage, setStorageUsage] = useState({ used: 0, total: 0, percentage: 0 });
  const [savedStoriesCount, setSavedStoriesCount] = useState(0);
  const [message, setMessage] = useState({ text: '', type: '' });
  const [loading, setLoading] = useState(false);
  const [billing, setBilling] = useState(null);
  const [billingLoading, setBillingLoading] = useState(true);
  const [portalLoading, setPortalLoading] = useState(false);

  useEffect(() => {
    calculateStorageUsage();
    countSavedStories();
    loadBilling();
  }, []);

  const loadBilling = async () => {
    setBillingLoading(true);
    try {
      const data = await getBillingBalance();
      setBilling(data);
    } catch (error) {
      console.error('Failed to load billing info:', error);
    } finally {
      setBillingLoading(false);
    }
  };

  const handleManageSubscription = async () => {
    setPortalLoading(true);
    try {
      const { portal_url } = await createBillingPortalSession();
      window.location.href = portal_url;
    } catch (error) {
      setMessage({
        text: error.response?.data?.detail || 'Could not open the billing portal.',
        type: 'error'
      });
      setPortalLoading(false);
    }
  };

  const calculateStorageUsage = async () => {
    try {
      if ('storage' in navigator && 'estimate' in navigator.storage) {
        const estimate = await navigator.storage.estimate();
        const used = estimate.usage || 0;
        const total = estimate.quota || 0;
        const percentage = total > 0 ? ((used / total) * 100).toFixed(2) : 0;

        setStorageUsage({
          used: (used / (1024 * 1024)).toFixed(2), // Convert to MB
          total: (total / (1024 * 1024)).toFixed(2),
          percentage
        });
      }
    } catch (error) {
      console.error('Storage estimation error:', error);
    }
  };

  const countSavedStories = () => {
    try {
      const request = indexedDB.open('EduSmartOfflineDB', 1);
      request.onsuccess = (event) => {
        const db = event.target.result;
        if (db.objectStoreNames.contains('stories')) {
          const transaction = db.transaction(['stories'], 'readonly');
          const store = transaction.objectStore('stories');
          const countRequest = store.count();

          countRequest.onsuccess = () => {
            setSavedStoriesCount(countRequest.result);
          };
        }
        db.close();
      };
    } catch (error) {
      console.error('Error counting stories:', error);
    }
  };

  const handleUpdateUsername = async () => {
    if (!username.trim()) {
      setMessage({ text: 'Username cannot be empty', type: 'error' });
      return;
    }

    setLoading(true);
    try {
      await apiClient.put('/api/auth/update-username', { username });
      setMessage({ text: 'Username updated successfully!', type: 'success' });
      setIsEditing(false);
      setTimeout(() => setMessage({ text: '', type: '' }), 3000);
    } catch (error) {
      setMessage({
        text: error.response?.data?.detail || 'Failed to update username',
        type: 'error'
      });
    } finally {
      setLoading(false);
    }
  };

  const handleChangePassword = async (e) => {
    e.preventDefault();

    if (newPassword !== confirmPassword) {
      setMessage({ text: 'New passwords do not match', type: 'error' });
      return;
    }

    if (newPassword.length < 6) {
      setMessage({ text: 'Password must be at least 6 characters', type: 'error' });
      return;
    }

    setLoading(true);
    try {
      await apiClient.post('/api/auth/change-password', {
        current_password: currentPassword,
        new_password: newPassword
      });

      setMessage({ text: 'Password changed successfully!', type: 'success' });
      setIsChangingPassword(false);
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setTimeout(() => setMessage({ text: '', type: '' }), 3000);
    } catch (error) {
      setMessage({
        text: error.response?.data?.detail || 'Failed to change password',
        type: 'error'
      });
    } finally {
      setLoading(false);
    }
  };

  const formatBytes = (mb) => {
    if (mb < 1) return `${(mb * 1024).toFixed(2)} KB`;
    if (mb > 1024) return `${(mb / 1024).toFixed(2)} GB`;
    return `${mb} MB`;
  };

  return (
    <div className="user-profile">
      <div className="profile-header">
        <button onClick={onBack} className="back-button">
          <ChevronLeft size={16} /> Back
        </button>
        <h1>User Profile</h1>
      </div>

      {message.text && (
        <motion.div
          className={`profile-message ${message.type}`}
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
        >
          {message.text}
        </motion.div>
      )}

      <div className="profile-content">
        {/* Billing Card */}
        <motion.div
          className="profile-card"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.05 }}
        >
          <h2><CreditCard size={18} aria-hidden="true" /> Billing</h2>

          {billingLoading ? (
            <p className="field-hint">Loading...</p>
          ) : billing ? (
            <>
              <div className="storage-stats">
                <div className="stat-item">
                  <span className="stat-label">Story Credits</span>
                  <span className="stat-value">{billing.unlimited ? 'Unlimited' : billing.credits_balance}</span>
                </div>
                <div className="stat-item">
                  <span className="stat-label">Plan</span>
                  <span className="stat-value" style={{ textTransform: 'capitalize' }}>{billing.unlimited ? 'Admin' : billing.subscription_tier}</span>
                </div>
              </div>
              <div className="password-actions" style={{ marginTop: '1rem' }}>
                <button onClick={onViewPlans} className="save-button">
                  <Sparkles size={14} /> View Plans
                </button>
                {billing.subscription_tier !== 'free' && (
                  <button onClick={handleManageSubscription} disabled={portalLoading} className="cancel-button">
                    <ExternalLink size={14} /> {portalLoading ? 'Opening...' : 'Manage Subscription'}
                  </button>
                )}
              </div>
            </>
          ) : (
            <p className="field-hint">Could not load billing info.</p>
          )}
        </motion.div>

        {/* User Details Card */}
        <motion.div
          className="profile-card"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
        >
          <h2>Account Details</h2>

          <div className="profile-field">
            <label>User ID</label>
            <div className="field-group">
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={!isEditing}
                className={isEditing ? 'editing' : ''}
              />
              {!isEditing ? (
                <button onClick={() => setIsEditing(true)} className="edit-button">
                  <Pencil size={14} /> Edit
                </button>
              ) : (
                <div className="edit-actions">
                  <button onClick={handleUpdateUsername} disabled={loading} className="save-button">
                    <Check size={14} /> Save
                  </button>
                  <button onClick={() => {
                    setIsEditing(false);
                    setUsername(user?.email?.split('@')[0] || '');
                  }} className="cancel-button">
                    <X size={14} /> Cancel
                  </button>
                </div>
              )}
            </div>
          </div>

          <div className="profile-field">
            <label>Email</label>
            <input
              type="email"
              value={user?.email || ''}
              disabled
              className="readonly"
            />
            <span className="field-hint">Email cannot be changed</span>
          </div>
        </motion.div>

        {/* Change Password Card */}
        <motion.div
          className="profile-card"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <h2>Security</h2>

          {!isChangingPassword ? (
            <button onClick={() => setIsChangingPassword(true)} className="change-password-button">
              <Lock size={16} /> Change Password
            </button>
          ) : (
            <form onSubmit={handleChangePassword} className="password-form">
              <div className="profile-field">
                <label>Current Password</label>
                <input
                  type="password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  required
                  placeholder="Enter current password"
                />
              </div>

              <div className="profile-field">
                <label>New Password</label>
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  required
                  placeholder="Enter new password"
                  minLength={6}
                />
              </div>

              <div className="profile-field">
                <label>Confirm New Password</label>
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                  placeholder="Confirm new password"
                  minLength={6}
                />
              </div>

              <div className="password-actions">
                <button type="submit" disabled={loading} className="save-button">
                  {loading ? 'Changing...' : 'Change Password'}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setIsChangingPassword(false);
                    setCurrentPassword('');
                    setNewPassword('');
                    setConfirmPassword('');
                  }}
                  className="cancel-button"
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </motion.div>

        {/* Storage Usage Card */}
        <motion.div
          className="profile-card"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
        >
          <h2>Storage Usage</h2>

          <div className="storage-stats">
            <div className="stat-item">
              <span className="stat-label">IndexedDB Usage</span>
              <span className="stat-value">{formatBytes(storageUsage.used)}</span>
            </div>

            <div className="stat-item">
              <span className="stat-label">Total Available</span>
              <span className="stat-value">{formatBytes(storageUsage.total)}</span>
            </div>

            <div className="stat-item">
              <span className="stat-label">Saved Stories</span>
              <span className="stat-value">{savedStoriesCount}</span>
            </div>
          </div>

          <div className="storage-bar">
            <div
              className="storage-fill"
              style={{ width: `${Math.min(storageUsage.percentage, 100)}%` }}
            />
          </div>
          <p className="storage-percentage">{storageUsage.percentage}% used</p>
        </motion.div>

        {/* Logout Card */}
        <motion.div
          className="profile-card danger-card"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
        >
          <h2>Account Actions</h2>
          <button onClick={onLogout} className="logout-button">
            <LogOut size={16} /> Logout
          </button>
        </motion.div>
      </div>
    </div>
  );
}

export default UserProfile;
