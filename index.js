module.exports = {
  /**
   * Returns information about this package.
   * 
   * @returns {Object} Package information
   */
  getInfo: function() {
    return {
      name: 'cwm-api-gateway-mcp',
      description: 'OpenAI API MCP Gateway',
      version: require('./package.json').version,
      repository: 'zz'
    };s
  },
  

  serverPath: require('path').join(__dirname, 'bin', 'server.js')
};
