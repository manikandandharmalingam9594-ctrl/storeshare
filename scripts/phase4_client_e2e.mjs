/**
 * End-to-end verification of Phase 4 JavaScript crypto and key management.
 * Calls frontend/src/services/crypto.js and frontend/src/services/keyStore.js directly.
 */

import {
  generateFileKey,
  generateIV,
  encryptFile,
  decryptFile,
  computeSHA256,
  bufferToHex,
} from '../frontend/src/services/crypto.js'
import {
  saveFileKey,
  getFileKey,
  hasFileKey,
  deleteFileKey,
  exportVaultBackup,
  importVaultBackup,
  clearVault,
} from '../frontend/src/services/keyStore.js'

async function run() {
  const args = process.argv.slice(2)
  const coordinatorUrl = args[0] || 'http://127.0.0.1:8001'
  const consumerToken = args[1]

  if (!consumerToken) {
    console.error('Error: consumerToken argument required.')
    process.exit(1)
  }

  console.log('[*] Phase 4 JS Client: Beginning real Web Crypto AES-256-GCM workflow...')

  // 1. Prepare original file content (real test document)
  const originalContent = `Store-Share Phase 4 End-to-End Test File\nGenerated: ${new Date().toISOString()}\nConfidential Zero-Knowledge Data.`
  const originalBytes = new TextEncoder().encode(originalContent)
  const originalSha256 = await computeSHA256(originalBytes)
  console.log(`[*] Original File: ${originalBytes.byteLength} bytes | SHA-256: ${originalSha256}`)

  // 2. Generate 256-bit AES-GCM key and 12-byte IV using Web Crypto API
  const key = await generateFileKey()
  const iv = generateIV()
  console.log(`[*] Generated Web Crypto AES-256-GCM key and 12-byte IV: ${bufferToHex(iv)}`)

  // 3. Encrypt file in JS client before transmission
  const encResult = await encryptFile(originalBytes, key, iv)
  console.log(`[*] Encrypted locally: ${encResult.plaintextSize} bytes plaintext -> ${encResult.ciphertextSize} bytes ciphertext (+16B auth tag)`)
  console.log(`    Ciphertext SHA-256: ${encResult.sha256Hex}`)

  // 4. Upload ciphertext via coordinator API
  const uploadPayload = {
    filename: 'phase4_verified_doc.txt',
    file_size: encResult.plaintextSize,
    iv_hex: encResult.ivHex,
    sha256_hash: encResult.sha256Hex,
    ciphertext_base64: encResult.ciphertextBase64,
  }

  const upRes = await fetch(`${coordinatorUrl}/api/consumer/files/upload`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${consumerToken}`,
    },
    body: JSON.stringify(uploadPayload),
  })

  if (!upRes.ok) {
    const errText = await upRes.text()
    throw new Error(`Upload failed (HTTP ${upRes.status}): ${errText}`)
  }

  const upData = await upRes.json()
  const fileId = upData.file_id
  console.log(`[OK] Upload accepted: File #${fileId} (Initial status: ${upData.status}, Provider Node #${upData.provider_node_id})`)

  // 5. Store key in local vault associated with fileId
  await saveFileKey(fileId, key, {
    filename: 'phase4_verified_doc.txt',
    sha256_hash: encResult.sha256Hex,
    plaintextSize: encResult.plaintextSize,
  })
  const keySaved = await hasFileKey(fileId)
  if (!keySaved) throw new Error('Failed to verify key in local vault!')
  console.log(`[OK] Decryption key saved in local client key vault for File #${fileId}`)

  // 6. Poll coordinator until status is STORED (physical provider confirmed)
  console.log(`[*] Polling coordinator for Provider storage confirmation...`)
  let stored = false
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 500))
    const stRes = await fetch(`${coordinatorUrl}/api/consumer/files/${fileId}/status`, {
      headers: { Authorization: `Bearer ${consumerToken}` },
    })
    if (stRes.ok) {
      const stData = await stRes.json()
      if (stData.status === 'STORED' && stData.is_stored === true) {
        stored = true
        console.log(`[OK] File #${fileId} status confirmed: STORED (Provider confirmed chunk storage)`)
        break
      }
    }
  }
  if (!stored) throw new Error('Timeout waiting for file to reach STORED status!')

  // Signal that file is stored so Python runner can inspect physical disk chunk before deletion
  console.log(`__STORED_FILE_ID__:${fileId}`)

  // Wait a moment for caller synchronization if needed
  await new Promise((r) => setTimeout(r, 500))

  // 7. Download ciphertext from coordinator
  console.log(`[*] Requesting ciphertext retrieval via GET /api/consumer/files/${fileId}/download...`)
  const dlRes = await fetch(`${coordinatorUrl}/api/consumer/files/${fileId}/download`, {
    headers: { Authorization: `Bearer ${consumerToken}` },
  })
  if (!dlRes.ok) {
    throw new Error(`Download failed: HTTP ${dlRes.status}`)
  }

  const downloadedCiphertext = await dlRes.arrayBuffer()
  const dlIv = dlRes.headers.get('x-iv-hex')
  const dlSha = dlRes.headers.get('x-sha256-checksum')
  console.log(`[OK] Retrieved ${downloadedCiphertext.byteLength} bytes ciphertext from provider node`)

  // 8. Verify pre-decryption SHA-256 checksum and decrypt with local vault key
  const retrievedKey = await getFileKey(fileId)
  if (!retrievedKey) throw new Error('Retrieved key was null!')

  const decryptedBuffer = await decryptFile(
    downloadedCiphertext,
    retrievedKey,
    dlIv,
    dlSha
  )
  const decryptedBytes = new Uint8Array(decryptedBuffer)
  const decryptedSha256 = await computeSHA256(decryptedBytes)

  console.log(`[*] Decrypted file SHA-256: ${decryptedSha256}`)
  if (decryptedSha256 !== originalSha256) {
    throw new Error(`Integrity Mismatch: Decrypted SHA-256 (${decryptedSha256}) != Original SHA-256 (${originalSha256})!`)
  }
  const decryptedText = new TextDecoder().decode(decryptedBytes)
  if (decryptedText !== originalContent) {
    throw new Error('Decrypted string does not match original file content!')
  }
  console.log(`[OK] Decryption verified: 100% byte fidelity with original client plaintext!`)

  // 9. Key backup export / clear / import round-trip
  console.log(`[*] Testing password-protected key backup export & import...`)
  const passphrase = 'EndToEndBackupPassphrase123!'
  const backupBundle = await exportVaultBackup(passphrase)
  await clearVault()
  if (await hasFileKey(fileId)) throw new Error('Vault clear failed!')
  console.log(`[OK] Vault cleared: Key is absent (decryption impossible without backup)`)

  const restoreResult = await importVaultBackup(backupBundle, passphrase)
  console.log(`[OK] Vault restored from backup: ${restoreResult.restoredCount} keys recovered`)
  const restoredKey = await getFileKey(fileId)
  const testDecrypt = await decryptFile(downloadedCiphertext, restoredKey, dlIv, dlSha)
  if (new TextDecoder().decode(testDecrypt) !== originalContent) {
    throw new Error('Decryption with restored backup key failed!')
  }
  console.log(`[OK] Restored key successfully decrypted ciphertext!`)

  // 10. Delete file
  console.log(`[*] Deleting file via DELETE /api/consumer/files/${fileId}...`)
  const delRes = await fetch(`${coordinatorUrl}/api/consumer/files/${fileId}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${consumerToken}` },
  })
  if (!delRes.ok) throw new Error(`Delete failed: HTTP ${delRes.status}`)
  await deleteFileKey(fileId)
  console.log(`[OK] File #${fileId} deleted and local key removed.`)

  console.log(`__COMPLETED_FILE_ID__:${fileId}`)
}

run().catch((err) => {
  console.error('[FAIL]', err)
  process.exit(1)
})
