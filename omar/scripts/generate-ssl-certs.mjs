import { execSync } from 'node:child_process';
import { existsSync, mkdirSync, copyFileSync, writeFileSync } from 'node:fs';
import { networkInterfaces } from 'node:os';
import { join, resolve } from 'node:path';

const ROOT_DIR = resolve(import.meta.dirname, '..');
const CERT_DIR = join(ROOT_DIR, 'certificates');
const OPENSSL = existsSync('C:\\Program Files\\Git\\usr\\bin\\openssl.exe')
  ? 'C:\\Program Files\\Git\\usr\\bin\\openssl.exe'
  : 'openssl';

if (!existsSync(CERT_DIR)) {
  mkdirSync(CERT_DIR, { recursive: true });
}

// 1. Gather all local IPv4 addresses
const ips = new Set(['127.0.0.1']);
const nets = networkInterfaces();
for (const iface of Object.values(nets)) {
  if (!iface) continue;
  for (const config of iface) {
    if (config.family === 'IPv4' && !config.internal) {
      ips.add(config.address);
    }
  }
}

console.log('[ssl] Detected host LAN IPs:', Array.from(ips).join(', '));

// 2. Build SAN list
const sanList = [
  'DNS.1 = localhost',
  'DNS.2 = *.local',
  'DNS.3 = kinetiq.local',
  'DNS.4 = host.docker.internal',
];
let ipIndex = 1;
for (const ip of ips) {
  sanList.push(`IP.${ipIndex++} = ${ip}`);
}

const caKey = join(CERT_DIR, 'kinetiq-ca.key');
const caCrt = join(CERT_DIR, 'kinetiq-ca.crt');
const serverKey = join(CERT_DIR, 'server.key');
const serverCsr = join(CERT_DIR, 'server.csr');
const serverCrt = join(CERT_DIR, 'server.crt');
const opensslCnf = join(CERT_DIR, 'openssl.cnf');

const cnfContent = `
[ req ]
default_bits        = 2048
prompt              = no
default_md          = sha256
distinguished_name  = req_distinguished_name
req_extensions      = req_ext

[ req_distinguished_name ]
C                   = EG
ST                  = Cairo
L                   = Cairo
O                   = KinetiQ Smart Gym
OU                  = Edge Engineering
CN                  = KinetiQ Local Server

[ req_ext ]
subjectAltName      = @alt_names

[ v3_ca ]
subjectKeyIdentifier   = hash
authorityKeyIdentifier = keyid:always,issuer
basicConstraints       = critical, CA:true
keyUsage               = critical, digitalSignature, cRLSign, keyCertSign

[ v3_req ]
basicConstraints     = CA:FALSE
keyUsage             = digitalSignature, keyEncipherment
extendedKeyUsage     = serverAuth, clientAuth
subjectAltName       = @alt_names

[ alt_names ]
${sanList.join('\n')}
`;

writeFileSync(opensslCnf, cnfContent.trim(), 'utf8');

// 3. Generate CA if not exists
if (!existsSync(caKey) || !existsSync(caCrt)) {
  console.log('[ssl] Generating Root CA...');
  execSync(`"${OPENSSL}" genrsa -out "${caKey}" 2048`, { stdio: 'inherit' });
  execSync(
    `"${OPENSSL}" req -x509 -new -nodes -key "${caKey}" -sha256 -days 3650 -out "${caCrt}" -subj "/C=EG/ST=Cairo/L=Cairo/O=KinetiQ Smart Gym/OU=Root CA/CN=KinetiQ Gym Local Root CA" -extensions v3_ca -config "${opensslCnf}"`,
    { stdio: 'inherit' }
  );
  console.log('[ssl] Created Root CA:', caCrt);
} else {
  console.log('[ssl] Using existing Root CA:', caCrt);
}

// 4. Generate Server Key & CSR
console.log('[ssl] Generating Server Private Key and CSR...');
execSync(`"${OPENSSL}" genrsa -out "${serverKey}" 2048`, { stdio: 'inherit' });
execSync(
  `"${OPENSSL}" req -new -key "${serverKey}" -out "${serverCsr}" -config "${opensslCnf}"`,
  { stdio: 'inherit' }
);

// 5. Sign Server Certificate with Root CA
console.log('[ssl] Signing Server Certificate with Root CA...');
execSync(
  `"${OPENSSL}" x509 -req -in "${serverCsr}" -CA "${caCrt}" -CAkey "${caKey}" -CAcreateserial -out "${serverCrt}" -days 1825 -sha256 -extfile "${opensslCnf}" -extensions v3_req`,
  { stdio: 'inherit' }
);
console.log('[ssl] Created Server Certificate:', serverCrt);

// 6. Copy certificates to apps
const memberPublic = join(ROOT_DIR, 'apps', 'member-web', 'public');
const memberCertDir = join(ROOT_DIR, 'apps', 'member-web', 'certificates');
const adminPublic = join(ROOT_DIR, 'apps', 'admin-web', 'public');
const adminCertDir = join(ROOT_DIR, 'apps', 'admin-web', 'certificates');

for (const dir of [memberPublic, memberCertDir, adminPublic, adminCertDir]) {
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
}

// Copy Root CA to public for easy mobile download
copyFileSync(caCrt, join(memberPublic, 'kinetiq-ca.crt'));
copyFileSync(caCrt, join(adminPublic, 'kinetiq-ca.crt'));

// Copy Server cert and key to certificates folders
copyFileSync(serverKey, join(memberCertDir, 'server.key'));
copyFileSync(serverCrt, join(memberCertDir, 'server.crt'));
copyFileSync(caCrt, join(memberCertDir, 'ca.crt'));

copyFileSync(serverKey, join(adminCertDir, 'server.key'));
copyFileSync(serverCrt, join(adminCertDir, 'server.crt'));
copyFileSync(caCrt, join(adminCertDir, 'ca.crt'));

console.log('[ssl] Certificates successfully deployed to apps/member-web and apps/admin-web!');

