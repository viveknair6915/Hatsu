# Polygon Migration Tool — System Architecture and User Flows

## Overview

The **Polygon Migration Tool** is a Django-based application designed to bridge competitive programming problem curation on Codeforces Polygon with internal problem management systems (such as Algopath). The system extracts problem statements, sample cases, full test-case suites, and reference solutions from Polygon via its REST API, stages the data in Redis for low-latency inspection, persists normalized problem records into PostgreSQL, and exports test suites into cloud or object storage in a standardized layout.

---

## 1. System Architecture & Component Interactions

```
+---------------------------------------------------------------------------------+
|                                Frontend (HTML/JS)                               |
|              - Problem Fetch Form     - Tag Selector & Chips                    |
|              - Statement Preview      - DB / Storage Migration Triggers        |
+---------------------------------------------------------------------------------+
                                      |
                                      | HTTP POST / GET
                                      v
+---------------------------------------------------------------------------------+
|                               Django Backend                                    |
|   - Authentication (`users.views.LoginView`, `users.backends.EmailBackend`)      |
|   - Orchestrator View (`problems.views.index`)                                  |
|   - API Client (`problems.polygon_api.PolygonAPI`)                              |
|   - Storage Service Interface (`problems.storage.BaseStorageService`)           |
+---------------------------------------------------------------------------------+
          |                               |                          |
          | Read/Write                    | Cache/Retrieve           | Read/Write
          v                               v                          v
+--------------------+        +---------------------+      +---------------------+
| PostgreSQL 17      |        | Redis Cache (6379)  |      | Storage Service     |
| - users_user       |        | - Raw test cases    |      | (Azure Blob / Local)|
| - problems_problem |        | - Problem info      |      | test_cases/{id}/{n} |
| - problems_testcase|        | - Package metadata  |      | test_cases/{id}/{n}.a
+--------------------+        +---------------------+      +---------------------+
```

---

## 2. End-to-End User Flows

### Flow 1: User Login

1. **User Action:**
   The user navigates to `/users/login/`, inputs their email address and password into the authentication form, and clicks the **Sign In** button.
2. **Frontend Payload:**
   A standard `POST` request to `/users/login/` containing:
   - `csrfmiddlewaretoken`: Django CSRF protection token
   - `username`: Email address (e.g., `admin@hatsu.com`)
   - `password`: User password
3. **Backend Execution:**
   - The request is routed to `users.views.LoginView.post`.
   - The backend validates the credentials using Django's authentication framework via `users.backends.EmailBackend`.
   - Upon successful verification, `django.contrib.auth.login(request, user)` creates a session record in the database and assigns a session cookie (`sessionid`) with a 24-hour expiration (`SESSION_COOKIE_AGE = 86400`).
   - If authentication fails, `django.contrib.messages.error` flags the error and re-renders the login template.
4. **External APIs Called:**
   None.
5. **Database & Storage Reads/Writes:**
   - **Read:** Queries `users_user` table filtering by `email`.
   - **Write:** Updates `last_login` timestamp in `users_user` and writes the session state into `django_session`.
6. **User View on Completion:**
   The user is redirected to the root URL `/` (the main problem migration dashboard), displaying the navigation bar with active session information and the problem fetch interface.

---

### Flow 2: Fetching a Problem from Polygon

1. **User Action:**
   On the main migration page (`/`), the user enters a numeric Polygon Problem ID (e.g., `69927`) into the input field and clicks **Fetch Problem**.
2. **Frontend Payload:**
   A `POST` request to `/` containing:
   - `csrfmiddlewaretoken`: Django CSRF token
   - `problem_id`: String containing the Polygon problem ID
3. **Backend Execution:**
   - The request is handled by `problems.views.index`.
   - It instantiates `problems.polygon_api.PolygonAPI` using credentials loaded from `.env` (`POLYGON_API_KEY`, `POLYGON_API_SECRET`).
   - The backend verifies if the problem already exists in PostgreSQL (`Problem.objects.filter(polygon_id=polygon_id).first()`).
   - The backend attempts to fetch cached test-case and statement data from Redis.
   - On a cache miss, `PolygonAPI` invokes Polygon REST endpoints to retrieve:
     - Problem general information (`problem.info`)
     - Latest package details (`problem.packages`)
     - Complete test suite descriptions (`problem.tests`)
     - Reference solutions and custom checker details (`problem.solutions`, `problem.checker`)
     - Sample test cases and full input/output files (`problem.testInput`, `problem.testAnswer`)
   - Test cases are cached in Redis under `polygon_test_cases_{problem_id}` with a 30-minute expiration window.
