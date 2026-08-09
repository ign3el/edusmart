import axios from 'axios';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const apiClient = axios.create({
  baseURL: API_URL,
});

// Use an interceptor to automatically add the auth token to every request header
apiClient.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('auth_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// --- Admin DB Viewer Functions ---

export const getJobStateDbTables = async () => {
  const response = await apiClient.get('/api/admin/db/job_state/tables');
  return response.data;
};

export const getJobStateTableData = async (tableName) => {
  const response = await apiClient.get(`/api/admin/db/job_state/table/${tableName}`);
  return response.data;
};

export const cancelStuckJob = async (storyId) => {
  const response = await apiClient.post(`/api/admin/stories/${storyId}/cancel`);
  return response.data;
};

// Repairs a failed job in place - regenerates only the scenes whose image or
// audio is missing, reusing the stored scene text and image prompt. Does NOT
// re-run the story text: the source document is deleted after processing.
export const retryFailedJob = async (storyId) => {
  const response = await apiClient.post(`/api/admin/stories/${storyId}/retry`);
  return response.data;
};

export const deleteJob = async (storyId) => {
  const response = await apiClient.delete(`/api/admin/stories/${storyId}`);
  return response.data;
};

export const generateTestSpeech = async (settings) => {
  const { text, language, speed, silence } = settings;
  const response = await apiClient.post('/api/admin/tts/test', {
    text,
    language,
    speed,
    silence
  }, {
    responseType: 'arraybuffer' // This is crucial for receiving audio data
  });
  return response.data;
}

// --- Progressive TTS Functions ---

export const getTtsStatus = async (storyId) => {
  const response = await apiClient.get(`/api/story/${storyId}/tts-status`);
  return response.data;
};

// Quiz completion
export const markQuizComplete = async (storyId) => {
  const response = await apiClient.post(`/api/story/${storyId}/complete-quiz`);
  return response.data;
};

// --- Billing Functions ---
// Pricing itself lives server-side in the subscription_plans table, not here -
// getPlans() always reflects whatever an admin has configured, live.

export const getPlans = async () => {
  const response = await apiClient.get('/api/billing/plans');
  return response.data;
};

export const getBillingBalance = async () => {
  const response = await apiClient.get('/api/billing/balance');
  return response.data;
};

export const redeemPromoCode = async (code) => {
  const response = await apiClient.post('/api/billing/redeem-promo', { code });
  return response.data;
};

export const createCheckoutSession = async (tierKey, promoCode) => {
  const response = await apiClient.post('/api/billing/checkout', {
    tier_key: tierKey,
    promo_code: promoCode || undefined,
  });
  return response.data;
};

export const createBillingPortalSession = async () => {
  const response = await apiClient.post('/api/billing/portal');
  return response.data;
};

// --- Admin: System dashboard (all read-only, no deploy/switchover trigger exists) ---

export const getAdminSystemConfig = async () => (await apiClient.get('/api/admin/system/config')).data;
export const getAdminDeployStatus = async () => (await apiClient.get('/api/admin/system/deploy-status')).data;
export const getAdminBackupStatus = async () => (await apiClient.get('/api/admin/system/backup-status')).data;
export const getAdminSystemHealth = async () => (await apiClient.get('/api/admin/system/health')).data;
export const getAdminRateLimits = async () => (await apiClient.get('/api/admin/system/rate-limits')).data;

export const getAdminContentReview = async () => (await apiClient.get('/api/admin/system/content-review')).data;

// Requests-per-day per model per API key. Returns key LABELS only - the key
// values are never sent to the client.
export const getAdminApiUsage = async (days = 3) =>
  (await apiClient.get('/api/admin/usage/rpd', { params: { days } })).data;

export const getAdminAuditLog = async (params = {}) =>
  (await apiClient.get('/api/admin/system/audit-log', { params })).data;

// --- Admin: Feature flags ---

export const getAdminConfigFlags = async () => (await apiClient.get('/api/admin/config-flags')).data;
export const updateAdminConfigFlag = async (key, configValue) =>
  (await apiClient.put(`/api/admin/config-flags/${key}`, { config_value: configValue })).data;

// --- Admin: Announcements (site-wide banner) ---

export const getAdminAnnouncements = async () => (await apiClient.get('/api/admin/announcements')).data;
export const createAdminAnnouncement = async (payload) =>
  (await apiClient.post('/api/admin/announcements', payload)).data;
export const updateAdminAnnouncement = async (id, payload) =>
  (await apiClient.put(`/api/admin/announcements/${id}`, payload)).data;

// Public - no auth. What the site-wide banner calls.
export const getActiveAnnouncement = async () => (await apiClient.get('/api/announcements/active')).data;

// Permanently deletes the signed-in account. Irreversible.
// The credential goes in the request BODY, not the query string - a password in
// a URL ends up in nginx access logs and browser history.
export const deleteAccount = async ({ password, confirmEmail }) => {
  const response = await apiClient.delete('/api/auth/me', {
    data: { password: password || null, confirm_email: confirmEmail || null },
  });
  return response.data;
};

// --- Share links ---
//
// The owner-side calls go through apiClient so the interceptor attaches the
// JWT. getSharedStory deliberately does NOT: it is the anonymous read path, and
// sending a stale token there would only invite a 401 on a route that needs no
// credentials at all.

export const getShareLink = async (storyId) =>
  (await apiClient.get(`/api/story/${storyId}/share`)).data;

export const createShareLink = async (storyId, { rotate = false } = {}) =>
  (await apiClient.post(`/api/story/${storyId}/share`, { rotate })).data;

export const revokeShareLink = async (storyId) =>
  (await apiClient.delete(`/api/story/${storyId}/share`)).data;

export const getSharedStory = async (token) => {
  const response = await axios.get(`${API_URL}/api/share/${encodeURIComponent(token)}`);
  return response.data;
};

// Video export - one render per story, tracked server-side in story_videos.
// generateVideo is safe to call again while a render is already in flight:
// the backend returns the existing progress instead of starting a second one.
export const generateVideo = async (storyId) =>
  (await apiClient.post(`/api/story/${storyId}/video`)).data;

export const getVideoStatus = async (storyId) =>
  (await apiClient.get(`/api/story/${storyId}/video/status`)).data;

// Fetched as a blob rather than used directly as a <video src>/<a href>: the
// route needs the same Bearer JWT every other authenticated call uses, and
// browsers cannot attach a custom header to a plain tag src. The caller turns
// this into an object URL (see VideoExportModal).
export const fetchVideoBlob = async (storyId) =>
  (await apiClient.get(`/api/story/${storyId}/video`, { responseType: 'blob' })).data;

export default apiClient;
