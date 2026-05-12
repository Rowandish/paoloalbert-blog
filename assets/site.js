const input = document.querySelector("#archive-search");
const cards = Array.from(document.querySelectorAll("[data-search]"));

if (input) {
  input.addEventListener("input", () => {
    const query = input.value.trim().toLowerCase();
    for (const card of cards) {
      card.hidden = query.length > 0 && !card.dataset.search.includes(query);
    }
  });
}
