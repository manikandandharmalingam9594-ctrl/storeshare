import test from 'node:test'
import assert from 'node:assert/strict'
import {
  MAX_FILE_SIZE_BYTES,
  bufferToHex,
  hexToBuffer,
  bufferToBase64,
  base64ToBuffer,
  computeSHA256,
  generateFileKey,
  generateIV,
  exportKeyToJWK,
  importKeyFromJWK,
  encryptFile,
  decryptFile,
  encryptKeyVaultBackup,
  decryptKeyVaultBackup,
} from '../src/services/crypto.js'
import {
  saveFileKey,
  getFileKey,
  hasFileKey,
  deleteFileKey,
  exportVaultBackup,
  importVaultBackup,
  clearVault,
} from '../src/services/keyStore.js'

test('Crypto utilities: bufferToHex and hexToBuffer round trip', () => {
  const original = new Uint8Array([0, 1, 15, 16, 255, 128, 64])
  const hex = bufferToHex(original)
  assert.equal(hex, '00010f10ff8040')
  const recovered = hexToBuffer(hex)
  assert.deepEqual(recovered, original)
})

test('Crypto utilities: bufferToBase64 and base64ToBuffer round trip', () => {
  const original = new TextEncoder().encode('Hello Store-Share Browser Encryption!')
  const base64 = bufferToBase64(original)
  const recovered = base64ToBuffer(base64)
  assert.deepEqual(recovered, original)
})

test('Crypto utilities: SHA-256 computation', async () => {
  const data = new TextEncoder().encode('Store-Share Test Hash')
  const sha = await computeSHA256(data)
  assert.equal(typeof sha, 'string')
  assert.equal(sha.length, 64)
  // Verify deterministic output
  const sha2 = await computeSHA256(data)
  assert.equal(sha, sha2)
})

test('AES-256-GCM: Key and IV generation', async () => {
  const key = await generateFileKey()
  assert.ok(key)
  assert.equal(key.algorithm.name, 'AES-GCM')
  assert.equal(key.algorithm.length, 256)
  assert.equal(key.extractable, true)

  const iv1 = generateIV()
  const iv2 = generateIV()
  assert.equal(iv1.length, 12)
  assert.equal(iv2.length, 12)
  // Randomness: two consecutive IVs should never match
  assert.notDeepEqual(iv1, iv2)
})

test('AES-256-GCM: File encryption and decryption round trip', async () => {
  const originalText = 'Store-Share Confidential Document Contents: Phase 4 Verified.'
  const plaintext = new TextEncoder().encode(originalText)
  const key = await generateFileKey()

  // 1. Encrypt
  const encResult = await encryptFile(plaintext, key)
  assert.ok(encResult.ciphertextBuffer)
  assert.ok(encResult.ciphertextBase64)
  assert.equal(encResult.plaintextSize, plaintext.byteLength)
  // AES-GCM ciphertext length must be exactly plaintextSize + 16-byte tag
  assert.equal(encResult.ciphertextSize, plaintext.byteLength + 16)
  assert.equal(encResult.ivHex.length, 24)
  assert.equal(encResult.sha256Hex.length, 64)

  // 2. Decrypt with correct key and IV
  const decryptedBuffer = await decryptFile(
    encResult.ciphertextBuffer,
    key,
    encResult.ivHex,
    encResult.sha256Hex
  )
  const decryptedText = new TextDecoder().decode(decryptedBuffer)
  assert.equal(decryptedText, originalText)
})

test('AES-256-GCM: Tampered ciphertext is rejected by authentication tag', async () => {
  const plaintext = new TextEncoder().encode('Integrity critical message')
  const key = await generateFileKey()
  const encResult = await encryptFile(plaintext, key)

  // Tamper with 1 byte in the ciphertext
  const tamperedBytes = new Uint8Array(encResult.ciphertextBuffer)
  tamperedBytes[0] ^= 0xff

  // Check that decryptFile throws authentication error (even without expected sha256)
  await assert.rejects(
    async () => {
      await decryptFile(tamperedBytes, key, encResult.ivHex)
    },
    /Decryption failed: cryptographic authentication tag mismatch/
  )
})

test('AES-256-GCM: Pre-decryption SHA-256 integrity check catches modified payload', async () => {
  const plaintext = new TextEncoder().encode('Integrity check verification')
  const key = await generateFileKey()
  const encResult = await encryptFile(plaintext, key)

  const tamperedBytes = new Uint8Array(encResult.ciphertextBuffer)
  tamperedBytes[0] ^= 0x01

  await assert.rejects(
    async () => {
      await decryptFile(tamperedBytes, key, encResult.ivHex, encResult.sha256Hex)
    },
    /Ciphertext SHA-256 integrity verification failed/
  )
})