4. **External APIs Called (Polygon):**
   - `problem.info`: Problem basic properties and configuration.
   - `problem.packages`: Validates package compilation state.
   - `problem.package`: Downloads compiled statement and resources.
   - `problem.tests`: Enumerates all tests and whether each is marked as a sample.
   - `problem.testInput` & `problem.testAnswer`: Retrieves exact input and output contents for individual test cases.
   - `problem.viewSolution`: Downloads reference solution source code.
5. **Database & Storage Reads/Writes:**
   - **Read:** `problems_problem` table to check if this Polygon ID was previously migrated.
   - **Write:** Redis key `polygon_test_cases_{problem_id}` populated with parsed test cases.
6. **User View on Completion:**
   The dashboard re-renders with:
   - Problem title, Polygon ID, time limit, memory limit, and current migration status.
   - HTML-rendered problem statement, input specification, output specification, and notes.
   - Table of sample test cases.
   - Table of all test cases with inputs, outputs, and sample status badges.
   - Reference solution code viewer with syntax formatting.
   - Interactive migration controls (difficulty dropdown, tag input, and migration buttons).

---

### Flow 3: Migrating Problem Metadata to the Database

1. **User Action:**
   After reviewing the fetched data, the user selects a difficulty (`Easy`, `Medium`, or `Hard`), selects or inputs at least two tags (e.g., `math`, `string`), and clicks **Create/Update problem in Database**.
2. **Frontend Payload:**
   A `POST` request to `/` containing:
   - `csrfmiddlewaretoken`: Django CSRF token
   - `problem_id`: Polygon problem ID
   - `migrate_to_db`: `1`
   - `difficulty`: String (`Easy`, `Medium`, or `Hard`)
   - `tags`: Repeated values or array of selected tag strings
3. **Backend Execution:**
   - Handled in `problems.views.index` under the `migrate_to_db` branch.
   - Server-side validation verifies that `difficulty` is provided and that `tags` contains at least two distinct items.
   - A URL-safe slug is generated via `django.utils.text.slugify(title)`. If a different problem in the database already uses that slug, the system appends the Polygon ID (`f"{base_slug}-{polygon_id}"`) to prevent unique key constraint violations.
   - Inside an atomic database transaction (`transaction.atomic()`):
     - The `Problem` record is created or updated via `Problem.objects.update_or_create(polygon_id=polygon_id, defaults=...)`.
     - Tags are processed: existing `Tag` instances are reused or created via `Tag.objects.get_or_create(name=tag_name)`, then associated with the problem.
     - Sample test cases are synchronized: stale `SampleTestCase` rows are deleted, and new sample records are bulk inserted.
4. **External APIs Called:**
   None (data was pre-fetched and cached during Flow 2).
5. **Database & Storage Reads/Writes:**
   - **Read:** `problems_problem` (slug and ID checks), `problems_tag`.
   - **Write:** `problems_problem` (insert/update), `problems_problem_tags` (M2M table), `problems_sampletestcase` (cleanup and bulk create).
6. **User View on Completion:**
   A green success banner announces: `"Problem and sample test cases migrated to database successfully."`
   The problem metadata table updates to show:
   - Database Problem ID
   - Live Algopath problem link (e.g., `https://www.algopath.ai/problems/ab`)
   - Assigned difficulty and tags
   - Enabled status on the remaining migration buttons

---

### Flow 4: Migrating Test Cases to the Database

1. **User Action:**
   The user clicks **Migrate Test Cases to DB**.
2. **Frontend Payload:**
   A `POST` request to `/` containing:
   - `csrfmiddlewaretoken`: Django CSRF token
   - `problem_id`: Polygon problem ID
   - `migrate_test_cases_to_db`: `1`
3. **Backend Execution:**
   - Handled in `problems.views.index` under `migrate_test_cases_to_db`.
   - The backend checks that the problem already exists in PostgreSQL.
   - Test cases are loaded from the Redis cache or re-fetched from Polygon if the cache has expired.
   - Inside an atomic transaction (`transaction.atomic()`):
     - The backend syncs the test suite: any existing `ProblemTestCase` rows with `test_case_number > len(test_cases)` are removed (handling test deletions on Polygon).
     - For each test case, `ProblemTestCase.objects.update_or_create` persists the record with `test_case_number`, full `input_data`, full `output_data`, and `is_sample` flag. Complete inputs and outputs are preserved without truncation.
4. **External APIs Called:**
   Polygon API endpoints are only called if Redis test case cache has expired.
5. **Database & Storage Reads/Writes:**
   - **Read:** `problems_problem` by `polygon_id`.
   - **Write:** Upserts and deletes in `problems_problemtestcase`.
6. **User View on Completion:**
   A green success notification states: `"Test cases migrated to database successfully (N test cases created/updated)."`
   The problem overview card confirms test suite synchronization.

---

### Flow 5: Uploading Test Cases to Cloud/Object Storage

1. **User Action:**
   The user clicks **Migrate Test Cases to Azure** (or storage upload button).
