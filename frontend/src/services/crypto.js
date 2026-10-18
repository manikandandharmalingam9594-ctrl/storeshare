/**
 * Store-Share Browser-Side Cryptography Service.
 *
 * Implements Phase 4: AES-256-GCM encryption, decryption, and PBKDF2 key backup.
 *
 * ZERO-KNOWLEDGE SECURITY GUARANTEE:
 * - Encryption and decryption keys are generated and held EXCLUSIVELY in the consumer's browser.
 * - Files are encrypted in-memory before leaving the client.
 * - Decryption keys, recovery passphrases, and unencrypted plaintext NEVER touch
 *   the Store-Share coordinator, backend database, or storage provider nodes.
 */

export const MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024 // 5 MB

/**
 * Convert an ArrayBuffer or Uint8Array to a lowercase hex string.
 */
export function bufferToHex(buffer) {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer)
  let hex = ''
  for (let i = 0; i < bytes.length; i++) {
    hex += bytes[i].toString(16).padStart(2, '0')
  }
  return hex
}

/**
 * Convert a hex string to a Uint8Array.
 */
export function hexToBuffer(hex) {
  const clean = hex.trim().toLowerCase()
  if (clean.length % 2 !== 0) {
    throw new Error('Invalid hex string length: must be an even number of characters.')
  }
  const bytes = new Uint8Array(clean.length / 2)
  for (let i = 0; i < clean.length; i += 2) {
    const val = parseInt(clean.substring(i, i + 2), 16)
    if (isNaN(val)) {
      throw new Error(`Invalid hex character at position ${i}: ${clean.substring(i, i + 2)}`)
    }
    bytes[i / 2] = val
  }
  return bytes
}

/**
 * Convert an ArrayBuffer or Uint8Array to a Base64 string.
 */
export function bufferToBase64(buffer) {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer)
  if (typeof Buffer !== 'undefined') {
    // Node.js environment
    return Buffer.from(bytes).toString('base64')
  }
  let binary = ''
  const len = bytes.byteLength
  for (let i = 0; i < len; i++) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary)
}

/**
 * Convert a Base64 string to a Uint8Array.
 */
export function base64ToBuffer(base64) {
  if (typeof Buffer !== 'undefined') {
    // Node.js environment
    return new Uint8Array(Buffer.from(base64, 'base64'))
  }
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes
}

/**
 * Compute the SHA-256 checksum of an ArrayBuffer or Uint8Array.
 * Returns a 64-character lowercase hex string.
 */
export async function computeSHA256(buffer) {
  const hashBuffer = await crypto.subtle.digest('SHA-256', buffer)
  return bufferToHex(hashBuffer)
}

/**
 * Generate a fresh cryptographically random 256-bit AES-GCM key.
 */
export async function generateFileKey() {
  return await crypto.subtle.generateKey(
    {
      name: 'AES-GCM',
      length: 256,
    },
    true, // Extractable so the key can be exported to JWK for local vault and password backup
    ['encrypt', 'decrypt']
  )
}

/**
 * Generate a cryptographically random 12-byte (96-bit) IV for AES-GCM.
 */
export function generateIV() {
  const iv = new Uint8Array(12)
  crypto.getRandomValues(iv)
  return iv
}

/**
 * Export an AES-GCM CryptoKey to JSON Web Key (JWK) format.
 */
export async function exportKeyToJWK(key) {
  return await crypto.subtle.exportKey('jwk', key)
}

/**
 * Import a JWK into an AES-GCM CryptoKey.
 */
export async function importKeyFromJWK(jwk) {
  return await crypto.subtle.importKey(
    'jwk',
    jwk,
    { name: 'AES-GCM' },
    true,
    ['encrypt', 'decrypt']
  )
}

/**
 * Encrypt file data locally in the browser using AES-256-GCM.
 *
 * @param {ArrayBuffer|Uint8Array|File|Blob} fileData - The plaintext file contents.
 * @param {CryptoKey} key - AES-GCM 256-bit key.
 * @param {Uint8Array} [iv] - Optional 12-byte IV (generated automatically if omitted).
 * @returns {Promise<{
 *   ciphertextBuffer: ArrayBuffer,
 *   ciphertextBase64: string,
 *   ivHex: string,
 *   sha256Hex: string,
 *   plaintextSize: number,
 *   ciphertextSize: number
 * }>}
 */
