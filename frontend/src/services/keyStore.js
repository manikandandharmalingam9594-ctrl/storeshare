/**
 * Store-Share Consumer Key Vault (IndexedDB + Cryptographic Key Management).
 *
 * Implements Phase 4: Local Key Storage, Non-Extractable In-Memory Keys,
 * and Password-Protected Authenticated Key Backup & Recovery.
 *
 * ZERO-KNOWLEDGE PRINCIPLES:
 * 1. Encryption keys are generated and stored strictly inside the consumer's local browser vault.
 * 2. Keys are NEVER transmitted to the Store-Share coordinator or storage providers.
 * 3. IndexedDB storage is local to this browser profile. If browser cache/data is cleared
 *    or the user accesses Store-Share from a new machine, files CANNOT be decrypted without
 *    restoring from a password-protected key backup (.storeshare-keys).
 */

import {
  decryptKeyVaultBackup,
  encryptKeyVaultBackup,
  exportKeyToJWK,
  importKeyFromJWK,
} from './crypto.js'

const DB_NAME = 'StoreShareKeyVault'
const DB_VERSION = 1
const STORE_NAME = 'file_keys'

// In-memory fallback map for non-browser/test environments where indexedDB is undefined
const memoryStore = new Map()

/**
 * Open or initialize the IndexedDB key vault database.
 */
function openDB() {
  if (typeof indexedDB === 'undefined') {
    // Return null to signal using memory fallback
    return Promise.resolve(null)
  }

  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)

    request.onupgradeneeded = (event) => {
      const db = event.target.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: 'file_id' })
        store.createIndex('sha256_hash', 'sha256_hash', { unique: false })
        store.createIndex('filename', 'filename', { unique: false })
        store.createIndex('created_at', 'created_at', { unique: false })
      }
    }

    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

/**
 * Save an encryption key and its associated file metadata to the local key vault.
 *
 * @param {number} fileId - The backend file record ID.
 * @param {CryptoKey} cryptoKey - The AES-GCM CryptoKey used for encryption.
 * @param {Object} metadata - File metadata ({ filename, sha256_hash, plaintextSize }).
 */
export async function saveFileKey(fileId, cryptoKey, metadata = {}) {
  if (!fileId) {
    throw new Error('fileId is required to save an encryption key.')
  }

  const jwk = await exportKeyToJWK(cryptoKey)
  const record = {
    file_id: Number(fileId),
    filename: metadata.filename || 'unknown',
    sha256_hash: metadata.sha256_hash || '',
    file_size: metadata.plaintextSize || 0,
    jwk,
    created_at: new Date().toISOString(),
  }

  const db = await openDB()
  if (!db) {
    memoryStore.set(record.file_id, record)
    return record
  }

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    const req = store.put(record)

    req.onsuccess = () => resolve(record)
    req.onerror = () => reject(req.error)
  })
}

/**
 * Retrieve a decryption key from the vault and instantiate it as a CryptoKey.
 *
 * For regular file decryption, this imports the key as non-extractable
 * (extractable: false) in memory to reduce the attack surface.
 *
 * @param {number} fileId - Backend file record ID.
 * @param {boolean} [extractable=false] - Whether the instantiated key should be extractable.
 * @returns {Promise<CryptoKey|null>} The CryptoKey, or null if not found.
 */
export async function getFileKey(fileId, extractable = false) {
  const id = Number(fileId)
  let record = null

  const db = await openDB()
  if (!db) {
    record = memoryStore.get(id) || null
  } else {
    record = await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readonly')
      const store = tx.objectStore(STORE_NAME)
      const req = store.get(id)

      req.onsuccess = () => resolve(req.result || null)
      req.onerror = () => reject(req.error)
    })
  }

  if (!record || !record.jwk) {
    return null
  }

  // Import into Web Crypto (defaulting to non-extractable for normal operation)
  return await crypto.subtle.importKey(
    'jwk',
    record.jwk,
    { name: 'AES-GCM' },
    extractable,
    ['encrypt', 'decrypt']
  )
}

