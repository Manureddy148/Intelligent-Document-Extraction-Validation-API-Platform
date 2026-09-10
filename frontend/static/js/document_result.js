(function () {
  const apiPrefix = document.body.dataset.apiPrefix || "/api/v1";
  const documentName = document.body.dataset.documentName;

  const titleEl = document.getElementById("doc-title");
  const subtitleEl = document.getElementById("doc-subtitle");
  const fieldsCard = document.getElementById("fields-card");
  const fieldsGrid = document.getElementById("fields-grid");
  const lineItemsCard = document.getElementById("line-items-card");
  const lineItemsContainer = document.getElementById("line-items-container");
  const validationCard = document.getElementById("validation-card");
  const validationSummary = document.getElementById("validation-summary");
  const checksTbody = document.getElementById("checks-tbody");
  const jsonView = document.getElementById("json-view");
  const toggleJsonBtn = document.getElementById("toggle-json");

  function badgeFor(status) {
    const normalized = (status || "").toUpperCase();
    const cls = normalized === "PASS" ? "pass" : normalized === "FAILED" || normalized === "FAIL" ? "fail" : "na";
    return `<span class="badge ${cls}">${normalized || "UNKNOWN"}</span>`;
  }

  function formatValue(value) {
    if (value === null || value === undefined) return null;
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function renderFields(extractedData) {
    fieldsGrid.innerHTML = "";
    Object.entries(extractedData || {}).forEach(([key, field]) => {
      if (key === "line_items" || key === "statement_periods") return;
      if (!field || typeof field !== "object" || !("value" in field)) return;

      const item = document.createElement("div");
      const formatted = formatValue(field.value);
      const isMissing = formatted === null;
      item.className = `field-item${isMissing ? " missing" : ""}`;
      const pageInfo = field.page_number ? ` (p.${field.page_number})` : "";
      item.innerHTML = `
        <div class="label">${key.replace(/_/g, " ")}</div>
        <div class="value">${isMissing ? "Not found" : formatted}${pageInfo}</div>
      `;
      fieldsGrid.appendChild(item);
    });
    fieldsCard.hidden = fieldsGrid.children.length === 0;
  }

  function renderInvoiceLineItems(items) {
    if (!items || !items.length) return "";
    const rows = items
      .map(
        (li) => `<tr><td>${li.description}</td><td>${li.quantity}</td><td>${li.unit_price}</td><td>${li.amount}</td></tr>`
      )
      .join("");
    return `
      <h3>Line items</h3>
      <table>
        <thead><tr><th>Description</th><th>Qty</th><th>Unit price</th><th>Amount</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }

  function renderStatementLineItems(lineItemSections, periods) {
    if (!lineItemSections) return "";
    let html = "";
    Object.entries(lineItemSections).forEach(([sectionName, items]) => {
      if (!items || !items.length) return;
      const periodHeaders = (periods || []).map((p) => `<th>${p}</th>`).join("");
      const rows = items
        .map((item) => {
          const cells = (periods || [])
            .map((p) => `<td>${item.values && item.values[p] !== null && item.values[p] !== undefined ? item.values[p] : "-"}</td>`)
            .join("");
          return `<tr><td>${item.label}</td>${cells}</tr>`;
        })
        .join("");
      html += `
        <h3>${sectionName.replace(/_/g, " ")}</h3>
        <table>
          <thead><tr><th>Line item</th>${periodHeaders}</tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    });
    return html;
  }

  function renderChecks(validation) {
    checksTbody.innerHTML = "";
    (validation.checks || []).forEach((check) => {
      const row = document.createElement("tr");
      const statusClass = check.status === "FAIL" ? "fail" : check.status === "NOT_APPLICABLE" ? "na" : "";
      row.innerHTML = `
        <td>${check.name}</td>
        <td>${check.formula}</td>
        <td class="${statusClass}">${check.calculated_value ?? "-"}</td>
        <td class="${statusClass}">${check.reported_value ?? "-"}</td>
        <td class="${statusClass}">${check.variance ?? "-"}</td>
        <td>${badgeFor(check.status)}</td>
      `;
      checksTbody.appendChild(row);
    });
    validationSummary.innerHTML = `Overall status: ${badgeFor(validation.overall_status)}`;
    validationCard.hidden = (validation.checks || []).length === 0;
  }

  async function load() {
    try {
      const response = await fetch(`${apiPrefix}/documents/${encodeURIComponent(documentName)}`);
      const data = await response.json();
      if (!response.ok) {
        const message = data.error ? `${data.error.code}: ${data.error.message}` : "Failed to load document.";
        throw new Error(message);
      }

      titleEl.textContent = data.document_name;
      subtitleEl.innerHTML = `Type: ${data.document_type} &middot; Status: ${badgeFor(data.processing_status)} &middot; Processed at: ${new Date(
        data.processing_metadata.processed_at
      ).toLocaleString()} &middot; OCR used: ${data.processing_metadata.ocr_used ? "yes" : "no"}`;

      renderFields(data.extracted_data);

      const lineItems = data.extracted_data.line_items;
      if (Array.isArray(lineItems)) {
        lineItemsContainer.innerHTML = renderInvoiceLineItems(lineItems);
      } else if (lineItems && typeof lineItems === "object") {
        lineItemsContainer.innerHTML = renderStatementLineItems(lineItems, data.extracted_data.statement_periods);
      }
      lineItemsCard.hidden = !lineItemsContainer.innerHTML;

      renderChecks(data.validation);

      jsonView.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
      titleEl.textContent = documentName;
      subtitleEl.innerHTML = `<span class="error-text">${err.message}</span>`;
    }
  }

  toggleJsonBtn.addEventListener("click", () => {
    jsonView.hidden = !jsonView.hidden;
  });

  load();
})();
