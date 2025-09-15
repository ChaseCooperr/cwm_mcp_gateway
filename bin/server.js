#!/usr/bin/env node

const path = require('path');
const spawn = require('cross-spawn');
const fs = require('fs');

// Get the directory where this script is located
const scriptDir = __dirname;
// Get the root directory of the package
const rootDir = path.resolve(scriptDir, '..');
// Path to the Python script
const pythonScript = path.join(rootDir, 'api_gateway_server.py');

// Determine the Python executable to use
let pythonCommand = 'python';
// On Unix-like systems, prefer python3
if (process.platform !== 'win32') {
  pythonCommand = 'python3';
}

// Verify that the Python script exists
if (!fs.existsSync(pythonScript)) {
  console.error(`Error: Could not find the Python script at ${pythonScript}`);
  process.exit(1);
}

// Run the Python script with all arguments passed to this script
const result = spawn.sync(
  pythonCommand,
  [pythonScript, ...process.argv.slice(2)],
  { 
    stdio: 'inherit',
    env: process.env
  }
);

// Exit with the same code as the Python script
process.exit(result.status);
