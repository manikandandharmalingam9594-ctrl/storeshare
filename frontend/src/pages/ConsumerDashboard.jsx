import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import {
  MAX_FILE_SIZE_BYTES,
  decryptFile,
  encryptFile,
  generateFileKey,
  generateIV,
} from '../services/crypto'
import {
  deleteFileKey,
  exportVaultBackup,
  getFileKey,
  hasFileKey,
  importVaultBackup,
  listVaultKeyMetadata,
  saveFileKey,
} from '../services/keyStore'
import { consumerApi } from '../services/consumer'

export default function ConsumerDashboard() {
  const { user } = useAuth()

  // File list & vault state
  const [files, setFiles] = useState([])
  const [keyAvailability, setKeyAvailability] = useState({}) // { [file_id]: boolean }
  const [loadingFiles, setLoadingFiles] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [vaultKeyCount, setVaultKeyCount] = useState(0)

  // Upload state
  const [selectedFile, setSelectedFile] = useState(null)
  const [isDragging, setIsDragging] = useState(false)
  const [uploadStep, setUploadStep] = useState(null) // null | 1 | 2 | 3 | 4 | 5 | 6 | 'done' | 'error'
  const [uploadError, setUploadError] = useState(null)
  const [uploadSuccess, setUploadSuccess] = useState(null)
  const fileInputRef = useRef(null)

  // File action states (downloading / deleting)
  const [actionLoading, setActionLoading] = useState({}) // { [file_id]: 'downloading' | 'deleting' }
  const [actionMessage, setActionMessage] = useState(null)
  const [actionError, setActionError] = useState(null)

  // Backup Export / Import Modals
  const [showExportModal, setShowExportModal] = useState(false)
  const [exportPassphrase, setExportPassphrase] = useState('')
  const [exportPassphraseConfirm, setExportPassphraseConfirm] = useState('')
  const [exportLoading, setExportLoading] = useState(false)
  const [exportError, setExportError] = useState(null)
  const [exportSuccess, setExportSuccess] = useState(null)

  const [showImportModal, setShowImportModal] = useState(false)
  const [importFile, setImportFile] = useState(null)
  const [importPassphrase, setImportPassphrase] = useState('')
  const [importLoading, setImportLoading] = useState(false)
  const [importError, setImportError] = useState(null)
  const [importSuccess, setImportSuccess] = useState(null)

  // Check provider connectivity / 503 warning
  const [providerOfflineWarning, setProviderOfflineWarning] = useState(false)

  // Load consumer files and sync local key availability
  const loadFilesAndKeys = async (isInitial = false) => {
    if (isInitial) setLoadingFiles(true)
    else setRefreshing(true)

    try {
      const fileList = await consumerApi.listFiles()
      setFiles(fileList)

      // Query IndexedDB key availability for each file
      const availabilityMap = {}
      for (const f of fileList) {
        availabilityMap[f.file_id] = await hasFileKey(f.file_id)
      }
      setKeyAvailability(availabilityMap)

      // Get count of keys in local vault
      const vaultMeta = await listVaultKeyMetadata()
      setVaultKeyCount(vaultMeta.length)
      setProviderOfflineWarning(false)
    } catch (err) {
      console.error('Failed to load consumer files:', err)
      if (err.status === 503 || (err.message && err.message.includes('provider'))) {
        setProviderOfflineWarning(true)
      }
    } finally {
      if (isInitial) setLoadingFiles(false)
      setRefreshing(false)
    }
  }

  // Initial load
  useEffect(() => {
    loadFilesAndKeys(true)
  }, [])

  // Auto-polling when any file is in UPLOADING status
  useEffect(() => {
    const hasUploading = files.some((f) => f.status === 'UPLOADING')
    if (!hasUploading) return

    const pollInterval = setInterval(() => {
      loadFilesAndKeys(false)
    }, 2500)

    return () => clearInterval(pollInterval)
  }, [files])

  // Handle file selection
  const handleFileSelect = (file) => {
    setUploadError(null)
    setUploadSuccess(null)
    setUploadStep(null)

    if (!file) return

    if (file.size === 0) {
      setUploadError('Cannot upload empty file (0 bytes).')
      setSelectedFile(null)
      return
    }

    if (file.size > MAX_FILE_SIZE_BYTES) {
      setUploadError(
        `File size (${(file.size / (1024 * 1024)).toFixed(2)} MB) exceeds maximum allowable limit of 5.00 MB.`
      )
      setSelectedFile(null)
      return
    }

    setSelectedFile(file)
  }

  // Handle Drag & Drop
  const handleDragOver = (e) => {
    e.preventDefault()
    setIsDragging(true)
  }

  const handleDragLeave = () => {
    setIsDragging(false)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setIsDragging(false)
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFileSelect(e.dataTransfer.files[0])
    }
  }

  // Perform Browser-Side Encryption & Upload Workflow
  const handleEncryptAndUpload = async () => {
    if (!selectedFile) return

    setUploadError(null)
    setUploadSuccess(null)
    setProviderOfflineWarning(false)

    try {
      // Step 1: Generate cryptographic 256-bit AES key & 12-byte IV
      setUploadStep(1)
      const key = await generateFileKey()
      const iv = generateIV()

      // Step 2: Encrypt in-memory with Web Crypto AES-256-GCM
      setUploadStep(2)
      const encResult = await encryptFile(selectedFile, key, iv)

      // Step 3: Verify SHA-256 checksum of ciphertext
      setUploadStep(3)
      // Checksum is already computed inside encryptFile as encResult.sha256Hex

      // Step 4: Upload ciphertext payload to Store-Share coordinator
      setUploadStep(4)
      const uploadResponse = await consumerApi.uploadEncryptedFile({
        filename: selectedFile.name,
        file_size: encResult.plaintextSize,
        iv_hex: encResult.ivHex,
        sha256_hash: encResult.sha256Hex,
        ciphertext_base64: encResult.ciphertextBase64,
      })

      // Step 5: Save key into local IndexedDB vault associated with the backend file_id
      setUploadStep(5)
      await saveFileKey(uploadResponse.file_id, key, {
        filename: selectedFile.name,
        sha256_hash: encResult.sha256Hex,
        plaintextSize: encResult.plaintextSize,
      })

      // Step 6: Polling provider node confirmation
      setUploadStep(6)
      setUploadSuccess(
        `Encrypted '${selectedFile.name}' (${encResult.plaintextSize} bytes plaintext -> ${encResult.ciphertextSize} bytes ciphertext). ` +
        `Decryption key secured in browser vault. Coordinator queued physical chunk storage on Provider Node #${uploadResponse.provider_node_id}.`
      )

      // Reset selection and refresh file list
      setSelectedFile(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
      await loadFilesAndKeys(false)
    } catch (err) {
      console.error('Upload failed:', err)
      setUploadStep('error')
      let msg = err.message || 'File encryption or upload failed.'
      if (err.status === 503) {
        setProviderOfflineWarning(true)
        msg = 'No online storage provider node with sufficient capacity is currently available. Start a provider node or try again later.'
      }
      setUploadError(msg)
    }
  }

  // Handle Download, Checksum Integrity Verification & Browser Decryption
  const handleDownloadAndDecrypt = async (file) => {
    setActionError(null)
    setActionMessage(null)
    setActionLoading((prev) => ({ ...prev, [file.file_id]: 'downloading' }))

    try {
      // 1. Check if we have the local key in IndexedDB
      const key = await getFileKey(file.file_id, false)
      if (!key) {
        throw new Error(
          `Decryption key for '${file.filename}' is missing from this browser's local vault. ` +
          `If you uploaded this file from another machine or cleared your browser data, please restore your key vault using 'Import Key Backup'.`
        )
      }

      // 2. Fetch ciphertext from coordinator (which retrieves it from provider node)
      setActionMessage(`Requesting encrypted chunk from provider node for '${file.filename}'...`)
      const downloadResult = await consumerApi.downloadEncryptedFile(file.file_id)

      // 3. Verify SHA-256 checksum against file record
      setActionMessage(`Verifying SHA-256 integrity of downloaded ciphertext...`)
      const ivHex = downloadResult.ivHex || file.iv_hex
      const expectedSha = downloadResult.sha256Checksum || file.sha256_hash

      // 4. Decrypt in consumer browser using Web Crypto AES-256-GCM
      setActionMessage(`Decrypting ciphertext in browser with local vault key...`)
      const decryptedBuffer = await decryptFile(
        downloadResult.ciphertextBuffer,
        key,
        ivHex,
        expectedSha
      )

      // 5. Verify byte size matches expected plaintext size
      if (file.file_size && decryptedBuffer.byteLength !== file.file_size) {
        throw new Error(
          `Decrypted size mismatch: expected ${file.file_size} bytes, got ${decryptedBuffer.byteLength} bytes.`
        )
      }

      // 6. Trigger native browser file save
      const blob = new Blob([decryptedBuffer])
      const downloadUrl = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = downloadUrl
      a.download = file.filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(downloadUrl)

      setActionMessage(
        `Successfully downloaded and decrypted '${file.filename}' (${decryptedBuffer.byteLength} bytes). SHA-256 checksum and AES-GCM authentication verified.`
      )
    } catch (err) {
      console.error('Download/Decryption failed:', err)
      setActionError(err.message || 'Failed to download or decrypt file.')
    } finally {
      setActionLoading((prev) => ({ ...prev, [file.file_id]: null }))
    }
  }

  // Handle File Deletion
  const handleDeleteFile = async (file) => {
    if (!window.confirm(`Are you sure you want to permanently delete '${file.filename}' and remove its physical chunk from the provider node?`)) {
      return
    }

    setActionError(null)
    setActionMessage(null)
    setActionLoading((prev) => ({ ...prev, [file.file_id]: 'deleting' }))

    try {
      // 1. Request coordinator to delete chunk on provider node and release quota
      await consumerApi.deleteFile(file.file_id)

      // 2. Remove key from local IndexedDB vault
      await deleteFileKey(file.file_id)

      setActionMessage(`File '${file.filename}' deleted and physical chunk safely removed from provider node.`)
      await loadFilesAndKeys(false)
    } catch (err) {
      console.error('Deletion failed:', err)
      setActionError(err.message || 'Failed to delete file.')
    } finally {
      setActionLoading((prev) => ({ ...prev, [file.file_id]: null }))
    }
  }

  // Handle Export Encrypted Backup
  const handleExportBackup = async (e) => {
    e.preventDefault()
    setExportError(null)
    setExportSuccess(null)

    if (exportPassphrase.length < 8) {
      setExportError('Passphrase must be at least 8 characters long.')
      return
    }

    if (exportPassphrase !== exportPassphraseConfirm) {
      setExportError('Passphrases do not match.')
      return
    }

    setExportLoading(true)
    try {
      const backupBundle = await exportVaultBackup(exportPassphrase)
      const bundleJson = JSON.stringify(backupBundle, null, 2)
      const blob = new Blob([bundleJson], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      const dateStr = new Date().toISOString().split('T')[0]
      a.href = url
      a.download = `storeshare-keys-${dateStr}.storeshare-keys`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)

      setExportSuccess('Encrypted key backup generated and downloaded successfully.')
      setExportPassphrase('')
      setExportPassphraseConfirm('')
    } catch (err) {
      setExportError(err.message || 'Failed to export key backup.')
    } finally {
      setExportLoading(false)
    }
  }

  // Handle Import Encrypted Backup
  const handleImportBackup = async (e) => {
    e.preventDefault()
    setImportError(null)
    setImportSuccess(null)

    if (!importFile) {
      setImportError('Please select a .storeshare-keys backup file.')
      return
    }

    if (!importPassphrase) {
      setImportError('Please enter the backup passphrase.')
      return
    }

    setImportLoading(true)
    try {
      const fileText = await importFile.text()
      const bundle = JSON.parse(fileText)
      const result = await importVaultBackup(bundle, importPassphrase)

      setImportSuccess(
        `Successfully decrypted and restored ${result.restoredCount} keys into local vault (total ${result.totalKeys} keys active).`
      )
      setImportPassphrase('')
      setImportFile(null)
      await loadFilesAndKeys(false)
    } catch (err) {
      setImportError(err.message || 'Failed to import key backup. Verify passphrase or file format.')
    } finally {
      setImportLoading(false)
    }
  }

  const formatBytes = (bytes) => {
    if (!bytes || bytes === 0) return '0 B'
    const k = 1024
    const sizes = ['B', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`
  }

  const formatDate = (isoStr) => {
    if (!isoStr) return 'N/A'
    try {
      return new Date(isoStr).toLocaleString()
    } catch {
      return isoStr
    }
  }

  return (
    <div className="dashboard-page">
      {/* Page Header */}
      <div className="dashboard-header-block">
        <div className="role-pill role-consumer">
          <span className="role-dot" />
          <span>Consumer Dashboard</span>
        </div>
        <h2 className="hero-title" style={{ marginTop: '12px', fontSize: '2.4rem' }}>
          Consumer Encrypted Vault
        </h2>
        <p className="hero-subtitle">
          End-to-end encrypted storage powered by AES-256-GCM. Files are encrypted in your browser before upload,
          stored across distributed provider nodes, and decrypted exclusively on your client.
        </p>
      </div>

      {/* Zero-Knowledge Security Notice */}
      <div className="zk-alert-box">
        <div className="zk-alert-icon">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
            <path d="M7 11V7a5 5 0 0 1 10 0v4" />
          </svg>
        </div>
        <div>
          <div className="zk-alert-title">Client-Side Zero-Knowledge Encryption Active</div>
          <div className="zk-alert-body">
            Store-Share enforces true zero-knowledge privacy. Encryption keys are generated and held{' '}
            <strong>strictly in your browser's local vault (IndexedDB)</strong> and are never transmitted to the coordinator or storage providers.
            If you clear browser data or switch devices without exporting a password-protected key backup (<code>.storeshare-keys</code>),
            your encrypted files will be <strong>permanently unrecoverable</strong>.
          </div>
        </div>
      </div>

      {/* Offline Provider Alert Banner */}
      {providerOfflineWarning && (
        <div className="alert-box error" style={{ marginBottom: '24px' }}>
          <div className="alert-title">No Storage Provider Node Available</div>
          <div className="alert-message">
            There are currently no online storage provider nodes connected with available quota. Uploads will fail until a provider node
            launches its storage agent. You can start a provider daemon via the Provider Dashboard on another port or terminal.
          </div>
        </div>
      )}

      {/* Action Feedback Messages */}
      {actionMessage && (
        <div className="alert-box success" style={{ marginBottom: '24px' }}>
          <div className="alert-message">{actionMessage}</div>
        </div>
      )}
      {actionError && (
        <div className="alert-box error" style={{ marginBottom: '24px' }}>
          <div className="alert-message">{actionError}</div>
        </div>
      )}

      {/* Top Stats Grid */}
      <div className="dashboard-grid" style={{ marginBottom: '28px' }}>
        {/* Account Profile Card */}
        <div className="glass-card">
          <div className="card-header">
            <div className="card-title-group">
              <div className="card-icon-box">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                  <circle cx="12" cy="7" r="4" />
                </svg>
              </div>
              <h3 className="card-title">Consumer Account</h3>
            </div>
            <div className="status-badge healthy">
              <span className="status-dot" />
              <span>Verified Session</span>
            </div>
          </div>

          <div className="telemetry-box">
            <div className="telemetry-row">
              <span className="telemetry-label">User ID:</span>
              <span className="telemetry-value">#{user?.id}</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Email:</span>
              <span className="telemetry-value">{user?.email}</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Active Stored Files:</span>
              <span className="telemetry-value success">{files.length} Files</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Max File Size:</span>
              <span className="telemetry-value">5.00 MB</span>
            </div>
          </div>
        </div>

        {/* Local Key Vault Management Card */}
        <div className="glass-card">
          <div className="card-header">
            <div className="card-title-group">
              <div className="card-icon-box">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 2l-2 2m-1-1l-3 3m2-2l-2 2m-1-1l-3 3m2-2l-2 2M3 21l9-9" />
                  <circle cx="7.5" cy="16.5" r="3.5" />
                </svg>
              </div>
              <h3 className="card-title">Local Key Vault</h3>
            </div>
            <div className="status-badge healthy">
              <span className="status-dot" />
              <span>IndexedDB Active</span>
            </div>
          </div>

          <p className="card-desc">
            Manages AES-256 cryptographic keys stored inside this browser instance.
          </p>

          <div className="telemetry-box" style={{ marginBottom: '16px' }}>
            <div className="telemetry-row">
              <span className="telemetry-label">Local Keys in Vault:</span>
              <span className="telemetry-value success">{vaultKeyCount} Decryption Keys</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Storage Backend:</span>
              <span className="telemetry-value">IndexedDB (Non-extractable runtime)</span>
            </div>
            <div className="telemetry-row">
              <span className="telemetry-label">Cipher:</span>
              <span className="telemetry-value">AES-256-GCM + PBKDF2</span>
            </div>
          </div>

          <div className="vault-actions-row">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => {
                setShowExportModal(true)
                setExportError(null)
                setExportSuccess(null)
              }}
              style={{ fontSize: '0.82rem', padding: '8px 14px' }}
            >
              Export Encrypted Backup
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => {
                setShowImportModal(true)
                setImportError(null)
                setImportSuccess(null)
              }}
              style={{ fontSize: '0.82rem', padding: '8px 14px' }}
            >
              Import Key Backup
            </button>
          </div>
        </div>
      </div>

      {/* File Upload Section */}
      <div className="glass-card" style={{ marginBottom: '32px' }}>
        <div className="card-header">
          <div className="card-title-group">
            <div className="card-icon-box">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
            </div>
            <h3 className="card-title">Client-Side Encrypted Upload</h3>
          </div>
          <span className="spec-badge">Limit: 5 MB</span>
        </div>

        <p className="card-desc">
          Select a file from your computer. Store-Share generates a random 256-bit AES key, encrypts the file locally with AES-256-GCM,
          saves the key into your IndexedDB vault, and uploads only the encrypted ciphertext to the storage mesh.
        </p>

        {/* Drop Zone */}
        <div
          className={`drop-zone ${isDragging ? 'dragover' : ''}`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            className="file-hidden-input"
            onChange={(e) => {
              if (e.target.files && e.target.files.length > 0) {
                handleFileSelect(e.target.files[0])
              }
            }}
          />
          <div className="drop-zone-icon">
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242" />
              <path d="M12 12v9" />
              <path d="m16 16-4-4-4 4" />
            </svg>
          </div>
          <div className="drop-zone-title">
            {selectedFile ? selectedFile.name : 'Click to select or drag and drop a file'}
          </div>
          <div className="drop-zone-hint">
            Maximum file size: 5 MB (AES-256-GCM browser encryption)
          </div>
        </div>

        {/* Selected File Details */}
        {selectedFile && (
          <div className="selected-file-chip">
            <div className="file-meta-primary">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
              <span>{selectedFile.name}</span>
              <span className="file-meta-size">({formatBytes(selectedFile.size)})</span>
            </div>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleEncryptAndUpload}
              disabled={uploadStep && uploadStep !== 'error'}
              style={{ padding: '8px 18px', fontSize: '0.85rem' }}
            >
              {uploadStep && uploadStep !== 'error' ? 'Processing...' : 'Encrypt & Upload to Mesh'}
            </button>
          </div>
        )}

        {/* Upload Pipeline Stepper */}
        {uploadStep && (
          <div className="pipeline-stepper">
            <div className="pipeline-stepper-title">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
              </svg>
              <span>Browser Encryption & Upload Pipeline</span>
            </div>

            <div className={`step-row ${uploadStep === 1 ? 'step-active' : uploadStep > 1 ? 'step-done' : ''}`}>
              <span className="step-dot" />
              <span>Step 1: Generate cryptographically random 256-bit AES-GCM key and 12-byte IV</span>
            </div>

            <div className={`step-row ${uploadStep === 2 ? 'step-active' : uploadStep > 2 ? 'step-done' : ''}`}>
              <span className="step-dot" />
              <span>Step 2: Client-side Web Crypto AES-256-GCM in-memory encryption (+16-byte auth tag)</span>
            </div>

            <div className={`step-row ${uploadStep === 3 ? 'step-active' : uploadStep > 3 ? 'step-done' : ''}`}>
              <span className="step-dot" />
              <span>Step 3: Compute SHA-256 integrity checksum over ciphertext payload</span>
            </div>

            <div className={`step-row ${uploadStep === 4 ? 'step-active' : uploadStep > 4 ? 'step-done' : ''}`}>
              <span className="step-dot" />
              <span>Step 4: Transmit ciphertext payload, IV, and checksum to coordinator</span>
            </div>

            <div className={`step-row ${uploadStep === 5 ? 'step-active' : uploadStep > 5 ? 'step-done' : ''}`}>
              <span className="step-dot" />
              <span>Step 5: Store encryption key in local browser IndexedDB key vault</span>
            </div>

            <div className={`step-row ${uploadStep === 6 ? 'step-active' : uploadStep === 'done' ? 'step-done' : ''}`}>
              <span className="step-dot" />
              <span>Step 6: Coordinator queues physical chunk transfer to Provider node (STORE_CHUNK)</span>
            </div>
          </div>
        )}

        {uploadSuccess && (
          <div className="alert-box success" style={{ marginTop: '16px' }}>
            <div className="alert-title">Upload & Encryption Complete</div>
            <div className="alert-message">{uploadSuccess}</div>
          </div>
        )}

        {uploadError && (
          <div className="alert-box error" style={{ marginTop: '16px' }}>
            <div className="alert-title">Upload Failed</div>
            <div className="alert-message">{uploadError}</div>
          </div>
        )}
      </div>

      {/* My Files Section */}
      <div className="glass-card">
        <div className="card-header">
          <div className="card-title-group">
            <div className="card-icon-box">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
                <polyline points="13 2 13 9 20 9" />
              </svg>
            </div>
            <h3 className="card-title">My Stored Encrypted Files</h3>
          </div>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => loadFilesAndKeys(false)}
            disabled={refreshing}
            style={{ fontSize: '0.8rem', padding: '6px 12px' }}
          >
            {refreshing ? 'Refreshing...' : 'Refresh List'}
          </button>
        </div>

        <p className="card-desc">
          Files currently stored in the Store-Share network. Status changes automatically as provider nodes confirm physical chunk storage.
        </p>

        {loadingFiles ? (
          <div className="vault-empty-state">
            <div className="pulse-dot" style={{ margin: '0 auto 12px', width: '12px', height: '12px' }} />
            <div className="vault-empty-desc">Loading encrypted files from coordinator...</div>
          </div>
        ) : files.length === 0 ? (
          <div className="vault-empty-state">
            <div className="vault-empty-icon">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
              </svg>
            </div>
            <div className="vault-empty-title">No Encrypted Files Found</div>
            <div className="vault-empty-desc">
              Your vault is currently empty. Use the upload area above to encrypt and upload your first file to the distributed storage network.
            </div>
          </div>
        ) : (
          <div className="files-table-wrapper">
            <table className="files-table">
              <thead>
                <tr>
                  <th>Filename</th>
                  <th>Size</th>
                  <th>Created</th>
                  <th>Network Status</th>
                  <th>Key Vault Status</th>
                  <th style={{ textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {files.map((file) => {
                  const hasKey = keyAvailability[file.file_id]
                  const isLoading = !!actionLoading[file.file_id]

                  return (
                    <tr key={file.file_id}>
                      {/* Filename */}
                      <td>
                        <div className="table-filename-group">
                          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" strokeWidth="2">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                            <polyline points="14 2 14 8 20 8" />
                          </svg>
                          <div>
                            <div className="table-filename-text">{file.filename}</div>
                            <div className="table-filesize-sub">
                              SHA: {file.sha256_hash.substring(0, 10)}...{file.sha256_hash.substring(file.sha256_hash.length - 6)}
                            </div>
                          </div>
                        </div>
                      </td>

                      {/* Size */}
                      <td>
                        <div style={{ fontWeight: '600' }}>{formatBytes(file.file_size)}</div>
                        <div className="table-filesize-sub">Enc: {formatBytes(file.encrypted_size)}</div>
                      </td>

                      {/* Created Date */}
                      <td style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                        {formatDate(file.created_at)}
                      </td>

                      {/* Status */}
                      <td>
                        {file.status === 'STORED' ? (
                          <div className="status-badge healthy">
                            <span className="status-dot" />
                            <span>STORED</span>
                          </div>
                        ) : file.status === 'UPLOADING' ? (
                          <div className="status-badge busy">
                            <span className="status-dot" />
                            <span>UPLOADING (Mesh Handoff)</span>
                          </div>
                        ) : (
                          <div className="status-badge error">
                            <span className="status-dot" />
                            <span>{file.status}</span>
                          </div>
                        )}
                      </td>

                      {/* Key Status */}
                      <td>
                        {hasKey ? (
                          <span className="key-pill key-ready">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                              <polyline points="20 6 9 17 4 12" />
                            </svg>
                            <span>Key Active</span>
                          </span>
                        ) : (
                          <span className="key-pill key-missing" title="Encryption key missing from this browser vault. Import key backup to decrypt.">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                              <circle cx="12" cy="12" r="10" />
                              <line x1="12" y1="8" x2="12" y2="12" />
                              <line x1="12" y1="16" x2="12.01" y2="16" />
                            </svg>
                            <span>Key Missing</span>
                          </span>
                        )}
                      </td>

                      {/* Actions */}
                      <td style={{ textAlign: 'right' }}>
                        <div style={{ display: 'inline-flex', gap: '8px' }}>
                          <button
                            type="button"
                            className="btn-action-icon btn-download"
                            onClick={() => handleDownloadAndDecrypt(file)}
                            disabled={file.status !== 'STORED' || isLoading}
                            title={
                              file.status !== 'STORED'
                                ? 'File is not yet fully stored on provider node'
                                : !hasKey
                                ? 'Key missing in local vault — import backup first'
                                : 'Download ciphertext, verify SHA-256, and decrypt in browser'
                            }
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                              <polyline points="7 10 12 15 17 10" />
                              <line x1="12" y1="15" x2="12" y2="3" />
                            </svg>
                            <span>{actionLoading[file.file_id] === 'downloading' ? 'Decrypting...' : 'Download & Decrypt'}</span>
                          </button>

                          <button
                            type="button"
                            className="btn-action-icon btn-delete"
                            onClick={() => handleDeleteFile(file)}
                            disabled={isLoading}
                            title="Delete file and free provider storage"
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <polyline points="3 6 5 6 21 6" />
                              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                            </svg>
                            <span>{actionLoading[file.file_id] === 'deleting' ? 'Deleting...' : 'Delete'}</span>
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Export Key Backup Modal */}
      {showExportModal && (
        <div className="modal-overlay" onClick={() => setShowExportModal(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h4 className="modal-title">Export Encrypted Key Backup</h4>
              <button
                type="button"
                className="modal-close"
                onClick={() => setShowExportModal(false)}
              >
                &times;
              </button>
            </div>

            <form onSubmit={handleExportBackup}>
              <div className="modal-body">
                <p style={{ fontSize: '0.88rem', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                  Your local encryption keys will be encrypted using <strong>AES-256-GCM</strong> with a key derived
                  via <strong>PBKDF2-HMAC-SHA256 (100,000 iterations)</strong>.
                </p>

                <div className="form-group" style={{ marginBottom: '14px' }}>
                  <label className="form-label">Backup Passphrase (min 8 characters):</label>
                  <input
                    type="password"
                    className="form-input"
                    value={exportPassphrase}
                    onChange={(e) => setExportPassphrase(e.target.value)}
                    placeholder="Enter a strong passphrase"
                    required
                  />
                </div>

                <div className="form-group" style={{ marginBottom: '16px' }}>
                  <label className="form-label">Confirm Backup Passphrase:</label>
                  <input
                    type="password"
                    className="form-input"
                    value={exportPassphraseConfirm}
                    onChange={(e) => setExportPassphraseConfirm(e.target.value)}
                    placeholder="Repeat passphrase"
                    required
                  />
                </div>

                {exportError && (
                  <div className="alert-box error" style={{ marginBottom: '14px' }}>
                    <div className="alert-message">{exportError}</div>
                  </div>
                )}

                {exportSuccess && (
                  <div className="alert-box success" style={{ marginBottom: '14px' }}>
                    <div className="alert-message">{exportSuccess}</div>
                  </div>
                )}
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowExportModal(false)}
                >
                  Close
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={exportLoading}
                >
                  {exportLoading ? 'Encrypting Backup...' : 'Generate & Download Backup'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Import Key Backup Modal */}
      {showImportModal && (
        <div className="modal-overlay" onClick={() => setShowImportModal(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h4 className="modal-title">Import Encrypted Key Backup</h4>
              <button
                type="button"
                className="modal-close"
                onClick={() => setShowImportModal(false)}
              >
                &times;
              </button>
            </div>

            <form onSubmit={handleImportBackup}>
              <div className="modal-body">
                <p style={{ fontSize: '0.88rem', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                  Restore your cryptographic keys from a <code>.storeshare-keys</code> backup file. Keys will be decrypted locally
                  and stored in this browser's IndexedDB vault.
                </p>

                <div className="form-group" style={{ marginBottom: '14px' }}>
                  <label className="form-label">Select .storeshare-keys File:</label>
                  <input
                    type="file"
                    className="form-input"
                    accept=".storeshare-keys,application/json"
                    onChange={(e) => {
                      if (e.target.files && e.target.files.length > 0) {
                        setImportFile(e.target.files[0])
                      }
                    }}
                    required
                  />
                </div>

                <div className="form-group" style={{ marginBottom: '16px' }}>
                  <label className="form-label">Backup Passphrase:</label>
                  <input
                    type="password"
                    className="form-input"
                    value={importPassphrase}
                    onChange={(e) => setImportPassphrase(e.target.value)}
                    placeholder="Enter the passphrase used during export"
                    required
                  />
                </div>

                {importError && (
                  <div className="alert-box error" style={{ marginBottom: '14px' }}>
                    <div className="alert-message">{importError}</div>
                  </div>
                )}

                {importSuccess && (
                  <div className="alert-box success" style={{ marginBottom: '14px' }}>
                    <div className="alert-message">{importSuccess}</div>
                  </div>
                )}
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowImportModal(false)}
                >
                  Close
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={importLoading}
                >
                  {importLoading ? 'Decrypting & Restoring...' : 'Restore Keys'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
