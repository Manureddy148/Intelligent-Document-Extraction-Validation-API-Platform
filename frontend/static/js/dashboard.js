(function () {
  const apiPrefix = document.body.dataset.apiPrefix || "/api/v1";
  const tbody = document.getElementById("documents-tbody");
  const form = document.getElementById("upload-form");
  const statusEl = document.getElementById("upload-status");
  const submitBtn = document.getElementById("submit-btn");

  function badgeFor(status) {
    const normalized = (status || "").toUpperCase();
    const cls = normalized === "PASS" ? "pass" : normalized === "FAILED" || normalized === "FAIL" ? "fail" : "na";
    return `<span class="badge ${cls}">${normalized || "UNKNOWN"}</span>`;
  }

  async function loadDocuments() {
    tbody.innerHTML = "<tr><td colspan=\"4\">Loading...</td></tr>";
    try {
      const response = await fetch(`${apiPrefix}/documents`);
      if (!response.ok) throw new Error(`Failed to load documents (${response.status})`);
      const data = await response.json();
      if (!data.documents || data.documents.length === 0) {
        tbody.innerHTML = "<tr><td colspan=\"4\">No documents processed yet.</td></tr>";
        return;
      }
      tbody.innerHTML = "";
      data.documents.forEach((doc) => {
        const row = document.createElement("tr");
        row.className = "clickable-row";
        row.innerHTML = `
          <td>${doc.document_name}</td>
          <td>${doc.document_type}</td>
          <td>${badgeFor(doc.processing_status)}</td>
          <td>${new Date(doc.updated_at).toLocaleString()}</td>
        `;
        row.addEventListener("click", () => {
          window.location.href = `/documents/${encodeURIComponent(doc.document_name)}`;
        });
        tbody.appendChild(row);
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="4" class="error-text">${err.message}</td></tr>`;
    }
  }

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
      statusEl.textContent = `Processed "${data.document_name}" — status: ${data.processing_status}.`;
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
