/**
 * api/modules/library.js — Strategy Library / Marketplace API Module
 *
 * Endpoints: /api/library/* (`backend_app/routers/library.py`, mounted in `main.py` with
 * `prefix="/api/library"`).
 *
 * Every path below was derived from a real `@router.<verb>` / `@literal_router.<verb>`
 * decorator in that router; nothing here is assembled from a guess. The module constructs
 * NO client, NO base URL and NO host — it travels the shared `../../apiClient` like every
 * other module, so retries, the circuit breaker, request de-duplication, the token header
 * and the error envelope are the same ones the rest of the product gets (Requirement 20.2).
 *
 * TWO METHODS THE SPEC LISTS ARE ABSENT, DELIBERATELY
 * --------------------------------------------------
 * `submissions.list` (the owner's own Submission list) and `submissions.withdraw` have no
 * route in `backend_app/routers/library.py`. The router exposes exactly one owner-scoped
 * submission read — `GET /submissions/{submission_id}` — and no withdrawal verb at all
 * (`SubmissionAction` covers approve/reject/publish/suspend/unpublish, all admin-only).
 * A method pointing at a path that does not exist is a call that fails at runtime, so
 * neither is written here until the backend route lands.
 *
 * `settings` is a **PATCH**, not the PUT the design sketch shows: the route is
 * `@literal_router.patch("/{library_id}/settings")`. `patch` is exported by `../../apiClient`
 * alongside the rest, so the shared-client rule is unaffected.
 */

import { get, post, patch, publicGet } from '../../apiClient';

/**
 * @typedef {Object} BrowseParams
 * @property {number} [page] - 1-based, `>= 1`.
 * @property {number} [limit] - 1..50, default 20.
 * @property {string} [sort] - clones|rating|sharpe|return|newest|featured.
 * @property {string} [category]
 * @property {string} [difficulty]
 * @property {boolean} [has_ml]
 * @property {number} [min_sharpe]
 * @property {number} [min_return]
 * @property {string} [tags] - Comma-separated tag filter.
 * @property {string} [q] - Name/description search.
 */

/**
 * @typedef {Object} ListingCard
 * @property {string} listing_id - The Listing identifier. Never `id`; the public projection
 *   (`listing_projection.project_listing`) emits `listing_id`.
 * @property {string} name
 * @property {string} category
 * @property {string} difficulty
 * @property {number} [avg_rating] - **Omitted** when `rating_count` is 0.
 * @property {number} [rating_count] - **Omitted** when 0.
 * @property {boolean} [user_has_cloned] - Present only for an authenticated caller.
 * @property {number|null} [user_rating] - Present only for an authenticated caller.
 */

/**
 * @typedef {Object} BrowseResponse
 * @property {ListingCard[]} items
 * @property {number} total
 * @property {number} page
 * @property {number} limit
 */

/**
 * @typedef {Object} SubmissionCreateBody
 * @property {string} strategy_id - UUID of the owner's strategy.
 * @property {string[]} backtest_ids - 3..10 Backtest_Evidence UUIDs.
 */

/**
 * @typedef {Object} PriceRangeResponse
 * @property {string} submission_id
 * @property {string} currency
 * @property {number} minimum_price_minor - Integer Minor_Units.
 * @property {number} recommended_price_minor - Integer Minor_Units.
 * @property {number} maximum_price_minor - Integer Minor_Units.
 * @property {string} evaluator_version
 */

/**
 * @typedef {Object} RatingBody
 * @property {number} rating - 1..5.
 * @property {string} [review_text] - Up to 500 characters.
 */

/**
 * @typedef {Object} LibrarySettingsBody
 * @property {boolean} source_cloning_enabled - The owner's clone-consent switch.
 */

/**
 * Repeated-key query string for `GET /api/library/admin/submissions`.
 *
 * `state` is `Optional[List[str]] = Query(None)` server-side, which FastAPI reads as a
 * **repeated** `?state=A&state=B`. Axios' default array serialisation (`state[]=A`) does not
 * parse into that, so the string is built here rather than handed to `config.params`.
 *
 * @param {{state?: (string|string[]), page?: number, pageSize?: number}} [params]
 * @returns {string} The query string including its leading `?`, or `''` when empty.
 */
