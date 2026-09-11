(function () {
  const apiPrefix = document.body.dataset.apiPrefix || "/api/v1";
  const tbody = document.getElementById("documents-tbody");
  const form = document.getElementById("upload-form");
  const statusEl = document.getElementById("upload-status");
  const submitBtn = document.getElementById("submit-btn");
  const searchInput = document.getElementById("search");
  const countEl = document.getElementById("documents-count");

  let documents = [];

  // Document names come from uploaded file names, so they are attacker-controlled
  // and must never reach innerHTML raw.
  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  function badgeFor(status) {
    const normalized = (status || "").toUpperCase();
    const cls = normalized === "PASS" ? "pass" : normalized === "FAILED" || normalized === "FAIL" ? "fail" : "na";
    return `<span class="badge ${cls}">${escapeHtml(normalized || "UNKNOWN")}</span>`;
  }

  function render() {
    const query = (searchInput.value || "").trim().toLowerCase();
    const matches = query
      ? documents.filter(
          (doc) =>
            doc.document_name.toLowerCase().includes(query) ||
            doc.document_type.toLowerCase().includes(query) ||
            (doc.processing_status || "").toLowerCase().includes(query)
        )
      : documents;

    countEl.textContent = query
      ? `${matches.length} of ${documents.length} documents`
      : `${documents.length} document${documents.length === 1 ? "" : "s"}`;

    if (!matches.length) {
      const message = documents.length
        ? `No documents match "${escapeHtml(searchInput.value)}".`
        : "No documents processed yet.";
      tbody.innerHTML = `<tr><td colspan="4">${message}</td></tr>`;
      return;
    }

    tbody.innerHTML = "";
    matches.forEach((doc) => {
      const row = document.createElement("tr");
      row.className = "clickable-row";
      row.innerHTML = `
        <td>${escapeHtml(doc.document_name)}</td>
        <td>${escapeHtml(doc.document_type)}</td>
        <td>${badgeFor(doc.processing_status)}</td>
        <td>${escapeHtml(new Date(doc.updated_at).toLocaleString())}</td>
      `;
      row.addEventListener("click", () => {
        window.location.href = `/documents/${encodeURIComponent(doc.document_name)}`;
      });
      tbody.appendChild(row);
    });
  }

  async function loadDocuments() {
    tbody.innerHTML = '<tr><td colspan="4">Loading...</td></tr>';
    try {
      const response = await fetch(`${apiPrefix}/documents?limit=500`);
      if (!response.ok) throw new Error(`Failed to load documents (${response.status})`);
      const data = await response.json();
      documents = data.documents || [];
      render();
    } catch (err) {
      documents = [];
      countEl.textContent = "";
      tbody.innerHTML = `<tr><td colspan="4" class="error-text">${escapeHtml(err.message)}</td></tr>`;
    }
  }

  searchInput.addEventListener("input", render);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fileInput = document.getElementById("file");
    const documentType = document.getElementById("document_type").value;
    if (!fileInput.files.length) return;

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);
    formData.append("document_type", documentType);

    submitBtn.disabled = true;
    statusEl.textContent = "Processing... this may take a few seconds for scanned documents.";
    statusEl.className = "status-line";

    try {
      const response = await fetch(`${apiPrefix}/documents/process`, {
        method: "POST",
        body: formData,
      });
      const data = await response.json();
      if (!response.ok) {
        const message = data.error ? `${data.error.code}: ${data.error.message}` : "Processing failed.";
        throw new Error(message);
      }
      statusEl.textContent = `Processed "${data.document_name}" — status: ${data.processing_status}, validation: ${data.validation.overall_status}.`;
      form.reset();
      await loadDocuments();
    } catch (err) {
      statusEl.textContent = err.message;
      statusEl.className = "status-line error-text";
    } finally {
      submitBtn.disabled = false;
    }
  });

  loadDocuments();
})();
