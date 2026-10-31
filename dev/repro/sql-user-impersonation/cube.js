module.exports = {
  checkSqlAuth: async (req, user) => ({ password: 'repro', securityContext: { user } }),
  canSwitchSqlUser: async (current, target) => current === 'repro',
};