test('AES-256-GCM: Decryption with wrong key is rejected', async () => {
  const plaintext = new TextEncoder().encode('Secret data')
  const keyA = await generateFileKey()
  const keyB = await generateFileKey()

  const encResult = await encryptFile(plaintext, keyA)

  await assert.rejects(
    async () => {
      await decryptFile(encResult.ciphertextBuffer, keyB, encResult.ivHex, encResult.sha256Hex)
    },
    /Decryption failed: cryptographic authentication tag mismatch/
  )
})

test('AES-256-GCM: File size constraints enforcement', async () => {
  const key = await generateFileKey()

  // 0-byte file rejected
  await assert.rejects(
    async () => {
      await encryptFile(new Uint8Array(0), key)
    },
    /File size must be greater than 0 bytes/
  )

  // File > 5 MB rejected
  const oversized = new Uint8Array(MAX_FILE_SIZE_BYTES + 1)
  await assert.rejects(
    async () => {
      await encryptFile(oversized, key)
    },
    /exceeds maximum allowable limit of 5 MB/
  )
})

test('Key Store: Local vault save, retrieve, has, and delete', async () => {
  await clearVault()
  const fileId = 101
  const key = await generateFileKey()

  // Initially hasFileKey is false
  assert.equal(await hasFileKey(fileId), false)
  assert.equal(await getFileKey(fileId), null)

  // Save key
  await saveFileKey(fileId, key, {
    filename: 'test-doc.txt',
    sha256_hash: 'a'.repeat(64),
    plaintextSize: 128,
  })

  // Key now exists
  assert.equal(await hasFileKey(fileId), true)
  const retrievedKey = await getFileKey(fileId)
  assert.ok(retrievedKey)
  assert.equal(retrievedKey.algorithm.name, 'AES-GCM')

  // Verify retrieved key works for decryption
  const plaintext = new TextEncoder().encode('Round-trip testing with retrieved key')
  const enc = await encryptFile(plaintext, key)
  const decrypted = await decryptFile(enc.ciphertextBuffer, retrievedKey, enc.ivHex)
  assert.equal(new TextDecoder().decode(decrypted), 'Round-trip testing with retrieved key')

  // Delete key
  await deleteFileKey(fileId)
  assert.equal(await hasFileKey(fileId), false)
  assert.equal(await getFileKey(fileId), null)
})

test('Key Vault Backup: Password-protected export and recovery workflow', async () => {
  await clearVault()

  // Setup: store 2 file keys in the vault
  const key1 = await generateFileKey()
  const key2 = await generateFileKey()
  await saveFileKey(201, key1, { filename: 'file1.pdf', sha256_hash: '1'.repeat(64) })
  await saveFileKey(202, key2, { filename: 'file2.png', sha256_hash: '2'.repeat(64) })

  const originalPlaintext = new TextEncoder().encode('Document encrypted with Key 1')
  const enc = await encryptFile(originalPlaintext, key1)

  // 1. Export vault backup protected by passphrase
  const passphrase = 'SuperSecretBackupPassword123!'
  const backupBundle = await exportVaultBackup(passphrase)
  assert.equal(backupBundle.app, 'Store-Share')
  assert.equal(backupBundle.type, 'storeshare-key-vault-backup')
  assert.equal(backupBundle.kdf.algorithm, 'PBKDF2')
  assert.equal(backupBundle.kdf.iterations, 100000)
  assert.ok(backupBundle.ciphertext_base64)

  // 2. Clear vault to simulate new browser, cleared cache, or new device
  await clearVault()
  assert.equal(await hasFileKey(201), false)
  assert.equal(await hasFileKey(202), false)

  // 3. Import with WRONG passphrase must fail
  await assert.rejects(
    async () => {
      await importVaultBackup(backupBundle, 'WrongPassword456!')
    },
    /Failed to decrypt key backup: incorrect passphrase/
  )

  // 4. Import with CORRECT passphrase succeeds
  const restoreResult = await importVaultBackup(backupBundle, passphrase)
  assert.equal(restoreResult.restoredCount, 2)

  // 5. Verify restored key can decrypt original ciphertext
  const restoredKey1 = await getFileKey(201)
  assert.ok(restoredKey1)
  const decryptedBuffer = await decryptFile(enc.ciphertextBuffer, restoredKey1, enc.ivHex)
  assert.equal(new TextDecoder().decode(decryptedBuffer), 'Document encrypted with Key 1')
})
