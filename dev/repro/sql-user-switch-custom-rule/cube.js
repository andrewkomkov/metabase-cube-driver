module.exports = {
  checkSqlAuth: async (req, user) => ({ password: 'repro', securityContext: { user } }),
  canSwitchSqlUser: async () => true,
};
