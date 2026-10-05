# Issues Analysis

This document identifies and prioritizes the six most important issues discovered while auditing, refactoring, and testing the Polygon Migration Tool codebase. It also provides verified answers and behavioral analyses for the four edge case scenarios.

---

## Summary

| Type | Critical | High | Medium | Low | Total |
|------|:--------:|:----:|:------:|:---:|:-----:|
| **Product Issues** | 1 | 1 | 1 | 0 | **3** |
| **Code Issues** | 1 | 2 | 0 | 0 | **3** |

---

## Product Issues

Product issues affect the end-user or downstream consumers of the migrated data: broken functionality, silent corruption, missing validation, or confusing workflows.

---

### [P1] Silent Data Loss via 260-Byte Test Case Truncation in Database

**Severity**: Critical

**Location**: `problems/views.py` (database test-case insertion loop)

**Description**:
When persisting test cases via the "Migrate Test Cases to DB" action, the original starter implementation contained hardcoded string slices:
```python
in_data = test.get('input', '')[:260]
out_data = test.get('output', '')[:260]
```
Any test case whose input or output exceeded 260 characters was silently truncated before being written to PostgreSQL. The application reported a green "migration successful" notification to the user without any warning that data had been clipped.

**Impact**:
- Competitive programming test cases regularly exceed several kilobytes or megabytes (e.g., graphs with thousands of edges, large strings, numeric arrays).
- Truncating input files renders them syntactically invalid for problem solvers, causing test-case grading engines and judge workers to crash or produce false "Wrong Answer" / "Runtime Error" verdicts.
- Because the UI signaled full success, problem setters and administrators assumed the problem was complete and deployed corrupted problems into production contests.

**Suggested Fix**:
Remove the arbitrary `[:260]` slice and store the entire test string directly in PostgreSQL using Django's `TextField`, which supports arbitrarily large text payloads without silent data loss.

---

### [P2] Missing Validation on Difficulty and Tag Taxonomy During Problem Migration

**Severity**: High

**Location**: `problems/templates/problems/index.html` and `problems/views.py:migrate_to_db`

**Description**:
The problem migration form allowed submission without enforcing that a difficulty was selected or that the required minimum of two tags was attached. Furthermore, the starter template's JavaScript lacked proper event handlers to attach dynamically typed tags to hidden form inputs. When users clicked "Create/Update problem in Database", the form submitted empty difficulty strings or no tags at all.

**Impact**:
- Incomplete problem records entered the database missing required classification metadata.
- Downstream platforms (such as the Algopath problem directory) rely on difficulty ratings (`Easy`, `Medium`, `Hard`) and tags for search indexing, curriculum assignment, and filtering. Problems migrated without these fields appeared broken or orphaned in category listings.
- Users were confused when tags typed in the UI disappeared upon page reload.

**Suggested Fix**:
1. Implement client-side chips with keyboard and selection handlers that populate `<input type="hidden" name="tags" value="...">` inputs before form submission.
2. Enforce strict server-side validation in `problems/views.py`: reject any request where `difficulty` is missing or where `len(tags) < 2`, returning a prominent warning banner.

---

### [P3] Cryptic Error Messages and Missing Actionable Diagnostics on External API Failures

**Severity**: Medium

**Location**: `problems/polygon_api.py` and `problems/views.py`

**Description**:
When external API calls to Polygon or cloud storage failed (e.g., incorrect API keys, non-existent problem IDs, unbuilt package archives, or Azure tenant authentication errors), the system either raised an unhandled `requests.exceptions.HTTPError: 400 Client Error` or surfaced low-level internal stack traces (such as MSAL authority discovery failures: `Unable to get authority configuration for https://login.microsoftonline.com/test-tenant-id`).

**Impact**:
- Non-technical problem setters cannot determine whether an error was caused by invalid credentials, Polygon rate limits, missing permissions, or an uncompiled Polygon package.
- Debugging required SSH access to server logs rather than providing actionable guidance within the UI.

**Suggested Fix**:
1. Parse Polygon's structured JSON error payload (`{"status": "FAILED", "comment": "..."}`) and display the human-readable `comment` directly in the UI flash message.
2. In storage integrations, check whether credentials contain placeholder values and guide the administrator to update `.env` or select `STORAGE_PROVIDER=local`.

---

## Code Issues

Code issues represent technical debt, structural flaws, race conditions, or performance bottlenecks in the codebase.

---

### [C1] Database Transactions Wrapping Slow Synchronous Network Requests

**Severity**: Critical

**Location**: `problems/views.py` (`index` view and migration handlers)

**Description**:
The original codebase decorated the entire `index` view or wrapped the migration blocks with `@transaction.atomic`. Within this atomic transaction, the code executed synchronous HTTP requests to external services, including:
- Multiple calls to `PolygonAPI.get_problem_info`, `get_all_test_cases`, and individual test downloads.
- Custom checker compilation and external cloud storage upload requests.