2. **Frontend Payload:**
   A `POST` request to `/` containing:
   - `csrfmiddlewaretoken`: Django CSRF token
   - `problem_id`: Polygon problem ID
   - `migrate_to_azure`: `1`
3. **Backend Execution:**
   - Handled in `problems.views.index` under `migrate_to_azure`.
   - Verifies the database record exists for `db_problem.id`.
   - Obtains the storage adapter through `problems.storage.get_storage_service()`. The factory checks `STORAGE_PROVIDER` (supporting `azure` or `local`).
   - Prior test cases in storage for this problem are purged via `storage.delete_test_cases(db_problem.id)`.
   - The backend retrieves the test cases (from Redis or Polygon) and iterates through them:
     - For each test `idx`, uploads the raw input file and expected output file.
   - If a custom checker is configured on Polygon, its source code is retrieved and uploaded to `test_cases/{db_problem.id}/custom_checker.cpp`.
4. **External APIs Called:**
   - Cloud Storage Provider API (Azure Blob REST API / Azure Storage SDK) when `STORAGE_PROVIDER=azure`.
   - Local filesystem operations when `STORAGE_PROVIDER=local`.
5. **Database & Storage Reads/Writes:**
   - **Read:** `problems_problem` to resolve primary key `id`.
   - **Write:** Creates blob objects or filesystem files structured as:
     - `test_cases/{problem_id}/{test_number}`
     - `test_cases/{problem_id}/{test_number}.a`
6. **User View on Completion:**
   A green alert displays: `"Test cases migrated to storage successfully (N test cases uploaded)."`
   The files can now be verified directly in the cloud console or filesystem explorer.

---

## 3. External Integrations Reference

### 3.1 Polygon API Integration

The application integrates with the official Codeforces Polygon REST API at `https://polygon.codeforces.com/api/`.

#### Request Authentication & Signature Generation
Polygon requires every authenticated request to supply an SHA-512 signature (`apiSig`) calculated across all request parameters:
1. Generate a random 6-character alphanumeric prefix (`rand`).
2. Collect request parameters (`apiKey`, `time` in Unix epoch, and endpoint-specific parameters).
3. Sort all parameters alphabetically by parameter key.
4. Construct the signature string: `<rand>/<methodName>?<param1>=<val1>&<param2>=<val2>...#<POLYGON_API_SECRET>`.
5. Compute the SHA-512 hash of the UTF-8 encoded string.
6. Append the signature to the parameters: `apiSig = <rand> + <sha512_hash>`.

#### Endpoints Used
| Endpoint | Method | Purpose |
|---|---|---|
| `problem.info` | GET | Fetches statement properties, time limits, memory limits, and checker type. |
| `problem.packages` | GET | Lists compiled packages (Linux/Windows) to extract package IDs. |
| `problem.package` | GET | Downloads the full zip package containing HTML statements and resources. |
| `problem.tests` | GET | Lists test indices, sample flags, and test case groups. |
| `problem.testInput` | GET | Retrieves the raw input text for a specific test number. |
| `problem.testAnswer` | GET | Retrieves the model output text for a specific test number. |
| `problem.checker` | GET | Identifies the checker type (standard testlib or custom). |
| `problem.viewSolution` | GET | Retrieves source code for model solutions. |

---

### 3.2 Cloud Storage Architecture

The storage layer is encapsulated behind `problems.storage.BaseStorageService`:

```python
class BaseStorageService(ABC):
    @abstractmethod
    def upload_test_case(self, problem_id, test_number, input_data, output_data):
        pass

    @abstractmethod
    def delete_test_cases(self, problem_id):
        pass

    @abstractmethod
    def upload_file(self, blob_path, content):
        pass

    @abstractmethod
    def list_files(self, problem_id):
        pass
```

#### Object Hierarchy & Naming Convention
Test cases are stored without artificial zero-padding, matching downstream judge requirements:
```
test_cases/
  └── {problem_id}/
        ├── 1           # Test 1 input
        ├── 1.a         # Test 1 output / answer
        ├── 2           # Test 2 input
        ├── 2.a         # Test 2 output / answer
        ├── ...
        └── custom_checker.cpp (optional custom checker source)
```

#### Provider Implementations
1. **Azure Blob Storage (`AzureStorageService`):**
   - Authentication via connection string (`AZURE_STORAGE_CONNECTION_STRING`), account key, or Azure Active Directory (`UsernamePasswordCredential`).
   - Blobs are uploaded directly to the target container with `overwrite=True`.
2. **Local Object Storage (`LocalStorageService`):**
   - Configured via `STORAGE_PROVIDER=local` and `STORAGE_LOCAL_DIR`.
   - Mirrors the exact cloud directory structure on the local filesystem, enabling local development, offline demos, and test suite verification without cloud subscription requirements.
