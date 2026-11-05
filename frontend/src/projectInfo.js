// The text you are most likely to edit. HomePage.jsx reads everything from here.
export const PROJECT = {
  name: "Engineering RAG",
  tagline: "Ask questions about engineering documents. Get answers that point to the page.",
  summary:
    "Upload specifications, standards and reports, or search a library you indexed in advance. Every answer is built " +
    "from passages found in your documents and cites them, so you can check the source in one click.",

  // Optional: your own measured retrieval results, from `python -m engrag.cli evaluate --compare`.
  // Fill this in and the Evaluation section shows a results table; leave it empty and the table is hidden.
  // Example row: { method: "hybrid", hit1: 0.52, hit3: 0.71, hit5: 0.78, hit10: 0.85, recall10: 0.80, mrr: 0.63 }
  results: [],
  resultsNote: "", // e.g. "Construction domain, 200 PDFs, 150 questions, document-level scoring."
};