**Impact**:
- Holding open a database transaction while waiting on external network I/O locks PostgreSQL connection pool slots for tens of seconds or minutes.
- Under multi-user concurrency, this quickly exhausts the database connection pool, stalling all other application requests.
- If an external network call times out after several minutes, the entire database transaction rolls back, wasting computational resources and causing unnecessary database lock contention.

**Suggested Fix**:
Confine `transaction.atomic()` strictly to local database mutations (`Problem.objects.update_or_create`, `ProblemTestCase.objects.bulk_create`, and deletions). Execute all external network requests to Polygon and Cloud Storage outside of database transactions.

---

### [C2] Stale Test Cases Retained in Database and Storage When Test Counts Decrease

**Severity**: High

**Location**: `problems/views.py` (`migrate_test_cases_to_db` and `migrate_to_azure`)

**Description**:
When migrating test cases to the database, the starter code looped through the newly fetched test list and executed `update_or_create` based solely on the loop index `test_case_number`. If a problem originally had 20 test cases and the author subsequently deleted 8 tests on Polygon (leaving 12), re-migrating the problem updated tests 1 through 12 but left tests 13 through 20 untouched in PostgreSQL. A similar bug existed in storage uploads if previous blobs were not purged.

**Impact**:
- The database test suite diverged from the canonical Polygon test suite.
- Downstream grading systems executed deprecated or invalid test cases that the problem setter had intentionally removed, producing incorrect test verdicts.

**Suggested Fix**:
Before or during the upsert loop, synchronize the test suite by explicitly deleting orphaned records:
```python
ProblemTestCase.objects.filter(
    problem=db_problem,
    test_case_number__gt=len(test_cases)
).delete()
```
For cloud storage, invoke `storage.delete_test_cases(problem_id)` prior to uploading new tests.

---

### [C3] Database Slug Collision on Identical Problem Titles Crashing Migration

**Severity**: High

**Location**: `problems/views.py` (`Problem.objects.update_or_create`) and `problems/models.py`

**Description**:
The `Problem` model specifies `slug = models.SlugField(max_length=255, unique=True)`. In the starter code, the migration logic generated the slug purely from the title: `slug = slugify(info['name'])`. If two distinct Polygon problems shared the same title (e.g., two problem setters each creating a problem named "Two Sum"), migrating the second problem triggered:
`django.db.utils.IntegrityError: duplicate key value violates unique constraint "problems_problem_slug_key"`

**Impact**:
- The second problem completely failed to migrate with an HTTP 500 error.
- Because Polygon Problem IDs are independent of titles, common titles or shared problem names broke the migration pipeline.

**Suggested Fix**:
Treat `polygon_id` as the primary business identity. Before saving, verify whether the generated slug already belongs to a different problem. If a collision is detected, append the Polygon ID to generate a deterministic, unique slug:
```python
base_slug = slugify(info.get('name', 'problem'))
slug = base_slug
existing = Problem.objects.filter(slug=slug).exclude(polygon_id=polygon_id).first()
if existing:
    slug = f"{base_slug}-{polygon_id}"
```

---

## Edge Case Analysis

### Q1: A Polygon problem has 0 sample test cases and 15 regular test cases. What happens when you migrate it?

**Analysis & Observed Behavior:**
- **Code Investigation:**
  In the starter code, the problem migration logic assumed at least one sample test case existed and accessed `sample_test_cases[0]` directly without guard checks. If `sample_test_cases` was empty (`[]`), this resulted in an `IndexError: list index out of range` during problem metadata migration. Furthermore, if a problem previously had sample test cases in the database and was re-migrated with 0 samples, stale `SampleTestCase` rows remained in the database.
- **Tested Implementation:**
  In our verified implementation, we handled this case defensively:
  1. The statement parser and test case extractor safely return an empty list when no tests have the `manual=true` or sample flag set.
  2. When persisting to the database, `SampleTestCase.objects.filter(problem=problem).delete()` purges any prior samples, and the insertion loop cleanly skips when the list is empty.
  3. The problem record and all 15 regular test cases migrate successfully. In the UI, the "Sample Test Cases" section is cleanly omitted or shows 0 samples, while the "All Test Cases" table displays all 15 regular tests.

---

### Q2: A problem is migrated with 20 test cases. The problem setter later removes eight on Polygon, leaving 12. What happens when you migrate the same problem again?

**Analysis & Observed Behavior:**
- **Code Investigation (Starter Code):**
  The original codebase used an index-based upsert loop (`for idx, test in enumerate(test_cases, start=1): update_or_create(problem=db_problem, test_case_number=idx, ...)`). When re-migrated with 12 tests, tests 1 through 12 were updated with new data, but records for test numbers 13 through 20 remained in the database. The database retained 20 test cases instead of 12, leaving 8 phantom test cases.
