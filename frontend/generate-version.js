const fs = require('fs');
const path = require('path');

// Read package.json for version
const packageJson = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'package.json'), 'utf8')
);

// Create version.json with current timestamp
const versionInfo = {
  version: packageJson.version,
  buildTime: new Date().toISOString()
};

// Write to public directory
const publicDir = path.join(__dirname, 'public');
if (!fs.existsSync(publicDir)) {
  fs.mkdirSync(publicDir, { recursive: true });
}

fs.writeFileSync(
  path.join(publicDir, 'version.json'),
  JSON.stringify(versionInfo, null, 2)
);

// Stamp the same build identity into sw.js's CACHE_VERSION so the service
// worker file itself changes bytes on every build. Without this, sw.js was
// byte-identical release to release, so browsers never detected an update
// and the activate handler's stale-cache cleanup never ran - users stayed
// stuck on whatever bundle they first loaded, no matter how many times the
// app was redeployed.
const swPath = path.join(publicDir, 'sw.js');
const buildId = versionInfo.buildTime.replace(/[^0-9]/g, '').slice(0, 14);
const swContent = fs.readFileSync(swPath, 'utf8');
const stampedSw = swContent.replace(
  /const CACHE_VERSION = '[^']*';/,
  `const CACHE_VERSION = 'build-${buildId}';`
);
fs.writeFileSync(swPath, stampedSw);

console.log('✅ Version file generated:', versionInfo);
console.log('✅ Service worker cache version stamped:', buildId);
