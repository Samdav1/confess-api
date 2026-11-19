# CONFESS — THE LISTENING ROOM: Backend Specification

**Version:** 1.1  
**Status:** Implemented  

---

## 1. Domain Model & Emotional Principles

- **Privacy by Design:** Authenticated `user_id`, `email`, and account identifiers are strictly decoupled in all public endpoints. Public payloads only expose user-selected pseudonyms (`author_label`, e.g. `"A quiet heart"`, `"A kind stranger"`).
- **Dignity over Gamification:** No vanity leaderboards, engagement hacks, or competitive rankings.
- **Product Terminology Alignment:**
  - **The Listening Room:** Community feed of published Thoughts
  - **A Thought:** Anonymous member reflection (`listening_room_thoughts`)
  - **A Gentle Word:** Supportive response (`listening_room_gentle_words`), held in `pending_review` until Care Desk approval
  - **Acknowledgements:** Structured empathy toggles (`i_hear_you`, `sending_warmth`, `you_are_not_alone`, etc.)
  - **The Care Desk:** Admin-only moderation & audit console
  - **Kindness Points & Little Perks:** Immutable ledger rewards redeemable for discounts on Confess Celebrations

---

## 2. Database Schema

### `listening_room_thoughts` (`Thought`)
- `id` (UUID PK)
- `author_user_id` (UUID FK -> `users.id`, indexed, private)
- `author_label` (VARCHAR, default `"A quiet heart"`)
- `body` (TEXT, 5–3000 chars)
- `category` (VARCHAR nullable, indexed; supports `gentle_reminder`, `quiet_confession`, `little_joys`, `late_night_thought`, `relationships`, `family`, `work`, `personal`, `health`, `grief`, `identity`, `other`)
- `tone` (VARCHAR, default `"cream"`; supports `pink`, `cream`, `peach`, `mauve`, `wine`)
- `content_hash` (VARCHAR nullable, indexed for duplicate detection)
- `status` (VARCHAR, default `"published"`, indexed; `published`, `pending`, `rejected`, `hidden`, `removed`, `flagged`, `deleted`)
- `moderation_status` (VARCHAR, indexed)
- `likes_count` (INTEGER, default `0`, updated atomically)
- `gentle_words_count` (INTEGER, default `0`, updated atomically)
- `is_urgent_flagged` (BOOLEAN, default `false`)
- `created_at`, `updated_at`, `published_at`, `deleted_at` (TIMESTAMPTZ)
- Composite indexes: `(status, published_at, id)`, `(status, created_at)`, `(category, status, created_at)`, `(author_user_id, created_at)`

### `listening_room_thought_likes` (`ThoughtLike`)
- `id` (UUID PK), `thought_id` (UUID FK), `user_id` (UUID FK), `created_at` (TIMESTAMPTZ)
- Constraint: `UNIQUE (thought_id, user_id)`

### `listening_room_acknowledgements` (`ThoughtAcknowledgement`)
- `id` (UUID PK), `thought_id` (UUID FK), `user_id` (UUID FK), `acknowledgement_type` (VARCHAR), `created_at` (TIMESTAMPTZ)
- Constraint: `UNIQUE (thought_id, user_id, acknowledgement_type)`

### `listening_room_gentle_words` (`GentleWord`)
- `id` (UUID PK), `thought_id` (UUID FK), `author_user_id` (UUID FK, private), `author_label` (VARCHAR, default `"A kind stranger"`)
- `body` (TEXT), `content_hash` (VARCHAR), `status` (VARCHAR, default `"pending_review"`)
- `moderated_by` (UUID FK -> `admins.id`), `moderated_at` (TIMESTAMPTZ), `moderation_note` (TEXT)
- `points_awarded` (INTEGER), `idempotency_key` (VARCHAR UNIQUE)

### Kindness Points, Perks, Reports & Audit
- `kindness_points_policies` (`KindnessPointsPolicy`)
- `user_kindness_balances` (`UserKindnessBalance`)
- `kindness_points_ledger` (`KindnessPointsLedger`, append-only)
- `little_perks` (`LittlePerk`)
- `perk_redemptions` (`PerkRedemption`)
- `content_reports` (`ContentReport`)
- `moderation_audit_logs` (`ModerationAuditLog`, append-only)

---

## 3. REST API Contracts (`/api/v1`)

### Feed, Thoughts & Interactions (supports both `/api/v1/thoughts/*` and `/api/v1/listening-room/thoughts/*`)
- `GET /api/v1/thoughts` — Paginated published feed (`category`, `cursor`, `limit`, `page`, `pageSize`), includes `isLiked`, `userAcknowledgements`, `acknowledgementsCount`.
- `POST /api/v1/thoughts` — Create Thought (`content` or `body`, `category`, `authorLabel`, `tone`, `acceptGuidelines`).
- `GET /api/v1/thoughts/{id}` — Thought detail + approved Gentle Words.
- `DELETE /api/v1/thoughts/{id}` — Owner soft-delete.
- `POST /api/v1/thoughts/{id}/likes` — Atomic like toggle (`likes_count = likes_count ± 1`).
- `POST /api/v1/thoughts/{id}/acknowledgements` — Toggle structured empathy acknowledgement (`i_hear_you`, `sending_warmth`, `you_are_not_alone`, etc.).
- `POST /api/v1/thoughts/{id}/gentle-words` — Submit Gentle Word into `pending_review`.
- `GET /api/v1/thoughts/{id}/gentle-words` — Public paginated list of approved Gentle Words.
- `POST /api/v1/thoughts/{id}/reports` & `POST /api/v1/content-reports` — Submit moderation report.

### Member Profile, Points & Little Perks
- `GET /api/v1/user/thoughts` & `GET /api/v1/me/thoughts` — Authenticated user's shared Thoughts.
- `GET /api/v1/user/gentle-words` & `GET /api/v1/me/gentle-words` — Authenticated user's Gentle Word submissions and moderation statuses.
- `GET /api/v1/me/kindness-points` — Current Kindness Points balance + paginated ledger.
- `GET /api/v1/little-perks` — Active Little Perk offers.
- `POST /api/v1/little-perks/{id}/redeem` — Atomic point debit & code generation (`idempotencyKey`).
- `GET /api/v1/me/redemptions` — User's redeemed perk codes.
- `POST /api/v1/redemptions/{code}/apply`, `/consume`, `/release` — Checkout reservation lifecycle integrated with Paystack.

### The Care Desk (Admin Moderation Console)
- `GET /api/v1/admin/care-desk/thoughts?status=pending`
- `GET /api/v1/admin/care-desk/gentle-words?status=pending_review`
- `PATCH` / `POST /api/v1/admin/care-desk/gentle-words/{id}/approve` (and `/api/v1/admin/gentle-words/{id}/approve`)
- `PATCH` / `POST /api/v1/admin/care-desk/gentle-words/{id}/reject` (and `/api/v1/admin/gentle-words/{id}/reject`)
- `PATCH` / `POST /api/v1/admin/care-desk/thoughts/{id}/approve` & `reject`
- `POST /api/v1/admin/content/{type}/{id}/hide`
- `GET /api/v1/admin/reports` & `POST /api/v1/admin/reports/{id}/resolve`
- `POST /api/v1/admin/users/{id}/posting-restrictions`
- `POST /api/v1/admin/kindness-points/adjust`
- `GET` / `PATCH /api/v1/admin/kindness-points/policy`
- `GET` / `POST` / `PATCH /api/v1/admin/little-perks`
- `GET /api/v1/admin/listening-room/analytics`