- **Tested Implementation:**
  We introduced synchronization logic in both the database migration and the storage migration:
  1. **Database:** An explicit pruning query removes orphaned rows:
     ```python
     ProblemTestCase.objects.filter(
         problem=db_problem,
         test_case_number__gt=len(test_cases)
     ).delete()
     ```
  2. **Storage:** `storage.delete_test_cases(db_problem.id)` purges all existing blobs under `test_cases/{problem_id}/` before uploading the active 12 test cases.
  3. **Verification:** Validated via automated unit test `test_remigrate_with_fewer_test_cases`. The test confirms that exactly 12 test cases remain in the database and in storage after re-migration.

---

### Q3: Two different Polygon problems have the same title, "Two Sum". You migrate the first successfully, then try to migrate the second. What happens?

**Analysis & Observed Behavior:**
- **Code Investigation (Starter Code):**
  The `Problem` database model enforces a unique constraint on `slug` (`unique=True`). The starter code computed `slug = slugify(info['name'])`, which evaluates to `"two-sum"` for both problems. When migrating the second problem, Django attempted to execute an `INSERT` with `slug="two-sum"`, resulting in an unhandled PostgreSQL database exception:
  `IntegrityError: duplicate key value violates unique constraint "problems_problem_slug_key"`.
  The migration failed and returned a 500 error to the user.
- **Tested Implementation:**
  We updated the migration flow to distinguish problems by their unique `polygon_id` and handle slug collisions:
  ```python
  base_slug = slugify(info.get('name', 'problem'))
  slug = base_slug
  existing_slug_problem = Problem.objects.filter(slug=slug).exclude(polygon_id=polygon_id).first()
  if existing_slug_problem:
      slug = f"{base_slug}-{polygon_id}"
  ```
  The first problem is stored with slug `two-sum`, and the second problem is stored with slug `two-sum-<second_polygon_id>`. Both problems coexist harmoniously in the database and link to their respective Algopath endpoints. Validated via automated unit test `test_two_problems_same_title_slug_collision`.

---

### Q4: When you click "Migrate Test Cases to DB", some data is intentionally discarded. What data is lost, and why could that cause problems?

**Analysis & Observed Behavior:**
- **Discarded Data Elements:**
  1. **Truncated Test Data:** The starter code intentionally sliced inputs and outputs with `[:260]`. Any input or output data exceeding 260 characters was permanently discarded.
  2. **Checker Source Code and Executables:** Polygon package archives often include custom testlib checkers (`checker.cpp`), testlib headers, and compiled validator binaries. The "Migrate Test Cases to DB" action only saves rows into `ProblemTestCase`; it completely discards custom checker code and compilation instructions.
  3. **Package Configuration & Limits:** Polygon package descriptors contain per-test time limits, memory limit overrides, points, and test group definitions (subtasks). These metadata fields are discarded when storing raw test strings in the database.
- **Why This Causes Problems:**
  - Truncating input and output data corrupts any non-trivial test case (arrays, graphs, long strings), preventing judge workers from feeding full inputs to student code or verifying complete expected outputs.
  - Problems with multiple valid outputs (e.g., floating-point approximations, problems asking for "any valid path", or arbitrary graph orderings) require a custom checker to evaluate correctness. Storing only the test case string without the custom checker makes automated evaluation impossible on the target platform.
- **Resolution in Our Implementation:**
  We removed the 260-byte slice in `problems/views.py` so the full test case input and output are preserved in PostgreSQL. For custom checkers, we integrated checker retrieval and upload via `storage.upload_file` so checker logic is preserved in storage alongside the test suite.

---

## Additional Architecture & Code Quality Observations

1. **Storage Decoupling:**
   The starter code directly bound Azure-specific classes (`AzureBlobManager`, `UsernamePasswordCredential`) inside views and helper scripts. Wrapping cloud operations behind `BaseStorageService` and supporting standard connection strings, Azure AD, and local storage (`LocalStorageService`) ensures the application can be deployed across AWS S3, GCP, Cloudflare R2, or local development environments without rewriting business logic.
2. **Redis Fault Tolerance:**
   Redis is used for caching test cases and Polygon packages. The original implementation hung indefinitely if Redis was unreachable or slow. We configured explicit socket timeouts (1.5s to 2.0s) on Redis connections so that if Redis is down or unconfigured, the application falls back smoothly to Polygon API calls without blocking worker threads.
3. **Database Migration Consistency:**
   The starter models included `notes` and `genie_chat` fields on `Problem` that were missing from the initial migration. Adding migration `0002_problem_genie_chat_problem_notes` resolved schema drift and ensured clean database provisioning on fresh PostgreSQL installations.