export async function encryptFile(fileData, key, iv = null) {
  let plaintextBuffer
  if (typeof Blob !== 'undefined' && fileData instanceof Blob) {
    plaintextBuffer = await fileData.arrayBuffer()
  } else if (fileData instanceof ArrayBuffer) {
    plaintextBuffer = fileData
  } else if (fileData instanceof Uint8Array) {
    plaintextBuffer = fileData.buffer.slice(fileData.byteOffset, fileData.byteOffset + fileData.byteLength)
  } else {
    throw new Error('Unsupported fileData type: must be ArrayBuffer, Uint8Array, File, or Blob.')
  }

  const plaintextSize = plaintextBuffer.byteLength

  if (plaintextSize === 0) {
    throw new Error('File size must be greater than 0 bytes.')
  }

  if (plaintextSize > MAX_FILE_SIZE_BYTES) {
    throw new Error(
      `File size (${plaintextSize} bytes) exceeds maximum allowable limit of 5 MB (${MAX_FILE_SIZE_BYTES} bytes).`
    )
  }

  const usedIv = iv || generateIV()
  if (usedIv.length !== 12) {
    throw new Error('AES-GCM requires a 12-byte (96-bit) initialization vector.')
  }

  const ivHex = bufferToHex(usedIv)
  if (ivHex === '0'.repeat(24)) {
    throw new Error('Static all-zero IV is prohibited for AES-GCM encryption.')
  }

  // Web Crypto AES-GCM outputs ciphertext + 16-byte authentication tag
  const ciphertextBuffer = await crypto.subtle.encrypt(
    {
      name: 'AES-GCM',
      iv: usedIv,
    },
    key,
    plaintextBuffer
  )

  const ciphertextSize = ciphertextBuffer.byteLength
  const expectedCiphertextSize = plaintextSize + 16
  if (ciphertextSize !== expectedCiphertextSize) {
    throw new Error(
      `Encryption produced unexpected size: expected ${expectedCiphertextSize} bytes, got ${ciphertextSize}.`
    )
  }

  const sha256Hex = await computeSHA256(ciphertextBuffer)
  const ciphertextBase64 = bufferToBase64(ciphertextBuffer)

  return {
    ciphertextBuffer,
    ciphertextBase64,
    ivHex,
    sha256Hex,
    plaintextSize,
    ciphertextSize,
  }
}

/**
 * Decrypt ciphertext locally in the browser using AES-256-GCM.
 *
 * Verifies SHA-256 integrity before decryption.
 *
 * @param {ArrayBuffer|Uint8Array} ciphertextData - The raw ciphertext + tag.
 * @param {CryptoKey} key - The AES-GCM 256-bit key.
 * @param {string|Uint8Array} iv - The 12-byte IV (as hex string or Uint8Array).
 * @param {string} [expectedSha256] - Optional expected SHA-256 checksum for pre-decryption verification.
 * @returns {Promise<ArrayBuffer>} The decrypted plaintext bytes.
 */
export async function decryptFile(ciphertextData, key, iv, expectedSha256 = null) {
  const ciphertextBuffer =
    ciphertextData instanceof ArrayBuffer
      ? ciphertextData
      : ciphertextData.buffer.slice(ciphertextData.byteOffset, ciphertextData.byteOffset + ciphertextData.byteLength)

  if (ciphertextBuffer.byteLength < 16) {
    throw new Error('Ciphertext is too short: must be at least 16 bytes (authentication tag).')
  }

  // 1. Verify SHA-256 checksum before decryption if provided
  if (expectedSha256) {
    const computedSha = await computeSHA256(ciphertextBuffer)
    if (computedSha.toLowerCase() !== expectedSha256.trim().toLowerCase()) {
      throw new Error(
        `Ciphertext SHA-256 integrity verification failed: computed '${computedSha}' does not match expected '${expectedSha256}'. Transmission may be corrupted or tampered with.`
      )
    }
  }

  // 2. Format IV
  const ivBuffer = typeof iv === 'string' ? hexToBuffer(iv) : iv
  if (ivBuffer.length !== 12) {
    throw new Error('AES-GCM requires a 12-byte (96-bit) initialization vector.')
  }

  // 3. Decrypt and authenticate via Web Crypto AES-GCM
  try {
    const plaintextBuffer = await crypto.subtle.decrypt(
      {
        name: 'AES-GCM',
        iv: ivBuffer,
      },
      key,
      ciphertextBuffer
    )
    return plaintextBuffer
  } catch (err) {
    throw new Error(
      `Decryption failed: cryptographic authentication tag mismatch. The encryption key may be incorrect, or the ciphertext has been modified (${err.message || 'Authentication error'}).`
    )
  }
}

