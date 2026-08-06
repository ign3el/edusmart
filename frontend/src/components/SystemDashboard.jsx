import React, { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import {
  RefreshCw, Server, GitBranch, Activity, HardDrive, DollarSign, ShieldAlert, Image, Mic, Sparkles, Gauge,
} from 'lucide-react';
import {
  getAdminSystemConfig, getAdminDeployStatus, getAdminBackupStatus, getAdminSystemHealth, getAdminRateLimits,
  getAdminApiUsage,
} from '../services/api';
import './SystemDashboard.css';

function formatDate(iso) {
  if (!iso) return 'never';
  return new Date(iso).toLocaleString();
}

const StatCard = ({ icon, title, children }) => (
  <motion.div className="sys-card" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
    <div className="sys-card-header">{icon}<h3>{title}</h3></div>
    <div className="sys-card-body">{children}</div>
  </motion.div>
);

const SystemDashboard = () => {
  const [config, setConfig] = useState(null);
  const [deployStatus, setDeployStatus] = useState(null);
  const [backupStatus, setBackupStatus] = useState(null);
  const [health, setHealth] = useState(null);
  const [rateLimits, setRateLimits] = useState(null);
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // allSettled, not all. These six endpoints are independent views of the
  // system, but Promise.all made them a single unit: one of them 500ing (or
  // 401ing) threw, the catch replaced the ENTIRE dashboard with an error
  // string, and every other card - including the ones that loaded fine - went
  // with it. That is exactly backwards for a diagnostics page, whose job is to
  // still tell you something when part of the system is unwell. Now each card
  // renders from whatever its own call returned, and failures are listed.
  const fetchAll = async () => {
    setLoading(true);
    setError(null);
    const calls = [
      ['config', getAdminSystemConfig, setConfig],
      ['deploy status', getAdminDeployStatus, setDeployStatus],
      ['backup status', getAdminBackupStatus, setBackupStatus],
      ['health', getAdminSystemHealth, setHealth],
      ['rate limits', getAdminRateLimits, setRateLimits],
      ['API usage', getAdminApiUsage, setUsage],
    ];
    const results = await Promise.allSettled(calls.map(([, fn]) => fn()));
    const failed = [];
    results.forEach((res, i) => {
      const [name, , set] = calls[i];
      if (res.status === 'fulfilled') set(res.value);
      else failed.push(`${name} (${res.reason?.response?.data?.detail || res.reason?.message || 'error'})`);
    });
    if (failed.length) setError('Could not load: ' + failed.join(', '));
    setLoading(false);
  };

  useEffect(() => { fetchAll(); }, []);

  if (loading) return <div className="loading">Loading system status...</div>;

  // The endpoint returns several days so the card can show a trend; the main
  // list is today only, because "requests per day" against a quota is only
  // meaningful for the day whose quota is currently being spent.
  const todayUsage = (usage?.items || []).filter((i) => i.day === usage?.day);

  return (
    <div className="system-dashboard">
      <div className="management-header">
        <h2>System</h2>
        <button className="refresh-btn" onClick={fetchAll}><RefreshCw size={14} /> Refresh</button>
      </div>
      {error && <div className="error-message">{error}</div>}

      <div className="sys-grid">
        <StatCard icon={<GitBranch size={16} />} title="Deploy status">
          {deployStatus?.backend ? (
            <div className="sys-row"><span>Backend</span><span className={`sys-pill sys-pill-${deployStatus.backend.active_color}`}>{deployStatus.backend.active_color}</span></div>
          ) : <div className="sys-muted">Backend: not recorded yet</div>}
          {deployStatus?.backend && <div className="sys-detail">{deployStatus.backend.version} · {formatDate(deployStatus.backend.deployed_at)}</div>}
          {deployStatus?.frontend ? (
            <div className="sys-row" style={{ marginTop: '0.6rem' }}><span>Frontend</span><span className={`sys-pill sys-pill-${deployStatus.frontend.active_color}`}>{deployStatus.frontend.active_color}</span></div>
          ) : <div className="sys-muted" style={{ marginTop: '0.6rem' }}>Frontend: not recorded yet</div>}
          {deployStatus?.frontend && <div className="sys-detail">{deployStatus.frontend.version} · {formatDate(deployStatus.frontend.deployed_at)}</div>}
          <div className="sys-footnote">Monitoring only - deploys/switchovers happen via SSH + ./deploy.sh, never from here.</div>
        </StatCard>

        <StatCard icon={<HardDrive size={16} />} title="Offsite backup">
          {backupStatus?.last_run_at ? (
            <>
              <div className="sys-row"><span>Last run</span><span>{formatDate(backupStatus.last_run_at)}</span></div>
              <div className="sys-row"><span>Result</span><span className={backupStatus.success ? 'sys-ok' : 'sys-bad'}>{backupStatus.success ? 'success' : 'failed'}</span></div>
              <div className="sys-row"><span>Size</span><span>{backupStatus.archive_size_bytes ? `${(backupStatus.archive_size_bytes / 1048576).toFixed(1)} MB` : '—'}</span></div>
              <div className="sys-row"><span>Uploaded to R2</span><span className={backupStatus.r2_uploaded ? 'sys-ok' : 'sys-bad'}>{backupStatus.r2_uploaded ? 'yes' : 'no'}</span></div>
              {backupStatus.error && <div className="sys-detail sys-bad">{backupStatus.error}</div>}
            </>
          ) : <div className="sys-muted">No backup recorded yet</div>}
        </StatCard>

        <StatCard icon={<Sparkles size={16} />} title="Active providers">
          {config && (
            <>
              <div className="sys-row"><span>Story text</span><span>{config.text_generation.backend} ({config.text_generation.story_model})</span></div>
              <div className="sys-row"><span>Vision</span><span>{config.text_generation.vision_model}</span></div>
              <div className="sys-row"><span>Groq fallback model</span><span>{config.text_generation.groq_model}</span></div>
              <div className="sys-row"><span>Images</span><span>{config.image_generation.backend}</span></div>
              <div className="sys-row"><span>Voice</span><span>{config.voice_generation.backend}</span></div>
              <div className="sys-row"><span>Cache</span><span>{config.caching.enabled ? `on, ${config.caching.ttl_seconds}s TTL` : 'off'}</span></div>
            </>
          )}
        </StatCard>

        <StatCard icon={<DollarSign size={16} />} title="RunPod spend (this month)">
          {health?.runpod_usage && (
            <>
              <div className="sys-row"><span>Images generated</span><span>{health.runpod_usage.images}</span></div>
              <div className="sys-row"><span>Spent</span><span>{health.runpod_usage.spent_aed.toFixed(2)} AED</span></div>
              <div className="sys-row"><span>Cap</span><span>{health.runpod_usage.cap_aed.toFixed(2)} AED</span></div>
              <div className="sys-bar"><div className="sys-bar-fill" style={{ width: `${Math.min(100, (health.runpod_usage.spent_aed / health.runpod_usage.cap_aed) * 100)}%` }} /></div>
            </>
          )}
        </StatCard>

        <StatCard icon={<Activity size={16} />} title="Concurrency &amp; queue">
          {health && (
            <>
              {['images', 'tts', 'llm'].map((k) => (
                <div className="sys-row" key={k}><span style={{ textTransform: 'capitalize' }}>{k}</span><span>{health.concurrency[k].in_use}/{health.concurrency[k].limit} in use, {health.concurrency[k].waiting} waiting</span></div>
              ))}
              <div className="sys-row"><span>Queue</span><span>{health.queue.running} running, {health.queue.queued} queued (max {health.queue.max_depth})</span></div>
              <div className="sys-row"><span>Vision budget today</span><span>{health.vision_budget.used}/{health.vision_budget.cap} ({health.vision_budget.remaining} left)</span></div>
            </>
          )}
        </StatCard>

        <StatCard icon={<ShieldAlert size={16} />} title="Rate-limit activity">
          <div className="sys-footnote" style={{ marginBottom: '0.5rem' }}>{rateLimits?.note}</div>
          {rateLimits?.buckets?.length ? rateLimits.buckets.slice(0, 8).map((b) => (
            <div className="sys-row" key={b.key}><span className="sys-mono">{b.key}</span><span>{b.attempts_in_window}x, last {b.most_recent_seconds_ago}s ago</span></div>
          )) : <div className="sys-muted">No active rate-limit buckets right now.</div>}
        </StatCard>

        <StatCard icon={<Gauge size={16} />} title="API requests per day">
          <div className="sys-footnote" style={{ marginBottom: '0.5rem' }}>
            Gemini meters RPD per model <em>per project</em>, so each key has its own pool.
            Counts reset at 00:00 UTC.
          </div>
          {todayUsage.length ? todayUsage.map((u) => (
            <div className="sys-row rpd-row" key={`${u.provider}-${u.model}-${u.key_label}`}>
              <span className="sys-mono rpd-label">
                {u.key_label}
                <span className="rpd-model">{u.model}</span>
              </span>
              <span className="rpd-value">
                {u.cap ? `${u.calls} / ${u.cap}` : `${u.calls}`}
                {u.errors > 0 && <span className="rpd-errors"> · {u.errors} failed</span>}
                {u.pct != null && (
                  <span className={`rpd-bar ${u.pct >= 80 ? 'hot' : u.pct >= 50 ? 'warm' : ''}`}>
                    <span style={{ width: `${Math.min(100, u.pct)}%` }} />
                  </span>
                )}
              </span>
            </div>
          )) : <div className="sys-muted">No provider calls recorded today.</div>}
          {usage?.items?.length > todayUsage.length && (
            <div className="sys-footnote" style={{ marginTop: '0.5rem' }}>
              Earlier days: {usage.items.filter((i) => i.day !== usage.day)
                .map((i) => `${i.day} ${i.key_label} ${i.calls}`).join(' · ')}
            </div>
          )}
        </StatCard>
      </div>
    </div>
  );
};

export default SystemDashboard;
