# System Documentation

## 1. User Flows

### Flow 1: User Login
- **User Action**: Enters email and password on `/users/login/` and clicks "Sign In".
- **Frontend Payload**: `POST` with `username` (email), `password`, and `csrfmiddlewaretoken`.
- **Backend Flow** (`users/views.py:LoginView`):
  1. Authenticates credentials via `users.backends.EmailBackend`.
  2. Creates session using `django.contrib.auth.login`.
- **External APIs**: None.
- **Database Reads/Writes**: Reads `users_user`; writes `last_login` and session to `django_session`.
- **User View**: Redirected to `/` showing the problem search bar.

---

### Flow 2: Fetching a Problem from Polygon
- **User Action**: Enters Polygon Problem ID (e.g. `69927`) and clicks "Fetch Problem".
- **Frontend Payload**: `POST` to `/` with `problem_id`.
- **Backend Flow** (`problems/views.py:index`):
  1. Checks if problem exists in database (`Problem.objects.filter`).
  2. Queries Redis cache (`polygon_test_cases_{id}`).
  3. If missing, calls Polygon API via `problems/polygon_api.py:PolygonAPI` to fetch metadata, statement, and tests.
  4. Caches test cases in Redis for 30 minutes.
- **External APIs**: Polygon API (`problem.info`, `problem.packages`, `problem.package`, `problem.tests`, `problem.testInput`, `problem.testAnswer`, `problem.solutions`).
- **Database Reads/Writes**: Reads `Problem` table; writes test case data to Redis cache.
- **User View**: Statement preview, sample cases table, all test cases table, and migration controls.

---

### Flow 3: Migrating Problem to Database
- **User Action**: Selects Difficulty (`Easy`/`Medium`/`Hard`), adds at least two tags, and clicks "Create/Update problem in Database".
- **Frontend Payload**: `POST` to `/` with `problem_id`, `migrate_to_db=1`, `difficulty`, and `tags`.
- **Backend Flow** (`problems/views.py:index`):
  1. Validates difficulty and minimum 2 tags.
  2. Generates slug from title; appends Polygon ID if slug collision occurs.
  3. Upserts `Problem` in an atomic database transaction.
  4. Creates or links `Tag` objects.
  5. Clears previous samples and inserts new `SampleTestCase` rows.
- **External APIs**: None.
- **Database Reads/Writes**: Writes to `problems_problem`, `problems_tag`, `problems_problem_tags`, and `problems_sampletestcase`.
- **User View**: Green success message, populated Database Problem ID, and Algopath link.

---

### Flow 4: Migrating Test Cases to Database
- **User Action**: Clicks "Migrate Test Cases to DB".
- **Frontend Payload**: `POST` to `/` with `problem_id` and `migrate_test_cases_to_db=1`.
- **Backend Flow** (`problems/views.py:index`):
  1. Loads test cases from Redis cache (or Polygon API if cache expired).
  2. Deletes stale tests (`test_case_number > len(test_cases)`).
  3. Upserts full test cases into `ProblemTestCase` without character truncation.
- **External APIs**: Polygon API only if Redis cache expired.
- **Database Reads/Writes**: Upserts and deletes in `problems_problemtestcase`.
- **User View**: Success banner showing count of migrated test cases.

---

### Flow 5: Uploading Test Cases to Cloud/Local Storage
- **User Action**: Clicks "Migrate Test Cases to Azure" (or Storage).
- **Frontend Payload**: `POST` to `/` with `problem_id` and `migrate_to_azure=1`.
- **Backend Flow** (`problems/views.py:index`):
  1. Retrieves storage provider via `problems/storage.py:get_storage_service()`.
  2. Calls `storage.delete_test_cases(db_problem.id)` to purge older files.
  3. Uploads input and output for each test case.
  4. Uploads custom checker file if present.
- **External APIs**: Azure Blob Storage REST API (if `STORAGE_PROVIDER=azure`) or local filesystem writes (if `STORAGE_PROVIDER=local`).
- **Database Reads/Writes**: Reads `Problem` record.
- **User View**: Success banner with total number of test cases uploaded.

---

## 2. External Integrations

### Polygon API
- **Base URL**: `https://polygon.codeforces.com/api/`
- **Authentication**: Signed via SHA-512 `apiSig`:
  1. Generate random 6-character string (`rand`).
  2. Sort query parameters alphabetically.
  3. Hash string: `<rand>/<methodName>?<params>#<POLYGON_API_SECRET>`.
  4. Send parameter `apiSig = <rand><sha512_hex>`.
- **Endpoints Used**:
  - `problem.info`: Problem limits and configuration.
  - `problem.packages` / `problem.package`: Statement package download.
  - `problem.tests`: Test list and sample flags.
  - `problem.testInput` / `problem.testAnswer`: Raw inputs and expected outputs.
  - `problem.viewSolution`: Reference solution.

### Storage Interface & Organization
All storage providers implement `BaseStorageService` (`problems/storage.py`):
```
test_cases/{problem_id}/{test_number}      # Input file
test_cases/{problem_id}/{test_number}.a    # Output file
```
- **Azure Blob Storage**: `AzureStorageService` connects via `AZURE_STORAGE_CONNECTION_STRING` or Azure credentials.
- **Local Storage**: `LocalStorageService` writes to `test_cases/{problem_id}/` for easy local development and verification.