/**
 * Derive an AES-256-GCM key from a user passphrase using PBKDF2-HMAC-SHA256.
 * Uses 100,000 iterations to protect against offline brute-force attacks.
 */
export async function deriveKeyFromPassphrase(passphrase, salt, iterations = 100000) {
  if (!passphrase || passphrase.length < 8) {
    throw new Error('Passphrase must be at least 8 characters long.')
  }
  const enc = new TextEncoder()
  const passKey = await crypto.subtle.importKey(
    'raw',
    enc.encode(passphrase),
    'PBKDF2',
    false,
    ['deriveKey']
  )

  const saltBytes = salt instanceof Uint8Array ? salt : hexToBuffer(salt)

  return await crypto.subtle.deriveKey(
    {
      name: 'PBKDF2',
      salt: saltBytes,
      iterations,
      hash: 'SHA-256',
    },
    passKey,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt']
  )
}

/**
 * Encrypt an exported key vault payload into a password-protected backup bundle.
 *
 * @param {Array<Object>} keyRecords - List of key records (with JWK and metadata).
 * @param {string} passphrase - User-chosen backup passphrase.
 * @returns {Promise<Object>} The encrypted backup bundle object.
 */
export async function encryptKeyVaultBackup(keyRecords, passphrase) {
  if (!Array.isArray(keyRecords)) {
    throw new Error('keyRecords must be an array.')
  }

  // 16-byte random salt
  const salt = new Uint8Array(16)
  crypto.getRandomValues(salt)

  // 12-byte random IV
  const iv = generateIV()

  // Derive encryption key
  const derivedKey = await deriveKeyFromPassphrase(passphrase, salt, 100000)

  // Serialize payload
  const enc = new TextEncoder()
  const payloadJson = JSON.stringify({
    version: 1,
    exported_at: new Date().toISOString(),
    key_count: keyRecords.length,
    keys: keyRecords,
  })
  const payloadBytes = enc.encode(payloadJson)

  // Encrypt with AES-256-GCM
  const ciphertextBuffer = await crypto.subtle.encrypt(
    {
      name: 'AES-GCM',
      iv,
    },
    derivedKey,
    payloadBytes
  )

  return {
    app: 'Store-Share',
    version: 1,
    type: 'storeshare-key-vault-backup',
    created_at: new Date().toISOString(),
    kdf: {
      algorithm: 'PBKDF2',
      hash: 'SHA-256',
      iterations: 100000,
      salt_hex: bufferToHex(salt),
    },
    cipher: {
      algorithm: 'AES-256-GCM',
      iv_hex: bufferToHex(iv),
    },
    ciphertext_base64: bufferToBase64(ciphertextBuffer),
  }
}

/**
 * Decrypt and verify a password-protected key vault backup bundle.
 *
 * @param {Object|string} backupBundle - The backup bundle object or JSON string.
 * @param {string} passphrase - The user's passphrase.
 * @returns {Promise<Array<Object>>} The restored key records.
 */
export async function decryptKeyVaultBackup(backupBundle, passphrase) {
  const bundle = typeof backupBundle === 'string' ? JSON.parse(backupBundle) : backupBundle

  if (!bundle || bundle.type !== 'storeshare-key-vault-backup') {
    throw new Error('Invalid backup file: missing or unrecognized backup format.')
  }

  if (bundle.version !== 1) {
    throw new Error(`Unsupported backup bundle version: ${bundle.version}.`)
  }

  const salt = hexToBuffer(bundle.kdf.salt_hex)
  const iv = hexToBuffer(bundle.cipher.iv_hex)
  const iterations = bundle.kdf.iterations || 100000
  const ciphertextBytes = base64ToBuffer(bundle.ciphertext_base64)

  // Derive key using bundle parameters
  const derivedKey = await deriveKeyFromPassphrase(passphrase, salt, iterations)

  // Decrypt
  try {
    const decryptedBuffer = await crypto.subtle.decrypt(
      {
        name: 'AES-GCM',
        iv,
      },
      derivedKey,
      ciphertextBytes
    )

    const dec = new TextDecoder()
    const jsonStr = dec.decode(decryptedBuffer)
    const payload = JSON.parse(jsonStr)

    if (!payload.keys || !Array.isArray(payload.keys)) {
      throw new Error('Backup payload is missing keys array.')
    }

    return payload.keys
  } catch (err) {
    throw new Error(
      `Failed to decrypt key backup: incorrect passphrase or corrupted file (${err.message || 'Authentication error'}).`
    )
  }
}
