import axios from 'axios';
import toast from 'react-hot-toast';

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL,
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const message = error.response?.data?.detail || error.message || 'An unexpected error occurred';
    toast.error(message);
    
    if (error.response?.status === 401 && window.location.pathname !== '/login') {
      // Expired protected sessions return to login. A failed login itself stays
      // on this page so the API error toast remains visible to the administrator.
      localStorage.removeItem('authToken');
      window.location.href = '/login';
    }

    return Promise.reject(error);
  }
);

export default apiClient;
