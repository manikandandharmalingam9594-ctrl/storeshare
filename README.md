# Store-Share — Authentication & Role-Based Frontend

Store-Share is a decentralized storage project. This repository contains the **Core Foundation, Authentication Backend, and Role-Based Frontend Experience**:
- **Backend**: FastAPI service on `http://127.0.0.1:8001` with SQLite database (`data/storeshare.db`), Argon2id password hashing, PyJWT bearer token authorization, and role-based access control (RBAC).
- **Frontend**: React + Vite application on `http://localhost:3000` with a dark storage-network theme, client-side route protection, authenticated session restoration, and dedicated dashboards for `CONSUMER`, `PROVIDER`, and `ADMIN` roles.

---

## Project Structure

```
storeshare/
├── backend/
│   ├── auth_router.py          # Authentication endpoints (/register, /login, /me, /admin/providers)
│   ├── create_admin.py         # Secure interactive CLI to bootstrap the first administrator
│   ├── database.py             # SQLite connection manager, users table, indexing & parameterized queries
│   ├── dependencies.py         # Reusable FastAPI auth dependencies & role checks (require_admin, etc.)
│   ├── main.py                 # FastAPI entrypoint, lifespan startup, CORS & router mounting
│   ├── requirements.txt        # Python dependencies (fastapi, uvicorn, argon2-cffi, pyjwt, pytest, httpx)
│   ├── schemas.py              # Pydantic request/response models with input validation & extra="forbid"
│   └── security.py             # Argon2id password hashing & PyJWT token creation/verification
├── data/
│   ├── .gitkeep                # Preserves directory in version control
│   └── storeshare.db           # SQLite database (auto-created at project root data/)
├── frontend/
│   ├── public/
│   │   └── favicon.svg         # Storage mesh node SVG icon
│   ├── src/
│   │   ├── components/
│   │   │   ├── LoginForm.jsx   # Polished sign-in form with validation, error banners, and loading state
│   │   │   ├── Navbar.jsx      # Header with brand, navigation links, role badge, and logout
│   │   │   └── RegisterForm.jsx# Consumer registration form (strictly no role selector)
│   │   ├── context/
│   │   │   ├── AuthContext.jsx # Authentication state, session restoration, login, register, logout
│   │   │   └── RouterContext.jsx# Lightweight client router with history.pushState and popstate support
│   │   ├── pages/
│   │   │   ├── AccessDeniedPage.jsx  # 403 Forbidden page showing role mismatch & security notices
│   │   │   ├── AdminDashboard.jsx    # Admin console with profile & provider provisioning form
│   │   │   ├── ConsumerDashboard.jsx # Consumer console with profile & encrypted vault placeholder
│   │   │   ├── ProviderDashboard.jsx # Provider console with profile & daemon telemetry placeholder
│   │   │   └── SystemStatusPage.jsx  # Preserved Day 1 health check & SQLite tables inspector
│   │   ├── services/
│   │   │   └── auth.js         # Central API client calling relative /api/* endpoints
│   │   ├── App.jsx             # Route guard orchestrator and role-based redirects
│   │   ├── index.css           # Custom dark storage network CSS design system
│   │   └── main.jsx            # React root entrypoint
│   ├── tests/
│   │   └── auth.test.js        # Automated frontend test suite (8 tests via Node test runner)
│   ├── index.html              # HTML entrypoint with Inter/JetBrains Mono typography
│   ├── package.json            # Frontend scripts and React/Vite dependencies
│   └── vite.config.js          # Vite dev server config (port 3000 + /api proxy to :8001)
├── tests/
│   ├── conftest.py             # Isolated temporary test database & TestClient fixtures
│   └── test_auth.py            # 21 automated backend tests covering auth, roles, and boundaries
├── .env.example                # Sample configuration file for environment variables
├── .gitignore                  # Ignores .env, .venv, node_modules, *.db, build artifacts
└── README.md                   # Documentation and execution instructions
```

---

## Token Storage & XSS Trade-off Notice

In this prototype, the frontend stores the JWT access token in **`sessionStorage`**.
- **Lifecycle**: The token is scoped to the active browser tab session and is automatically purged when the tab or window is closed.
- **Logout**: Explicitly cleared when the user clicks the "Logout" button.
- **Security Trade-off**: Storing tokens in client-accessible browser storage (`sessionStorage` or `localStorage`) is **not immune to Cross-Site Scripting (XSS)**. Any JavaScript executing in the origin could theoretically read the token if an XSS vulnerability exists.
- **Production Recommendation**: For production systems with strict zero-trust threat models, authentication tokens should be delivered via `httpOnly`, `Secure`, and `SameSite` cookies or held exclusively in memory with secure refresh token rotation.
- **Password Safety**: Passwords are never stored in browser storage or printed to console logs.

---

## Authentication Flow Explained

