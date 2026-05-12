const normalizeText = (value) =>
  (value || "")
    .toString()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();

const archiveInput = document.querySelector("#archive-search");
const archiveCards = Array.from(document.querySelectorAll("[data-search]"));

function filterArchive() {
  const query = normalizeText(archiveInput.value.trim());
  for (const card of archiveCards) {
    const haystack = normalizeText(card.dataset.search);
    card.hidden = query.length > 0 && !haystack.includes(query);
  }
}

if (archiveInput) {
  const params = new URLSearchParams(window.location.search);
  const initialQuery = params.get("q");
  if (initialQuery) {
    archiveInput.value = initialQuery;
  }
  archiveInput.addEventListener("input", filterArchive);
  filterArchive();
}

const homeInput = document.querySelector("#home-search");
const homeResults = document.querySelector("#home-search-results");
const homeStatus = document.querySelector("#home-search-status");
let postsPromise;
let homeSearchRun = 0;

function loadPosts() {
  if (!postsPromise) {
    postsPromise = fetch("data/posts.json")
      .then((response) => (response.ok ? response.json() : []))
      .catch(() => []);
  }
  return postsPromise;
}

function createPostCard(post) {
  const article = document.createElement("article");
  article.className = "post-card compact";
  article.dataset.search = normalizeText(post.search || `${post.title} ${post.excerpt}`);

  const thumb = document.createElement("a");
  thumb.className = "thumb";
  thumb.href = post.path;
  thumb.setAttribute("aria-hidden", "true");
  thumb.tabIndex = -1;

  if (post.image) {
    const image = document.createElement("img");
    image.src = post.image;
    image.alt = "";
    image.loading = "lazy";
    image.decoding = "async";
    thumb.append(image);
  } else {
    const placeholder = document.createElement("div");
    placeholder.className = "thumb-placeholder";
    const number = document.createElement("span");
    number.textContent = post.number;
    placeholder.append(number);
    thumb.append(placeholder);
  }

  const body = document.createElement("div");
  body.className = "post-card-body";

  const meta = document.createElement("p");
  meta.className = "post-card-meta";
  meta.textContent = `n. ${post.number} · ${post.date_label}`;
  if (post.comments) {
    meta.textContent += ` · ${post.comments} commenti`;
  }

  const title = document.createElement("h2");
  const link = document.createElement("a");
  link.href = post.path;
  link.textContent = post.title;
  title.append(link);

  const excerpt = document.createElement("p");
  excerpt.textContent = post.excerpt || "";

  body.append(meta, title, excerpt);
  article.append(thumb, body);
  return article;
}

async function renderHomeSearch() {
  const run = ++homeSearchRun;
  const query = normalizeText(homeInput.value.trim());
  homeResults.replaceChildren();

  if (!query) {
    homeResults.hidden = true;
    homeStatus.hidden = true;
    return;
  }

  const posts = await loadPosts();
  if (run !== homeSearchRun) {
    return;
  }

  const matches = posts.filter((post) =>
    normalizeText(post.search || `${post.title} ${post.excerpt}`).includes(query)
  );
  const visibleMatches = matches.slice(0, 12);

  for (const post of visibleMatches) {
    homeResults.append(createPostCard(post));
  }

  homeResults.hidden = visibleMatches.length === 0;
  homeStatus.hidden = false;
  if (matches.length === 0) {
    homeStatus.textContent = "Nessun articolo trovato.";
  } else if (matches.length > visibleMatches.length) {
    homeStatus.textContent = `Primi ${visibleMatches.length} di ${matches.length} risultati.`;
  } else {
    homeStatus.textContent = `${matches.length} risultati.`;
  }
}

if (homeInput && homeResults && homeStatus) {
  homeInput.addEventListener("input", renderHomeSearch);
  homeInput.form?.addEventListener("submit", (event) => {
    if (!homeInput.value.trim()) {
      event.preventDefault();
    }
  });
}
