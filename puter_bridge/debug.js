const path = require('path');
console.log('CWD:', process.cwd());
console.log('__dirname:', __dirname);
console.log('PUTER_AUTH_TOKEN length:', (process.env.PUTER_AUTH_TOKEN || '').length);
console.log('First 30 chars:', (process.env.PUTER_AUTH_TOKEN || '').substring(0,30));
