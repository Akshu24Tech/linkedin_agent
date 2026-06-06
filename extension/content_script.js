/**
 * content_script.js
 * ─────────────────
 * Runs inside LinkedIn pages in the user's real Chrome browser.
 * Extracts post data from activity pages and profile feeds.
 *
 * Triggered by popup.js via chrome.tabs.sendMessage().
 * Sends extracted posts back to popup via return value.
 */

// ── Helpers ────────────────────────────────────────────────────────────────────

function cleanText(text) {
  return (text || "").replace(/\s+/g, " ").trim();
}

function generatePostId(url, author, text) {
  // Stable ID: use activity ID from URL, or hash of author+first 100 chars
  const activityMatch = url.match(/activity[:\-](\d+)/);
  if (activityMatch) return `urn:li:activity:${activityMatch[1]}`;
  // Fallback: create a deterministic ID
  const raw = `${author}::${text.substring(0, 80)}`;
  let hash = 0;
  for (let i = 0; i < raw.length; i++) {
    hash = ((hash << 5) - hash) + raw.charCodeAt(i);
    hash |= 0;
  }
  return `ext_${Math.abs(hash)}`;
}

function getAuthorFromCard(card) {
  // Try multiple selectors for author name
  const selectors = [
    ".update-components-actor__name span[aria-hidden='true']",
    ".feed-shared-actor__name",
    ".update-components-actor__name",
    "[data-anonymize='person-name']",
    ".actor-name",
  ];
  for (const sel of selectors) {
    const el = card.querySelector(sel);
    if (el && el.innerText.trim()) return el.innerText.trim();
  }
  return "Post";
}

function getAuthorHeadline(card) {
  const selectors = [
    ".update-components-actor__description span[aria-hidden='true']",
    ".feed-shared-actor__sub-description",
    ".update-components-actor__description",
  ];
  for (const sel of selectors) {
    const el = card.querySelector(sel);
    if (el && el.innerText.trim()) return cleanText(el.innerText);
  }
  return "";
}

function getPostText(card) {
  // Expand "see more" if present before reading
  const seeMore = card.querySelector(
    "button.see-more, .feed-shared-inline-show-more-text button, " +
    "[aria-label='see more'], .update-components-text button"
  );
  if (seeMore) {
    try { seeMore.click(); } catch (_) {}
  }

  const selectors = [
    ".update-components-text .update-components-text__text-view",
    ".feed-shared-text .break-words",
    ".feed-shared-update-v2__description",
    ".update-components-text",
    ".feed-shared-text",
  ];
  for (const sel of selectors) {
    const el = card.querySelector(sel);
    if (el && el.innerText.trim().length > 30) return cleanText(el.innerText);
  }
  return "";
}

function getPostUrl(card) {
  // Try to find the permalink from the post timestamp link
  const selectors = [
    "a[href*='/activity-']",
    "a[href*='/feed/update/']",
    ".update-components-actor__meta a",
    "time a",
    ".feed-shared-actor__sub-description a",
  ];
  for (const sel of selectors) {
    const el = card.querySelector(sel);
    if (el && el.href && el.href.includes("linkedin.com")) {
      return el.href.split("?")[0]; // strip query params
    }
  }
  // Fallback: use current page URL if on a single post page
  if (window.location.href.includes("/activity-") ||
      window.location.href.includes("/feed/update/")) {
    return window.location.href.split("?")[0];
  }
  return window.location.href;
}

function getPostAge(card) {
  const selectors = [
    ".update-components-actor__sub-description span[aria-hidden='true']",
    ".feed-shared-actor__sub-description",
    "time",
    ".visually-hidden"
  ];
  for (const sel of selectors) {
    const el = card.querySelector(sel);
    if (el) {
      const t = cleanText(el.innerText || el.textContent || "");
      if (t.match(/\d+[mhdw]/i) || t.match(/(hour|day|week|minute)/i)) return t;
    }
  }
  return "";
}

function getEngagement(card) {
  let likes = "", comments = "";
  const likeEl = card.querySelector(
    "[aria-label*='reaction'], .social-details-social-counts__reactions-count, " +
    ".social-details-social-counts__count-value"
  );
  if (likeEl) likes = cleanText(likeEl.innerText || likeEl.getAttribute("aria-label") || "");

  const commentEl = card.querySelector(
    "[aria-label*='comment'], .social-details-social-counts__comments"
  );
  if (commentEl) comments = cleanText(commentEl.innerText || "");

  return { likes, comments };
}

function hasMedia(card, type) {
  if (type === "image") {
    return !!(card.querySelector(
      ".update-components-image, .feed-shared-image, img.ivm-view-attr__img--centered"
    ));
  }
  if (type === "video") {
    return !!(card.querySelector(
      ".update-components-linkedin-video, .feed-shared-linkedin-video, video"
    ));
  }
  return false;
}

// ── Main extractor ─────────────────────────────────────────────────────────────

function extractPostsFromPage() {
  const posts = [];

  // Find all post cards — multiple selectors for different LinkedIn layouts
  const cardSelectors = [
    ".feed-shared-update-v2",           // main feed
    ".occludable-update",               // activity tab
    ".update-components-update-v2",     // newer layout
    "div[data-urn*='activity']",        // urn-based
  ];

  let cards = [];
  for (const sel of cardSelectors) {
    cards = document.querySelectorAll(sel);
    if (cards.length > 0) break;
  }

  if (cards.length === 0) {
    return { posts: [], error: "No post cards found on this page. Make sure you are on a LinkedIn profile activity tab." };
  }

  const now = new Date().toISOString();

  for (const card of cards) {
    try {
      const text = getPostText(card);
      if (!text || text.length < 30) continue; // skip tiny/empty posts

      const author = getAuthorFromCard(card);
      const url = getPostUrl(card);
      const postId = generatePostId(url, author, text);
      const ageStr = getPostAge(card);
      const { likes, comments } = getEngagement(card);

      posts.push({
        post_id: postId,
        author_name: author,
        author_headline: getAuthorHeadline(card),
        post_text: text,
        post_url: url,
        has_image: hasMedia(card, "image"),
        has_video: hasMedia(card, "video"),
        likes_approx: likes,
        comments_approx: comments,
        extracted_at: now,
        screenshot_path: "",
        posted_at: ageStr,
        post_age_days: -1,
        source_page: window.location.href,
      });
    } catch (e) {
      console.warn("[LinkedIn Agent] Error parsing card:", e);
    }
  }

  return { posts, pageUrl: window.location.href };
}

// ── Message listener ───────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "extractPosts") {
    const result = extractPostsFromPage();
    sendResponse(result);
  }
  return true; // keep channel open for async
});
