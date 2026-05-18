'use strict'

/**
 * Local ESLint plugin — exposes project-specific rules under the "local/" namespace.
 * Installed as a file: dependency; run `npm install` once after cloning.
 */
const noHexColor = require('../eslint-rules/no-hex-color')

module.exports = {
  rules: {
    'no-hex-color': noHexColor,
  },
}