1. **Consumer Registration (`POST /api/auth/register`)**:
   - The user submits an email, password, and password confirmation on `/register`.
   - The form contains **no role selector**. Public registration is permanently locked to the `CONSUMER` role.
   - The backend validates the input, normalizes the email to lowercase, hashes the password with **Argon2id**, and stores the record in SQLite.
   - If the email is already in use, the backend returns **HTTP 409 Conflict**, which the UI presents as a readable error banner.
   - On success, the user is authenticated and navigated to the **Consumer Dashboard** (`/consumer`).

2. **Login & Token Issuance (`POST /api/auth/login`)**:
   - The user submits their email and password on `/login`.
   - The backend compares the password against the stored Argon2id hash. If invalid or inactive, it returns **HTTP 401 Unauthorized**.
   - Upon verification, the backend generates an `HS256` signed JWT token with a 60-minute expiry.
   - The frontend saves the token into `sessionStorage` and immediately calls `GET /api/auth/me` to confirm identity and verified role from the backend.
   - The user is redirected to their authorized dashboard based on their backend role:
     - `CONSUMER` &rarr; `/consumer`
     - `PROVIDER` &rarr; `/provider`
     - `ADMIN` &rarr; `/admin`

3. **Frontend Route Protection & Access Control**:
   - **Unauthenticated users** attempting to access `/consumer`, `/provider`, or `/admin` are immediately redirected to `/login`.
   - **Authenticated users** attempting to access a dashboard belonging to another role (e.g. a `CONSUMER` visiting `/admin`) are intercepted by the frontend and shown the **Access Denied (403 Forbidden)** screen explaining the role mismatch.
   - **Security Principle**: Frontend route protection provides an intuitive user experience; all actual data and endpoint access is enforced on the FastAPI backend using JWT token signature validation and role dependencies (`require_admin`, `require_provider`).

---

## Step-by-Step PowerShell Execution Guide

Open PowerShell in the project root directory (`C:\Projects\storeshare`).

### 1. Environment & Dependency Setup

```powershell
# Activate Python virtual environment
.\.venv\Scripts\Activate.ps1

# Install backend dependencies
python -m pip install -r backend/requirements.txt

# Ensure .env exists with your JWT secret
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

### 2. Bootstrap the First Administrator Account

Run the secure CLI command (prompts for email and password without printing):
```powershell
.\.venv\Scripts\python.exe -m backend.create_admin
```

Or run via automated PowerShell environment variable:
```powershell
$env:STORESHARE_ADMIN_TMP_PWD = 'AdminMasterKey123!'
.\.venv\Scripts\python.exe -m backend.create_admin --email admin@storeshare.io --password-env STORESHARE_ADMIN_TMP_PWD
Remove-Item Env:\STORESHARE_ADMIN_TMP_PWD
```
*(The command strictly refuses to overwrite existing accounts).*

### 3. Run Automated Tests

#### Backend Test Suite (Pytest - 21 tests)
```powershell
.\.venv\Scripts\python.exe -m pytest -v tests/
```

#### Frontend Test Suite (Node - 8 tests)
```powershell
Set-Location -Path C:\Projects\storeshare\frontend
npm test
Set-Location -Path C:\Projects\storeshare
```

#### Frontend Production Build
```powershell
Set-Location -Path C:\Projects\storeshare\frontend
npm run build
Set-Location -Path C:\Projects\storeshare
```

### 4. Start the Application Services

#### Terminal 1: FastAPI Backend (Port 8001)
```powershell
Set-Location -Path C:\Projects\storeshare
.\.venv\Scripts\Activate.ps1
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001 --reload
```

#### Terminal 2: React + Vite Frontend (Port 3000)
```powershell
Set-Location -Path C:\Projects\storeshare\frontend
npm run dev
```

---

## Exact Manual Verification Steps

1. **Verify Backend Health & SQLite Tables**:
   - Open your browser to `http://localhost:3000/`.
   - Click **"Check Backend Health"** &rarr; displays status `healthy`, database `connected`, and latency.
   - Click **"Inspect Database Tables"** &rarr; displays tables `app_metadata` and `users`.

2. **Register a New Consumer Account**:
   - Click **"Register"** in the top navigation or visit `http://localhost:3000/register`.
   - Notice that **no role selector exists** on the form.
   - Enter `user1@storeshare.io`, password `Password123!`, and confirmation `Password123!`.
   - Click **"Register Consumer Account"**.
   - You are automatically authenticated and redirected to the **Consumer Dashboard** (`http://localhost:3000/consumer`).
   - Notice your account details (`CONSUMER` role pill, User ID, active status) and the "Encrypted File Storage Vault — Upcoming Phase" placeholder card.