const adminSubmissionsQuery = ({ state, page, pageSize } = {}) => {
  const query = new URLSearchParams();
  const states = state === undefined || state === null
    ? []
    : (Array.isArray(state) ? state : [state]);
  for (const value of states) {
    if (value) query.append('state', value);
  }
  if (page !== undefined) query.set('page', String(page));
  if (pageSize !== undefined) query.set('page_size', String(pageSize));
  const suffix = query.toString();
  return suffix ? `?${suffix}` : '';
};

/** The caller's own Submissions, owner-scoped. */
const submissions = {
  /**
   * Create a Submission for one of the caller's strategies.
   *
   * `POST /api/library/submissions` (`create_submission_route`). The Eligibility_Gate decides:
   * a criteria failure is a 422 `MARKETPLACE_ELIGIBILITY_FAILED` naming **every** failed
   * criterion in `details.failures`, while a read that did not complete is a 503
   * `MARKETPLACE_ELIGIBILITY_UNEVALUABLE` — distinct by code, and neither creates a row.
   *
   * @param {SubmissionCreateBody} body
   * @returns {Promise<{submission_id: string, submission_state: string, message: string}>}
   */
  create: (body) => post('/api/library/submissions', body),

  /**
   * One of the caller's own Submissions.
   *
   * `GET /api/library/submissions/{submission_id}` (`get_own_submission`). The read is scoped
   * to the caller's `owner_id`, so another owner's Submission is answered with the same 404
   * `MARKETPLACE_SUBMISSION_NOT_FOUND` an absent one gets — no existence oracle.
   *
   * @param {string} submissionId
   * @returns {Promise<{submission: Object}>}
   */
  get: (submissionId) =>
    get(`/api/library/submissions/${encodeURIComponent(submissionId)}`),

  /**
   * Compute and persist the Price_Range for the caller's Submission.
   *
   * `POST /api/library/submissions/{submission_id}/price-range` (`submission_price_range`).
   * The range is computed from the immutable Backtest_Evidence and a
   * `marketplace_price_evaluations` row is persisted with the inputs digest, so
   * {@link submissions.setPrice} has something to enforce against. Missing pricing inputs are
   * a 422 `MARKETPLACE_PRICE_EVIDENCE_MISSING` that persists nothing. Rate-limited 30/60s.
   *
   * @param {string} submissionId
   * @param {string} currency - 3..8 characters, e.g. `"USD"` or `"INR"`.
   * @returns {Promise<PriceRangeResponse>}
   */
  priceRange: (submissionId, currency) =>
    post(
      `/api/library/submissions/${encodeURIComponent(submissionId)}/price-range`,
      { currency },
    ),

  /**
   * Set the Listing price for the caller's Submission — the single enforcement point.
   *
   * `POST /api/library/submissions/{submission_id}/price` (`submission_set_price`). The price
   * is an **integer number of Minor_Units** (1998 is `$19.98`); no major-unit float travels
   * this path. A value outside `minimum..maximum` is a 400 `MARKETPLACE_PRICE_OUT_OF_RANGE`
   * carrying the permitted range, and no price is written. Rate-limited 30/60s.
   *
   * @param {string} submissionId
   * @param {number} priceMinor - Integer Minor_Units, `>= 1`.
   * @param {string} currency - 3..8 characters, e.g. `"USD"` or `"INR"`.
   * @returns {Promise<Object>}
   */
  setPrice: (submissionId, priceMinor, currency) =>
    post(
      `/api/library/submissions/${encodeURIComponent(submissionId)}/price`,
      { price_minor: priceMinor, currency },
    ),

  // `list` and `withdraw` are absent on purpose — see the module header. There is no
  // `GET /api/library/submissions` and no withdrawal route in routers/library.py, so there is
  // no path for either method to call.
};

