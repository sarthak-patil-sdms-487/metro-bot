import axios from 'axios';

const defaultApiBaseUrl = 'http://localhost:8000/api/v1';

export const api = axios.create({
  baseURL: import.meta.env.VITE_PUBLIC_API_BASE_URL || defaultApiBaseUrl,
});

export async function getPublicStats() {
  const response = await api.get('/public/stats');
  return response.data;
}
