import React, { useState, useEffect } from 'react';
import { Save, RefreshCw, Eye, EyeOff, Star } from 'lucide-react';
import apiClient from '../services/api';
import './PlanManagement.css';

const PlanManagement = () => {
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [editState, setEditState] = useState({});
  const [savingKey, setSavingKey] = useState(null);

  useEffect(() => {
    fetchPlans();
  }, []);

  const fetchPlans = async () => {
    try {
      setLoading(true);
      const response = await apiClient.get('/api/admin/plans');
      if (response.data.success) {
        setPlans(response.data.plans);
        setError(null);
      }
    } catch (err) {
      setError('Failed to fetch plans: ' + (err.response?.data?.detail || err.message));
    } finally {
      setLoading(false);
    }
  };

  const getEdit = (plan) => editState[plan.tier_key] || {
    display_name: plan.display_name,
    price_display: plan.price_display,
    stripe_price_id: plan.stripe_price_id || '',
    credits_included: plan.credits_included,
    sort_order: plan.sort_order,
    description: plan.description || '',
    features: plan.features || '',
  };

  const setEdit = (plan, patch) => {
    setEditState((prev) => ({ ...prev, [plan.tier_key]: { ...(prev[plan.tier_key] || getEdit(plan)), ...patch } }));
  };

  const handleSave = async (plan) => {
    const edit = getEdit(plan);
    setSavingKey(plan.tier_key);
    setError(null);
    try {
      await apiClient.put(`/api/admin/plans/${plan.tier_key}`, {
        display_name: edit.display_name,
        price_display: edit.price_display,
        stripe_price_id: edit.stripe_price_id || null,
        credits_included: Number(edit.credits_included),
        sort_order: Number(edit.sort_order),
        description: edit.description,
        features: edit.features,
      });
      await fetchPlans();
    } catch (err) {
      setError('Save failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setSavingKey(null);
    }
  };

  const handleToggleActive = async (plan) => {
    setSavingKey(plan.tier_key);
    try {
      await apiClient.put(`/api/admin/plans/${plan.tier_key}`, { is_active: !plan.is_active });
      await fetchPlans();
    } catch (err) {
      setError('Update failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setSavingKey(null);
    }
  };

  const handleToggleRecommended = async (plan) => {
    setSavingKey(plan.tier_key);
    try {
      await apiClient.put(`/api/admin/plans/${plan.tier_key}`, { is_recommended: !plan.is_recommended });
      await fetchPlans();
    } catch (err) {
      setError('Update failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setSavingKey(null);
    }
  };

  if (loading) return <div className="loading">Loading plans...</div>;

  return (
    <div className="plan-management">
      <div className="management-header">
        <h2>Manage Plans</h2>
        <p className="plan-management-note">
          Changes here take effect on the very next request - no rebuild or restart needed.
        </p>
        <button className="refresh-btn" onClick={fetchPlans}><RefreshCw size={14} /> Refresh</button>
      </div>

      {error && <div className="error-message">{error}</div>}

      <div className="plans-table">
        {plans.map((plan) => {
          const edit = getEdit(plan);
          return (
            <div key={plan.tier_key} className={`plan-row ${!plan.is_active ? 'inactive' : ''}`}>
              <div className="plan-row-key">
                {plan.tier_key}
                {Boolean(plan.is_recommended) && <span className="recommended-tag"><Star size={11} /> Recommended</span>}
              </div>
              <label className="field-label">
                <span className="field-label-text">Display Name</span>
                <input value={edit.display_name} onChange={(e) => setEdit(plan, { display_name: e.target.value })} />
              </label>
              <label className="field-label">
                <span className="field-label-text">Price Display</span>
                <input value={edit.price_display} onChange={(e) => setEdit(plan, { price_display: e.target.value })} />
              </label>
              <label className="field-label">
                <span className="field-label-text">Credits</span>
                <input type="number" value={edit.credits_included} onChange={(e) => setEdit(plan, { credits_included: e.target.value })} />
              </label>
              <label className="field-label">
                <span className="field-label-text">Stripe Price ID</span>
                <input placeholder="price_..." value={edit.stripe_price_id} onChange={(e) => setEdit(plan, { stripe_price_id: e.target.value })} />
              </label>
              <label className="field-label">
                <span className="field-label-text">Sort Order</span>
                <input type="number" value={edit.sort_order} onChange={(e) => setEdit(plan, { sort_order: e.target.value })} />
              </label>
              <label className="field-label plan-row-description">
                <span className="field-label-text">Description</span>
                <input
                  placeholder="One-line tagline shown on the pricing page"
                  value={edit.description}
                  onChange={(e) => setEdit(plan, { description: e.target.value })}
                />
              </label>
              <label className="field-label plan-row-features">
                <span className="field-label-text">Features (one per line)</span>
                <textarea
                  rows={4}
                  placeholder={'15 stories/month\nEmail support\nCancel anytime'}
                  value={edit.features}
                  onChange={(e) => setEdit(plan, { features: e.target.value })}
                />
              </label>
              <div className="plan-row-actions">
                <button className="save-btn" onClick={() => handleSave(plan)} disabled={savingKey === plan.tier_key}>
                  <Save size={14} /> Save
                </button>
                <button className="toggle-btn" onClick={() => handleToggleActive(plan)} disabled={savingKey === plan.tier_key}>
                  {plan.is_active ? <><EyeOff size={14} /> Deactivate</> : <><Eye size={14} /> Activate</>}
                </button>
                <button
                  className={`toggle-btn ${plan.is_recommended ? 'active' : ''}`}
                  onClick={() => handleToggleRecommended(plan)}
                  disabled={savingKey === plan.tier_key}
                >
                  <Star size={14} /> {plan.is_recommended ? 'Recommended' : 'Mark Recommended'}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default PlanManagement;
