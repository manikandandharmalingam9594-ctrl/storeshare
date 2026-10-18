/**
 * Store-Share Consumer API Client.
 *
 * Implements Phase 4: Integration with backend Phase 3 consumer endpoints.
 * All requests authenticate with the consumer's bearer JWT token.
 *
 * ZERO-KNOWLEDGE REMINDER:
 * Payloads sent to the coordinator contain ONLY ciphertext, IV, SHA-256 checksum,
 * and file size metadata. Decryption keys and plaintext files are NEVER transmitted.
 */

import { getToken, parseApiError } from './auth.js'

export const consumerApi = {
  /**
   * Upload an encrypted file payload (JSON containing base64 ciphertext).
   *
   * @param {Object} payload
   * @param {string} payload.filename - Name of the file.
   * @param {number} payload.file_size - Plaintext size in bytes.
   * @param {string} payload.iv_hex - 12-byte IV in 24-char hex.
   * @param {string} payload.sha256_hash - SHA-256 of the ciphertext.
   * @param {string} payload.ciphertext_base64 - Base64-encoded ciphertext + tag.
   * @returns {Promise<Object>} FileUploadResponse from backend.
   */
  async uploadEncryptedFile(payload) {
    const token = getToken()
    if (!token) {
      throw new Error('Consumer authentication required.')
    }

    const response = await fetch('/api/consumer/files/upload', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        filename: payload.filename,
        file_size: payload.file_size,
        iv_hex: payload.iv_hex,
        sha256_hash: payload.sha256_hash,
        ciphertext_base64: payload.ciphertext_base64,
      }),
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    return await response.json()
  },

  /**
   * List all active, non-deleted files owned by the authenticated consumer.
   *
   * @returns {Promise<Array<Object>>} List of FileStatusResponse objects.
   */
  async listFiles() {
    const token = getToken()
    if (!token) {
      throw new Error('Consumer authentication required.')
    }

    const response = await fetch('/api/consumer/files', {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    return await response.json()
  },

  /**
   * Get the live status of an individual file.
   *
   * @param {number} fileId
   * @returns {Promise<Object>} FileStatusResponse.
   */
  async getFileStatus(fileId) {
    const token = getToken()
    if (!token) {
      throw new Error('Consumer authentication required.')
    }

    const response = await fetch(`/api/consumer/files/${fileId}/status`, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    return await response.json()
  },

  /**
   * Download the encrypted ciphertext of a stored file.
   *
   * Triggers RETRIEVE_CHUNK on the provider node, pulls ciphertext into transient spool,
   * verifies SHA-256 on the backend, delivers binary octet-stream, and purges spool.
   *
   * @param {number} fileId
   * @returns {Promise<{
   *   ciphertextBuffer: ArrayBuffer,
   *   ivHex: string,
   *   sha256Checksum: string,
   *   plaintextSize: number,
   *   encryptedSize: number,
   *   filename: string
   * }>}
   */
  async downloadEncryptedFile(fileId) {
    const token = getToken()
    if (!token) {
      throw new Error('Consumer authentication required.')
    }

    const response = await fetch(`/api/consumer/files/${fileId}/download`, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    const ivHex = response.headers.get('X-IV-Hex') || ''
    const sha256Checksum = response.headers.get('X-SHA256-Checksum') || ''
    const plaintextSize = parseInt(response.headers.get('X-Plaintext-Size') || '0', 10)
    const encryptedSize = parseInt(response.headers.get('X-Encrypted-Size') || '0', 10)

    // Parse filename from Content-Disposition header if available
    let filename = `file_${fileId}.bin`
    const disposition = response.headers.get('Content-Disposition')
    if (disposition) {
      const match = disposition.match(/filename="?([^";]+)"?/i)
      if (match && match[1]) {
        // Strip .enc suffix if appended by backend
        filename = match[1].replace(/\.enc$/i, '')
      }
    }

    const ciphertextBuffer = await response.arrayBuffer()

    return {
      ciphertextBuffer,
      ivHex,
      sha256Checksum,
      plaintextSize,
      encryptedSize,
      filename,
    }
  },

  /**
   * Delete a stored file and its underlying physical chunk on the provider node.
   *
   * @param {number} fileId
   * @returns {Promise<Object>} FileDeleteResponse.
   */
  async deleteFile(fileId) {
    const token = getToken()
    if (!token) {
      throw new Error('Consumer authentication required.')
    }

    const response = await fetch(`/api/consumer/files/${fileId}`, {
      method: 'DELETE',
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    return await response.json()
  },
}
