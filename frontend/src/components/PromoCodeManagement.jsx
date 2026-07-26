import React, { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Plus, RefreshCw, Eye, EyeOff, Tag } from 'lucide-react';
import apiClient from '../services/api';
import './PromoCodeManagement.css';

const emptyCreateState = {
  code: '',
  discount_type: 'free_credits',
  discount_value: 5,
  stripe_coupon_id: '',
  max_redemptions: '',
  max_redemptions_per_user: 1,
  expires_at: '',
};

function formatDate(dateString) {
  if (!dateString) return 'N/A';
  return new Date(dateString).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

const PromoCodeManagement = () => {
  const [codes, setCodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState(emptyCreateState);
  const [creating, setCreating] = useState(false);
  const [savingCode, setSavingCode] = useState(null);

  useEffect(() => {
    fetchCodes();
  }, []);

  const fetchCodes = async () => {
    try {
      setLoading(true);
      const response = await apiClient.get('/api/admin/promo-codes');
      if (response.data.success) {
        setCodes(response.data.promo_codes);
        setError(null);
      }
    } catch (err) {
      setError('Failed to fetch promo codes: ' + (err.response?.data?.detail || err.message));
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    if (!createForm.code.trim()) {
      setError('A code is required.');
      return;
    }
    setCreating(true);
    setError(null);
    try {
      await apiClient.post('/api/admin/promo-codes', {
        code: createForm.code.trim(),
        discount_type: createForm.discount_type,
        discount_value: Number(createForm.discount_value),
        stripe_coupon_id: createForm.stripe_coupon_id.trim() || null,
        max_redemptions: createForm.max_redemptions ? Number(createForm.max_redemptions) : null,
        max_redemptions_per_user: Number(createForm.max_redemptions_per_user),
        expires_at: createForm.expires_at || null,
      });
      setShowCreate(false);
      setCreateForm(emptyCreateState);
      await fetchCodes();
    } catch (err) {
      setError('Create failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setCreating(false);
    }
  };

  const handleToggleActive = async (promo) => {
    setSavingCode(promo.code);
    try {
      await apiClient.put(`/api/admin/promo-codes/${promo.code}`, { is_active: !promo.is_active });
      await fetchCodes();
    } catch (err) {
      setError('Update failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setSavingCode(null);
    }
  };

  if (loading) return <div className="loading">Loading promo codes...</div>;

  return (
    <div className="promo-management">
      <div className="management-header">
        <h2>Promo Codes</h2>
        <div className="header-actions">
          <button className="add-user-btn" onClick={() => setShowCreate(true)}><Plus size={16} /> New Code</button>
          <button className="refresh-btn" onClick={fetchCodes}><RefreshCw size={14} /> Refresh</button>
        </div>
      </div>

      {error && <div className="error-message">{error}</div>}

      <div className="promo-table">
        {codes.length === 0 && <div className="no-results">No promo codes yet.</div>}
        {codes.map((promo) => (
          <div key={promo.code} className={`promo-row ${!promo.is_active ? 'inactive' : ''}`}>
            <div className="promo-code"><Tag size={13} /> {promo.code}</div>
            <div className="promo-detail">
              {promo.discount_type === 'percent_off' ? `${promo.discount_value}% off` : `${promo.discount_value} free stories`}
            </div>
            <div className="promo-detail">
              {promo.times_redeemed}{promo.max_redemptions ? ` / ${promo.max_redemptions}` : ''} redeemed
            </div>
            <div className="promo-detail">Expires: {formatDate(promo.expires_at)}</div>
            <button className="toggle-btn" onClick={() => handleToggleActive(promo)} disabled={savingCode === promo.code}>
              {promo.is_active ? <><EyeOff size={14} /> Deactivate</> : <><Eye size={14} /> Activate</>}
            </button>
          </div>
        ))}
      </div>

      {showCreate && createPortal(
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h3><Plus size={18} /> New Promo Code</h3>
            <label className="field-label">
              <span className="field-label-text">Code</span>
              <input placeholder="SUMMER20" value={createForm.code} onChange={(e) => setCreateForm(f => ({ ...f, code: e.target.value.toUpperCase() }))} autoFocus />
            </label>
            <label className="field-label">
              <span className="field-label-text">Type</span>
              <select value={createForm.discount_type} onChange={(e) => setCreateForm(f => ({ ...f, discount_type: e.target.value }))}>
                <option value="free_credits">Free credits (no card needed - good for testers)</option>
                <option value="percent_off">Percent off at checkout</option>
              </select>
            </label>
            <label className="field-label">
              <span className="field-label-text">{createForm.discount_type === 'percent_off' ? 'Discount % (1-100)' : 'Number of free stories'}</span>
              <input type="number" value={createForm.discount_value} onChange={(e) => setCreateForm(f => ({ ...f, discount_value: e.target.value }))} />
            </label>
            {createForm.discount_type === 'percent_off' && (
              <label className="field-label">
                <span className="field-label-text">Stripe Coupon ID</span>
                <input placeholder="coupon from Stripe dashboard" value={createForm.stripe_coupon_id} onChange={(e) => setCreateForm(f => ({ ...f, stripe_coupon_id: e.target.value }))} />
              </label>
            )}
            <label className="field-label">
              <span className="field-label-text">Max Total Redemptions (blank = unlimited)</span>
              <input type="number" value={createForm.max_redemptions} onChange={(e) => setCreateForm(f => ({ ...f, max_redemptions: e.target.value }))} />
            </label>
            <label className="field-label">
              <span className="field-label-text">Max Redemptions Per User</span>
              <input type="number" value={createForm.max_redemptions_per_user} onChange={(e) => setCreateForm(f => ({ ...f, max_redemptions_per_user: e.target.value }))} />
            </label>
            <label className="field-label">
              <span className="field-label-text">Expires (blank = never)</span>
              <input type="date" value={createForm.expires_at} onChange={(e) => setCreateForm(f => ({ ...f, expires_at: e.target.value }))} />
            </label>
            <div className="modal-buttons">
              <button className="cancel-btn" onClick={() => { setShowCreate(false); setCreateForm(emptyCreateState); }}>Cancel</button>
              <button className="confirm-btn" onClick={handleCreate} disabled={creating}>{creating ? 'Creating...' : 'Create Code'}</button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
};

export default PromoCodeManagement;
