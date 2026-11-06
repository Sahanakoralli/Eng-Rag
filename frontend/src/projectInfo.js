// The text you are most likely to edit. HomePage.jsx reads everything from here.
export const PROJECT = {
  name: "Retrieval Based Question Answering",
  shortName: "RAG QA",
  // Hero headline: two big lines, then the smaller tagline underneath.
  headline: ["Retrieval Based", "Question Answering"],
  tagline: "for Document Fetching",
  summary: "Ask questions about your documents. Get answers that point to the page.",

  // Optional: your own measured retrieval results, from `python -m engrag.cli evaluate --compare`.
  // Fill this in and the Evaluation section shows a results table; leave it empty and the table is hidden.
  // Example row: { method: "hybrid", hit1: 0.52, hit3: 0.71, hit5: 0.78, hit10: 0.85, recall10: 0.80, mrr: 0.63 }
  results: [],
  resultsNote: "", // e.g. "Construction domain, 200 PDFs, 150 questions, document-level scoring."
};