/** Admin_Reviewer moderation. Authorisation is `get_admin_user` server-side, never a client flag. */
const admin = {
  /**
   * The moderation queue.
   *
   * `GET /api/library/admin/submissions` (`admin_list_submissions`). Ordered
   * `submitted_at DESC, id DESC`; `page_size` defaults to 25 and is **clamped** to 100 rather
   * than rejected. Omitting `state` returns all states. A read that did not complete is a 503
   * `MARKETPLACE_READ_FAILED`, never an empty queue.
   *
   * @param {{state?: (string|string[]), page?: number, pageSize?: number}} [params]
   * @returns {Promise<{items: Object[], total: number, page: number, page_size: number}>}
   */
  listSubmissions: (params) =>
    get(`/api/library/admin/submissions${adminSubmissionsQuery(params)}`),

  /**
   * The reviewer detail for one Submission.
   *
   * `GET /api/library/admin/submissions/{submission_id}` (`admin_submission_detail`). Reads
   * the immutable `marketplace_backtest_evidence` copy only, so no Protected_Logic column can
   * appear in the response.
   *
   * @param {string} submissionId
   * @returns {Promise<Object>}
   */
  getSubmission: (submissionId) =>
    get(`/api/library/admin/submissions/${encodeURIComponent(submissionId)}`),

  /**
   * `SUBMITTED → UNDER_REVIEW → APPROVED` (or `UNDER_REVIEW → APPROVED`).
   *
   * `POST /api/library/admin/submissions/{submission_id}/approve`. An illegal edge is a 409
   * naming the rejected transition; the transition and its audit entry are written atomically.
   *
   * @param {string} submissionId
   * @returns {Promise<{submission_id: string, submission_state: string}>}
   */
  approve: (submissionId) =>
    post(`/api/library/admin/submissions/${encodeURIComponent(submissionId)}/approve`),

  /**
   * `→ REJECTED`, with a recorded reason.
   *
   * `POST /api/library/admin/submissions/{submission_id}/reject`. The reason is trimmed
   * server-side and must be 1..2000 characters; a missing or whitespace-only one is a 422 and
   * nothing is written.
   *
   * @param {string} submissionId
   * @param {string} reason - 1..2000 characters after trimming.
   * @returns {Promise<{submission_id: string, submission_state: string}>}
   */
  reject: (submissionId, reason) =>
    post(
      `/api/library/admin/submissions/${encodeURIComponent(submissionId)}/reject`,
      { reason },
    ),

  /**
   * `APPROVED → PUBLISHED` or `SUSPENDED → PUBLISHED` — the edge that makes the Listing visible.
   *
   * `POST /api/library/admin/submissions/{submission_id}/publish`.
   *
   * @param {string} submissionId
   * @returns {Promise<{submission_id: string, submission_state: string}>}
   */
  publish: (submissionId) =>
    post(`/api/library/admin/submissions/${encodeURIComponent(submissionId)}/publish`),

  /**
   * `PUBLISHED → SUSPENDED` — reversible withdrawal; existing Subscriptions run on.
   *
   * `POST /api/library/admin/submissions/{submission_id}/suspend`.
   *
   * @param {string} submissionId
   * @returns {Promise<{submission_id: string, submission_state: string}>}
   */
  suspend: (submissionId) =>
    post(`/api/library/admin/submissions/${encodeURIComponent(submissionId)}/suspend`),

  /**
   * `PUBLISHED → UNPUBLISHED` or `SUSPENDED → UNPUBLISHED` — terminal, no edge out.
   *
   * `POST /api/library/admin/submissions/{submission_id}/unpublish`.
   *
   * @param {string} submissionId
   * @returns {Promise<{submission_id: string, submission_state: string}>}
   */
  unpublish: (submissionId) =>
    post(`/api/library/admin/submissions/${encodeURIComponent(submissionId)}/unpublish`),
};

