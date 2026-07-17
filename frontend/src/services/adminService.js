import api from './api';

// Admin API service — all endpoints require admin role
const adminService = {
  // Dashboard
  getDashboard: () => api.get('/admin/dashboard'),

  // Users
  getUsers: (params = {}) => api.get('/admin/users', { params }),
  getUser: (userId) => api.get(`/admin/users/${userId}`),
  updateUser: (userId, data) => api.put(`/admin/users/${userId}`, data),
  blockUser: (userId) => api.post(`/admin/users/${userId}/block`),
  unblockUser: (userId) => api.post(`/admin/users/${userId}/unblock`),
  deleteUser: (userId) => api.delete(`/admin/users/${userId}`),
  grantPlan: (userId, data) => api.post(`/admin/users/${userId}/grant-plan`, data),

  // Payments
  getPayments: (params = {}) => api.get('/admin/payments', { params }),
  getPaymentStats: () => api.get('/admin/payments/stats'),
  refundPayment: (paymentId) => api.post(`/admin/payments/${paymentId}/refund`),

  // AI
  getAIStatus: () => api.get('/admin/ai/status'),
  getAIUsage: () => api.get('/admin/ai/usage'),

  // APIs
  getAPIHealth: () => api.get('/admin/apis/health'),

  // Analytics
  getUserAnalytics: () => api.get('/admin/analytics/users'),
  getRevenueAnalytics: () => api.get('/admin/analytics/revenue'),
  getFeatureAnalytics: () => api.get('/admin/analytics/features'),

  // Logs
  getLogs: (params = {}) => api.get('/admin/logs', { params }),

  // Support
  getTickets: (params = {}) => api.get('/admin/support/tickets', { params }),
  createTicket: (data) => api.post('/admin/support/tickets', data),
  updateTicket: (ticketId, data) => api.put(`/admin/support/tickets/${ticketId}`, data),

  // Feature Flags
  getFeatureFlags: () => api.get('/admin/feature-flags'),
  createFeatureFlag: (data) => api.post('/admin/feature-flags', data),
  updateFeatureFlag: (flagId, data) => api.put(`/admin/feature-flags/${flagId}`, data),

  // Announcements
  getAnnouncements: () => api.get('/admin/announcements'),
  createAnnouncement: (data) => api.post('/admin/announcements', data),
  updateAnnouncement: (annId, data) => api.put(`/admin/announcements/${annId}`, data),
  deleteAnnouncement: (annId) => api.delete(`/admin/announcements/${annId}`),

  // System Health
  getSystemHealth: () => api.get('/admin/system/health'),
};

export default adminService;
