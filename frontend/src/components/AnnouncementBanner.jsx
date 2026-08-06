import React, { useState, useEffect } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { X, Megaphone, AlertTriangle, ShieldAlert } from 'lucide-react';
import { getActiveAnnouncement } from '../services/api';
import './AnnouncementBanner.css';

const DISMISS_KEY_PREFIX = 'edusmart_dismissed_announcement_';

const ICONS = { info: Megaphone, warning: AlertTriangle, critical: ShieldAlert };

const AnnouncementBanner = () => {
  const [announcement, setAnnouncement] = useState(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getActiveAnnouncement()
      .then((data) => {
        if (cancelled || !data) return;
        setAnnouncement(data);
        setDismissed(localStorage.getItem(DISMISS_KEY_PREFIX + data.id) === '1');
      })
      .catch(() => {
        // A banner failing to load must never break the rest of the app.
      });
    return () => { cancelled = true; };
  }, []);

  if (!announcement || dismissed) return null;

  const Icon = ICONS[announcement.severity] || Megaphone;

  const handleDismiss = () => {
    localStorage.setItem(DISMISS_KEY_PREFIX + announcement.id, '1');
    setDismissed(true);
  };

  return (
    <AnimatePresence>
      <motion.div
        className={`announcement-banner severity-${announcement.severity}`}
        initial={{ y: -60, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: -60, opacity: 0 }}
        transition={{ duration: 0.3, ease: 'easeOut' }}
      >
        <Icon size={16} className="announcement-icon" />
        <span className="announcement-message">{announcement.message}</span>
        <button className="announcement-dismiss" onClick={handleDismiss} aria-label="Dismiss announcement">
          <X size={16} />
        </button>
      </motion.div>
    </AnimatePresence>
  );
};

export default AnnouncementBanner;
