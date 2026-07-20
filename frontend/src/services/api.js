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


export default apiClient;