/**
 * Check whether a decryption key exists in the vault for a given file ID.
 */
export async function hasFileKey(fileId) {
  const id = Number(fileId)
  const db = await openDB()
  if (!db) {
    return memoryStore.has(id)
  }

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly')
    const store = tx.objectStore(STORE_NAME)
    const req = store.get(id)

    req.onsuccess = () => resolve(!!req.result)
    req.onerror = () => reject(req.error)
  })
}

/**
 * Delete a key record from the vault (e.g. after file deletion).
 */
export async function deleteFileKey(fileId) {
  const id = Number(fileId)
  const db = await openDB()
  if (!db) {
    return memoryStore.delete(id)
  }

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    const req = store.delete(id)

    req.onsuccess = () => resolve(true)
    req.onerror = () => reject(req.error)
  })
}

/**
 * List all file key metadata in the vault (without returning raw JWK material).
 */
export async function listVaultKeyMetadata() {
  const db = await openDB()
  if (!db) {
    return Array.from(memoryStore.values()).map(({ file_id, filename, sha256_hash, file_size, created_at }) => ({
      file_id,
      filename,
      sha256_hash,
      file_size,
      created_at,
    }))
  }

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly')
    const store = tx.objectStore(STORE_NAME)
    const req = store.getAll()

    req.onsuccess = () => {
      const results = (req.result || []).map(({ file_id, filename, sha256_hash, file_size, created_at }) => ({
        file_id,
        filename,
        sha256_hash,
        file_size,
        created_at,
      }))
      resolve(results)
    }
    req.onerror = () => reject(req.error)
  })
}

/**
 * Fetch all complete key records (internal helper for encrypted backup).
 */
async function getAllKeyRecords() {
  const db = await openDB()
  if (!db) {
    return Array.from(memoryStore.values())
  }

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly')
    const store = tx.objectStore(STORE_NAME)
    const req = store.getAll()

    req.onsuccess = () => resolve(req.result || [])
    req.onerror = () => reject(req.error)
  })
}

/**
 * Export all local vault keys into an authenticated, password-encrypted backup file.
 *
 * @param {string} passphrase - User-chosen passphrase (min 8 characters).
 * @returns {Promise<Object>} Encrypted backup bundle.
 */
export async function exportVaultBackup(passphrase) {
  const records = await getAllKeyRecords()
  if (records.length === 0) {
    throw new Error('Key vault is empty: there are no encryption keys to export.')
  }

  return await encryptKeyVaultBackup(records, passphrase)
}

/**
 * Decrypt and import keys from an authenticated backup bundle into the local vault.
 *
 * Merges keys into IndexedDB, preserving existing keys or updating missing ones.
 *
 * @param {Object|string} backupBundle - Encrypted backup object or JSON string.
 * @param {string} passphrase - Backup passphrase.
 * @returns {Promise<{ restoredCount: number, totalKeys: number }>}
 */
export async function importVaultBackup(backupBundle, passphrase) {
  const keys = await decryptKeyVaultBackup(backupBundle, passphrase)

  const db = await openDB()
  let restoredCount = 0

  if (!db) {
    for (const record of keys) {
      if (record.file_id && record.jwk) {
        memoryStore.set(record.file_id, record)
        restoredCount++
      }
    }
    return { restoredCount, totalKeys: memoryStore.size }
  }

  for (const record of keys) {
    if (record.file_id && record.jwk) {
      await new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, 'readwrite')
        const store = tx.objectStore(STORE_NAME)
        const req = store.put(record)

        req.onsuccess = () => {
          restoredCount++
          resolve()
        }
        req.onerror = () => reject(req.error)
      })
    }
  }

  const allRecords = await getAllKeyRecords()
  return {
    restoredCount,
    totalKeys: allRecords.length,
  }
}

/**
 * Clear the entire vault (used for testing or explicit vault reset).
 */
export async function clearVault() {
  const db = await openDB()
  if (!db) {
    memoryStore.clear()
    return true
  }

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    const req = store.clear()

    req.onsuccess = () => resolve(true)
    req.onerror = () => reject(req.error)
  })
}
