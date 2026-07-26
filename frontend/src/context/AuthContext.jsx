import { createContext, useState, useContext, useEffect, useCallback, useMemo } from 'react';
import axios from 'axios';

const AuthContext = createContext();

// Get API URL from environment variables, defaulting for local development
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

// Create a dedicated API client using Axios
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

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(() => localStorage.getItem('auth_token'));
  const [isLoading, setIsLoading] = useState(true);

  // Function to fetch the current user's data if a token exists.
  // useCallback is used for optimization, preventing re-creation on every render.
  const fetchCurrentUser = useCallback(async () => {
    if (localStorage.getItem('auth_token')) {
      try {
        const response = await apiClient.get('/api/auth/me');
        setUser(response.data);
      } catch (error) {
        // This likely means the token is invalid or expired.
        console.error("Failed to fetch user with stored token, logging out.", error);
        // Clean up the invalid token.
        localStorage.removeItem('auth_token');
        setToken(null);
        setUser(null);
      }
    }
    setIsLoading(false);
  }, []);

  // On initial application load, check for a token and try to fetch the user.
  useEffect(() => {
    fetchCurrentUser();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Signup function
  const signup = useCallback(async (username, email, password, confirmPassword) => {
    // Axios throws an error for non-2xx responses, simplifying error handling.
    const response = await apiClient.post('/api/auth/signup', {
      username,
      email,
      password,
      confirm_password: confirmPassword,
    });
    // On success, return the new user data.
    return response.data;
  }, []);

  // Resend verification email function
  const resendVerificationEmail = useCallback(async (email) => {
    const response = await apiClient.post('/api/auth/resend-verification', null, {
      params: { email },
    });
    return response.data;
  }, []);

  // Login function
  const login = useCallback(async (email, password) => {
    // The new backend /token endpoint expects data in 'application/x-www-form-urlencoded' format.
    const params = new URLSearchParams();
    params.append('username', email); // Per OAuth2 standard, the username field holds the email
    params.append('password', password);

    const response = await apiClient.post('/api/auth/token', params, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });

    const { access_token } = response.data;
    localStorage.setItem('auth_token', access_token);
    setToken(access_token);
    
    // After successfully getting the token, fetch the user's data.
    await fetchCurrentUser();
  }, [fetchCurrentUser]);

  // Social sign-in (Google / Facebook).
  // The provider token is verified server-side - nothing here decides who the
  // user is. The backend returns the same JWT password login does, so the
  // storage and fetch below are deliberately identical to login().
  const socialLogin = useCallback(async (provider, token) => {
    const response = await apiClient.post(`/api/auth/social/${provider}`, { token });

    const { access_token } = response.data;
    localStorage.setItem('auth_token', access_token);
    setToken(access_token);

    await fetchCurrentUser();
  }, [fetchCurrentUser]);

  // Logout function
  const logout = useCallback(() => {
    localStorage.removeItem('auth_token');
    setToken(null);
    setUser(null);
  }, []);

  // The value provided to consumers of the context
  const authContextValue = useMemo(() => ({
    user,
    token,
    isLoading,
    isAuthenticated: !!user,
    signup,
    login,
    socialLogin,
    logout,
    resendVerificationEmail,
  }), [user, token, isLoading]);

  return (
    <AuthContext.Provider value={authContextValue}>
      {children}
    </AuthContext.Provider>
  );
};

// Custom hook for easy access to the auth context
export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};