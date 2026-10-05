# Issues Analysis

## Summary

| Type | Critical | High | Medium | Low | Total |
|------|:--------:|:----:|:------:|:---:|:-----:|
| **Product Issues** | 1 | 1 | 1 | 0 | **3** |
| **Code Issues** | 1 | 2 | 0 | 0 | **3** |

---

## Product Issues

### [P1] Silent Data Loss via 260-Byte Test Case Truncation in Database
- **Severity**: Critical
- **Location**: `problems/views.py` (legacy `in_data[:260]` and `out_data[:260]`)
- **Description**: The database migration slice clipped inputs and outputs to 260 characters before saving to PostgreSQL, while still showing a green "Success" banner.
- **Impact**: Any non-trivial test case (arrays, graphs, long strings) was corrupted in the database, breaking problem grading and automated testing on the platform.
- **Suggested Fix**: Remove the `[:260]` slice and store the full string using PostgreSQL `TextField`.

---

### [P2] Missing Validation for Difficulty and Tags
- **Severity**: High
- **Location**: `problems/templates/problems/index.html` and `problems/views.py`
- **Description**: Users could submit the database migration form without selecting a difficulty or providing the required minimum of two tags. In addition, dynamically added tags were not attached to the form submit payload.
- **Impact**: Incomplete problem records were saved to the database, breaking problem classification and filtering on Algopath.
- **Suggested Fix**: Enforce client-side tag chip selection and server-side checks requiring difficulty and at least two tags before saving.

---

### [P3] Cryptic Error Messages on External API / Cloud Failures
- **Severity**: Medium
- **Location**: `problems/polygon_api.py` and `problems/views.py`
- **Description**: Failures in Polygon API or cloud storage returned raw technical exceptions (e.g. `HTTP 400 Bad Request` or MSAL authority discovery errors) instead of explaining the problem.
- **Impact**: Users could not tell if the issue was caused by invalid API credentials, unbuilt packages, or missing cloud configuration.
- **Suggested Fix**: Parse the `comment` field from Polygon JSON responses and show actionable error messages in the UI.

---

## Code Issues

### [C1] Database Transactions Wrapping Slow External Network Requests
- **Severity**: Critical
- **Location**: `problems/views.py`
- **Description**: The starter code placed `@transaction.atomic()` around blocks that performed slow synchronous HTTP requests to Polygon and cloud storage.
- **Impact**: Database connections were held open during long network calls, causing connection pool exhaustion and blocking other users under concurrency.
- **Suggested Fix**: Keep external HTTP requests outside of transactions and only use `transaction.atomic()` around local database writes.

---

### [C2] Stale Test Cases Retained When Test Count Decreases on Polygon
- **Severity**: High
- **Location**: `problems/views.py`
- **Description**: The migration loop upserted tests by index `test_case_number`. If a problem with 20 test cases was edited on Polygon to have 12 test cases, re-migrating updated tests 1–12 but left tests 13–20 in PostgreSQL.
- **Impact**: Deleted test cases remained in the database, causing test suites to diverge from Polygon.
- **Suggested Fix**: Delete stale test cases with `test_case_number__gt=len(test_cases)` during migration, and purge storage blobs prior to re-upload.

---

### [C3] Database Slug Collision on Identical Problem Titles
- **Severity**: High
- **Location**: `problems/views.py`
- **Description**: Problem slug was generated solely using `slugify(title)`. Because `slug` has a unique constraint, migrating a second problem with the same title (e.g. "Two Sum") crashed with an `IntegrityError`.
- **Impact**: The second problem failed to migrate completely.
- **Suggested Fix**: Check if the generated slug already belongs to another problem; if so, append the Polygon ID (`f"{base_slug}-{polygon_id}"`).

---

## Edge Case Analysis

### Q1: A Polygon problem has 0 sample test cases and 15 regular test cases. What happens when you migrate it?
- **Behavior**: In the starter code, accessing `sample_test_cases[0]` raised an `IndexError`, crashing migration. Stale sample records also lingered if re-migrating.
- **Fix & Verification**: The statement parser and DB save logic safely handle empty sample lists by clearing previous samples and proceeding with the migration. Verified via unit test `test_migrate_problem_zero_samples`.

---

### Q2: A problem is migrated with 20 test cases. Later, the problem setter removes 8 test cases on Polygon (leaving 12). What happens on re-migration?
- **Behavior**: In the starter code, the upsert loop updated test cases 1 to 12, leaving orphaned test cases 13 to 20 in the database.
- **Fix & Verification**: We added an explicit deletion step for tests where `test_case_number > len(test_cases)`. Older cloud files are also purged before re-upload. Verified via unit test `test_remigrate_with_fewer_test_cases`.

---

### Q3: Two different Polygon problems have the exact same title ("Two Sum"). What happens?
- **Behavior**: In the starter code, both problems produced the same slug (`two-sum`). Saving the second problem crashed with `IntegrityError` due to the unique constraint on `Problem.slug`.
- **Fix & Verification**: The system detects the duplicate slug and appends the Polygon ID to create a unique slug (e.g., `two-sum-69927`). Verified via unit test `test_two_problems_same_title_slug_collision`.

---

### Q4: When you click "Migrate Test Cases to DB", what data is intentionally discarded, and why is that problematic?
- **Discarded Data**:
  1. **Truncated Test Data**: The starter code sliced input and output strings to 260 characters (`[:260]`).
  2. **Custom Checkers & Validators**: Checker source code (`checker.cpp`) and testlib configurations are discarded from the database.
  3. **Package Limits & Groups**: Per-test memory limits, time limits, and subtask groupings are not stored.
- **Why It Causes Problems**: Truncated test cases break judge runners on large inputs (arrays, graphs), and missing checkers prevent evaluating problems with multiple valid outputs (e.g. float tolerances, arbitrary orderings). We fixed the truncation by saving full text in PostgreSQL.