3. **Verify Route Protection & Role Denial (403)**:
   - While logged in as `user1@storeshare.io`, manually change the URL to `http://localhost:3000/admin`.
   - The application renders the **Access Denied** page:
     - Current Role: `CONSUMER`
     - Required Role: `ADMIN`
     - Status: `403 Forbidden`
   - Click **"Return to My Authorized Dashboard"** &rarr; returns to `/consumer`.

4. **Verify Logout**:
   - Click the **"Logout"** button in the top navigation.
   - The token is cleared from `sessionStorage` and you are redirected to `/login`.
   - Trying to navigate directly to `/consumer` immediately redirects back to `/login`.

5. **Log In as Administrator & Provision a Provider**:
   - Navigate to `http://localhost:3000/login`.
   - Enter `admin@storeshare.io` and `AdminMasterKey123!`.
   - Click **"Sign In to Network"**.
   - You are redirected to the **Admin Dashboard** (`http://localhost:3000/admin`).
   - Notice the amber `ADMIN` role badge and    - You are redirected to the **Provider Dashboard** (`http://localhost:3000/provider`).
    - Notice the purple `PROVIDER` role badge, the storage allocation setup form, and the live status indicators.

---

## Provider Storage Allocation & Local Python Agent

Store-Share includes a local Python provider-agent daemon process (`provider_agent/`) that runs on the provider's machine:
- **NAT Friendly**: Connects outward to the coordinator via HTTP/HTTPS; requires no inbound port forwarding or public IP.
- **Directory Isolation & Safety**: Validates configured storage directory, ensures it exists or creates it, prohibits filesystem root, and rejects symlinked storage paths.
- **Path Traversal Protection**: Strictly validates all chunk operations within the storage directory, blocking relative escapes (`../`) and illegal characters.
- **Quota Reservations & Atomic Writes**: Enforces storage quota, checks physical disk free space (`shutil.disk_usage`), coordinates concurrency with thread-safe reservations, and writes chunks atomically via temporary files and `os.replace`.
- **Zero Plaintext / Keys**: Operates exclusively on opaque encrypted chunk payloads; stores no consumer files or cryptographic keys.

### Running the Provider Agent on Windows

```powershell
# Activate Python virtual environment
.\.venv\Scripts\Activate.ps1

# Run the provider agent with email and password
python -m provider_agent --storage-dir "C:\storeshare-storage" --quota-gb 50 --coordinator-url http://127.0.0.1:8001 --email "node1@storeshare.io" --password "ProviderPassword123!"

# Or run with a pre-authenticated JWT token
python -m provider_agent --storage-dir "C:\storeshare-storage" --quota-gb 50 --token "<YOUR_PROVIDER_JWT>"

# Run single health check / heartbeat and exit
python -m provider_agent --storage-dir "C:\storeshare-storage" --quota-gb 50 --email "node1@storeshare.io" --password "ProviderPassword123!" --run-once
```

---

## Actual Test & Build Results

### 1. Frontend Production Build
```
> frontend@0.0.0 build
> vite build

vite v8.3.4 building client environment for production...
transforming...
✓ 27 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.98 kB │ gzip:  0.51 kB
dist/assets/index-DaYJe4TC.css   12.14 kB │ gzip:  3.26 kB
dist/assets/index-BgjwHXhV.js   278.35 kB │ gzip: 79.32 kB

✓ built in 294ms
```

### 2. Frontend Automated Tests (`npm test`)
```
> frontend@0.0.0 test
> node --test tests/*.test.js

TAP version 13
# Subtest: parseApiError: extracts string detail correctly
ok 1 - parseApiError: extracts string detail correctly
# Subtest: parseApiError: extracts array validation detail correctly
ok 2 - parseApiError: extracts array validation detail correctly
# Subtest: parseApiError: falls back to HTTP status text when JSON parsing fails
ok 3 - parseApiError: falls back to HTTP status text when JSON parsing fails
# Subtest: sessionStorage token management: set, get, clear
ok 4 - sessionStorage token management: set, get, clear
# Subtest: Role-based navigation resolution
ok 5 - Role-based navigation resolution
# Subtest: Role-based guard access check
ok 6 - Role-based guard access check
# Subtest: Live Backend: Health check endpoint
ok 7 - Live Backend: Health check endpoint
# Subtest: Live Backend: Consumer registration, login, and profile verification
ok 8 - Live Backend: Consumer registration, login, and profile verification
# Subtest: Storage provider node setup schema validation
ok 9 - Storage provider node setup schema validation
1..9
# tests 9
# pass 9
# fail 0
# duration_ms 1690.9896
```

### 3. Backend Automated Tests (`pytest -v`)
```
platform win32 -- Python 3.14.7, pytest-9.1.1
rootdir: C:\Projects\storeshare
collected 41 items

tests/test_auth.py (21 tests) .....................                      [ 51%]
tests/test_provider_storage.py (20 tests) ....................           [100%]

======================== 41 passed, 1 warning in 4.32s ========================
```