export const libraryApi = {
  /**
   * Browse the public catalogue as the authenticated caller.
   *
   * `GET /api/library` (`browse_library`, route path `""` under the `/api/library` prefix).
   * Auth is optional server-side; travelling `get` means the token is sent when there is one,
   * which is what folds `user_has_cloned` and `user_rating` onto each card.
   *
   * @param {BrowseParams} [params]
   * @returns {Promise<BrowseResponse>}
   */
  browse: (params) => get('/api/library', { params }),

  /**
   * Browse the public catalogue with no credential at all (landing/marketing surfaces).
   *
   * Same route as {@link libraryApi.browse}; `publicGet` sends no `Authorization` header, so
   * the response carries no caller context.
   *
   * @param {BrowseParams} [params]
   * @returns {Promise<BrowseResponse>}
   */
  browsePublic: (params) => publicGet('/api/library', params),

  /**
   * Featured Listings.
   *
   * `GET /api/library/featured` (`get_featured_strategies`). `limit` is validated 1..10
   * server-side; a value outside it is a 422, not a clamp.
   *
   * @param {number} [limit=3] - 1..10.
   * @returns {Promise<{items: ListingCard[], total: number}>}
   */
  featured: (limit = 3) => get(`/api/library/featured?limit=${limit}`),

  /**
   * Featured Listings, unauthenticated.
   *
   * @param {number} [limit=3] - 1..10.
   * @returns {Promise<{items: ListingCard[], total: number}>}
   */
  featuredPublic: (limit = 3) => publicGet(`/api/library/featured?limit=${limit}`),

  /**
   * Trending Listings.
   *
   * `GET /api/library/trending` (`get_trending_strategies`). `limit` is validated 1..20
   * server-side.
   *
   * @param {number} [limit=10] - 1..20.
   * @returns {Promise<{items: ListingCard[], total: number}>}
   */
  trending: (limit = 10) => get(`/api/library/trending?limit=${limit}`),

  /**
   * Trending Listings, unauthenticated.
   *
   * @param {number} [limit=10] - 1..20.
   * @returns {Promise<{items: ListingCard[], total: number}>}
   */
  trendingPublic: (limit = 10) => publicGet(`/api/library/trending?limit=${limit}`),

  /**
   * Catalogue categories with their Listing counts.
   *
   * `GET /api/library/categories` (`get_categories`). Takes no parameters.
   *
   * @returns {Promise<Object>}
   */
  categories: () => get('/api/library/categories'),

  /**
   * Catalogue categories, unauthenticated.
   *
   * @returns {Promise<Object>}
   */
  categoriesPublic: () => publicGet('/api/library/categories'),

  /**
   * One Listing's public view.
   *
   * `GET /api/library/{library_id}` (`get_library_detail`). The handler **requires**
   * authentication (`Depends(get_current_user)`) and is rate-limited 100/minute. A Listing
   * whose Submission is not PUBLISHED answers a non-owner with the same status and body an
   * unknown identifier gets; the owner still reads their own Listing in any state.
   *
   * @param {string} listingId
   * @returns {Promise<Object>}
   */
  detail: (listingId) => get(`/api/library/${encodeURIComponent(listingId)}`),

  /**
   * One Listing's public view with no credential.
   *
   * Same route as {@link libraryApi.detail}. **Note:** that route carries
   * `Depends(get_current_user)`, so an unauthenticated call is answered 401 — this method
   * exists for the unauthenticated surfaces the design names, and it will start returning a
   * body once the route's auth requirement is relaxed. Nothing is faked client-side in the
   * meantime.
   *
   * @param {string} listingId
   * @returns {Promise<Object>}
   */
  detailPublic: (listingId) => publicGet(`/api/library/${encodeURIComponent(listingId)}`),

  /**
   * The caller's owned strategies and every Listing they hold a Subscription to, in one list.
   *
   * `GET /api/library/my-strategies` (`my_strategies`, rate-limited 120/60s). Each entry
   * carries a **server-derived** `ownership` (`OWNED` | `SUBSCRIBED`) and, when `SUBSCRIBED`,
   * `subscription: {state, period_expiry, renewal_state}` plus a server-computed
   * `allowed_actions` — none of which the page infers for itself. Takes no parameters.
   *
   * @returns {Promise<{items: Object[], total: number}>}
   */
  myStrategies: () => get('/api/library/my-strategies'),

  /** The caller's own Submissions. @see submissions */
  submissions,

  /** Admin_Reviewer moderation. @see admin */
  admin,

  /**
   * Create a payment checkout session for a Marketplace Subscription.
   *
   * `POST /api/library/{library_id}/checkout` (`create_marketplace_checkout`). The amount is
   * the Listing's stored `price_minor`, unchanged — the server computes no money from a
   * client value, and nothing here sends one. The Subscription becomes ACTIVE only when the
   * provider webhook confirms the payment and the Settlement_Record is written.
   *
   * @param {string} listingId
   * @param {string} [currency='USD'] - `"USD"` or `"INR"`; the server pattern-checks it.
   * @returns {Promise<Object>} The provider session, plus the amount in Minor_Units.
   */
  checkout: (listingId, currency = 'USD') =>
    post(`/api/library/${encodeURIComponent(listingId)}/checkout`, { currency }),

  /**
   * The caller's Subscription state for one Listing.
   *
   * `GET /api/library/{library_id}/subscribe` (`get_subscription_status`).
   *
   * @param {string} listingId
   * @returns {Promise<Object>}
   */
  subscriptionStatus: (listingId) =>
    get(`/api/library/${encodeURIComponent(listingId)}/subscribe`),

  /**
   * Cancel renewal on one of the caller's Subscriptions.
   *
   * `POST /api/library/subscriptions/{sub_id}/cancel` (`cancel_subscription`). Entitlement
   * runs to the **unchanged** current expiry — this stops the next charge, it does not revoke
   * the period already paid for. Takes no body.
   *
   * @param {string} subscriptionId
   * @returns {Promise<Object>}
   */
  cancelSubscription: (subscriptionId) =>
    post(`/api/library/subscriptions/${encodeURIComponent(subscriptionId)}/cancel`),

  /**
   * Open a payment session for the next Subscription_Period.
   *
   * `POST /api/library/subscriptions/{sub_id}/renew` (`renew_subscription`). It changes no
   * state: the transition into ACTIVE and the new expiry happen in exactly one place, on a
   * confirmed payment, inside `settlement_service.settle`.
   *
   * **No currency argument.** The handler's signature is `(sub_id, user)` — it declares no
   * request body, so a currency sent here would be read by nothing.
   * `checkout_service.create_renewal_checkout` derives the currency from the Subscription's
   * own stored record. Renewing early extends from the stored `period_expiry`, never from
   * `now`, so it buys the next month rather than shortening this one.
   *
   * @param {string} subscriptionId
   * @returns {Promise<Object>} The provider session, plus the renewal amount in Minor_Units.
   */
  renewSubscription: (subscriptionId) =>
    post(`/api/library/subscriptions/${encodeURIComponent(subscriptionId)}/renew`),

  /**
   * Clone a published Listing's strategy into the caller's own workspace.
   *
   * `POST /api/library/{library_id}/clone` (`clone_strategy`, rate-limited 20/minute). Takes
   * no body. Two gates precede any write: the owner's `source_cloning_enabled` consent (403
   * `MARKETPLACE_CLONING_DISABLED`) and an entitling Subscription; a read that did not
   * complete is a 503, never a 403. Already cloned is idempotent — the existing clone is
   * returned.
   *
   * @param {string} listingId
   * @returns {Promise<Object>} The caller's new (or existing) strategy row.
   */
  clone: (listingId) => post(`/api/library/${encodeURIComponent(listingId)}/clone`),

  /**
   * Submit or update the caller's rating for a Listing.
   *
   * `POST /api/library/{library_id}/rate` (`rate_strategy`, rate-limited 20/minute). Only a
   * caller with a verified clone of that Listing may rate it.
   *
   * @param {string} listingId
   * @param {RatingBody} body
   * @returns {Promise<Object>}
   */
  rate: (listingId, body) =>
    post(`/api/library/${encodeURIComponent(listingId)}/rate`, body),

  /**
   * Toggle the owner's clone-consent switch on their own Listing.
   *
   * `PATCH /api/library/{library_id}/settings` (`update_library_settings`, rate-limited
   * 20/60s) — a **PATCH**, single-column, idempotent. Owner-scoped with no existence oracle:
   * another owner's Listing and an absent one get the identical 404. The change is recorded
   * in one audit entry carrying only the boolean and the Listing id.
   *
   * @param {string} listingId
   * @param {LibrarySettingsBody} body
   * @returns {Promise<Object>}
   */
  settings: (listingId, body) =>
    patch(`/api/library/${encodeURIComponent(listingId)}/settings`, body),

  /**
   * The caller's own creator earnings and catalogue figures.
   *
   * `GET /api/library/creator/analytics` (`creator_analytics`). Every figure is read from
   * persisted data; a read that does not complete is refused rather than zero-filled, so an
   * absent figure means "not measured", not "zero". Takes no parameters.
   *
   * @returns {Promise<Object>}
   */
  creatorAnalytics: () => get('/api/library/creator/analytics'),

  /**
   * The caller's own Subscriptions and what they are committed to paying, per currency.
   *
   * `GET /api/library/subscriber/analytics` (`subscriber_analytics`). Amounts are integer
   * Minor_Units grouped by currency — never summed across currencies. Takes no parameters.
   *
   * @returns {Promise<Object>}
   */
  subscriberAnalytics: () => get('/api/library/subscriber/analytics'),
};

// Legacy compatibility
export const libraryEndpoints = libraryApi;
