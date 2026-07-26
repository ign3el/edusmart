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

export default apiClient;
