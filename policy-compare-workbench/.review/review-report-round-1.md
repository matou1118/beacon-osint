# Review Report — policy-compare-workbench

**Date**: 2026-09-26
**Reviewer**: code-review-fixer (isolated)
**Skill Path**: D:\tvbox\BeaconOSINT\policy-compare-workbench

## Overall Score: 7/10

## P1 Issues (Must Fix)

### P1-1: Field Schema Source Mismatch
- **File**: references/seed-topics.md
- **Issue**: The field schema in seed-topics.md should reference the actual `FIELD_SCHEMA` from `policy_fields.py` in the project, not a static copy. The seed topic should be consistent with the actual field extraction engine.
- **Fix**: Add a note that the field schema should match `policy_fields.py::FIELD_SCHEMA`.

### P1-2: Notice Type Naming Inconsistency
- **File**: references/data-models.md, SKILL.md
- **Issue**: The `notice_type` values in the data model (`new_region`, `update`, `revoke`, `field_change`) don't match the actual implementation in `compare_digest.py` (`new_links`, `field_changes`, `digest`).
- **Fix**: Update the data model to use the correct notice types: `new_links`, `field_changes`, `digest`.

### P1-3: Missing `updated_at` Column in `TopicDocLink`
- **File**: references/data-models.md
- **Issue**: The `TopicDocLink` table is missing the `updated_at` column, which is used in `compare_digest.py` for watermark-based diffing.
- **Fix**: Add `updated_at` column to the data model.

## P2 Issues (Should Fix)

### P2-1: Database Instance Reuse in Page Templates
- **File**: assets/page-templates/*.py
- **Issue**: Each page template creates a new `Database` instance. Should use a context manager or connection pool for better resource management.
- **Fix**: Add a note about using `with Database(db_path) as db:` pattern.

### P2-2: Table Name Case Sensitivity
- **File**: references/data-models.md
- **Issue**: Table names use `snake_case` but the actual implementation might use `PascalCase` for class names. Should clarify the mapping.
- **Fix**: Add a note that SQLAlchemy will use the class name as the table name (lowercase by default).

## Suggestions

### S-1: Add `channel` Column to `UserNotice`
- **File**: references/data-models.md
- **Suggestion**: Add a `channel` column to support different notification channels (in-app, email, push) in the future.

### S-2: Add `last_notified_at` to `TopicSubscription`
- **File**: references/data-models.md
- **Suggestion**: Add `last_notified_at` to track when the last notification was sent for a subscription.

### S-3: Add `metadata` JSON Column to All Tables
- **File**: references/data-models.md
- **Suggestion**: Add a `metadata` JSON column to all tables for future extensibility.

## Summary

The Skill correctly understands the requirement and has a solid architecture. The 4-phase workflow is clear and covers all required components. The page templates are well-structured and follow the project's existing patterns.

**Key Strengths**:
- Clear 4-phase workflow with explicit input/output for each step
- Good separation of concerns (data model, engine, pages, notifications)
- Reuses existing project components (SQLite, APScheduler, Announcement columns)
- Page templates are well-structured and follow the project's existing patterns

**Key Weaknesses**:
- Data model has some inconsistencies with the actual implementation
- Notice type naming is inconsistent between data model and implementation
- Missing `updated_at` column in `TopicDocLink` table

**Recommendation**: Fix P1 issues before using the Skill for implementation. P2 issues and suggestions can be addressed in a future iteration.
