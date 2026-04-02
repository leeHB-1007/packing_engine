const form = document.getElementById("packForm");
const specForm = document.getElementById("specForm");
const sampleBtn = document.getElementById("sampleBtn");
const resetBtn = document.getElementById("resetBtn");
const submitBtn = document.getElementById("submitBtn");
const downloadBtn = document.getElementById("downloadBtn");
const specSubmitBtn = document.getElementById("specSubmitBtn");

const shippingMethod = document.getElementById("shippingMethod");
const packingListNeeded = document.getElementById("packingListNeeded");
const orderText = document.getElementById("orderText");
const specQuery = document.getElementById("specQuery");

const statusBadge = document.getElementById("statusBadge");
const specStatusBadge = document.getElementById("specStatusBadge");
const successValue = document.getElementById("successValue");
const shippingValue = document.getElementById("shippingValue");
const packingListValue = document.getElementById("packingListValue");
const engineValue = document.getElementById("engineValue");
const normalizedOutput = document.getElementById("normalizedOutput");
const resultOutput = document.getElementById("resultOutput");
const tableSummary = document.getElementById("tableSummary");
const resultTableBody = document.getElementById("resultTableBody");
const specSummary = document.getElementById("specSummary");
const itemSpecList = document.getElementById("itemSpecList");
const fullboxSpecList = document.getElementById("fullboxSpecList");

const sampleOrder = `재포장
패킹리스트 no
1. A100110 / 15
2. 리체스 TX100 / 50
3. A100520 / 20`;

function setStatus(type, label) {
  statusBadge.className = `status-badge ${type}`;
  statusBadge.textContent = label;
}

function setSpecStatus(type, label) {
  specStatusBadge.className = `status-badge ${type}`;
  specStatusBadge.textContent = label;
}

function setIdleState() {
  setStatus("idle", "대기 중");
  successValue.textContent = "-";
  shippingValue.textContent = "-";
  packingListValue.textContent = "-";
  engineValue.textContent = "-";
  normalizedOutput.textContent = "아직 결과가 없습니다.";
  resultOutput.textContent = "아직 결과가 없습니다.";
  tableSummary.textContent = "아직 결과가 없습니다.";
  resultTableBody.innerHTML = '<tr><td colspan="9" class="empty-cell">아직 결과가 없습니다.</td></tr>';
}

function setSpecIdleState() {
  setSpecStatus("idle", "대기 중");
  specSummary.textContent = "아직 조회한 상품이 없습니다.";
  itemSpecList.innerHTML = '<div class="spec-empty">아직 조회한 상품이 없습니다.</div>';
  fullboxSpecList.innerHTML = '<div class="spec-empty">아직 조회한 상품이 없습니다.</div>';
}

function setLoadingState() {
  setStatus("loading", "처리 중");
  submitBtn.disabled = true;
  submitBtn.textContent = "계산 중...";
}

function clearLoadingState() {
  submitBtn.disabled = false;
  submitBtn.textContent = "패킹 결과 확인";
  downloadBtn.disabled = false;
  downloadBtn.textContent = "엑셀 다운로드";
}

function setSpecLoadingState() {
  specSubmitBtn.disabled = true;
  specSubmitBtn.textContent = "조회 중...";
  setSpecStatus("loading", "조회 중");
}

function clearSpecLoadingState() {
  specSubmitBtn.disabled = false;
  specSubmitBtn.textContent = "상품 스펙 조회";
}

async function copyText(targetId) {
  const target = document.getElementById(targetId);
  const text = target.textContent || "";

  try {
    await navigator.clipboard.writeText(text);
    const prev = statusBadge.textContent;
    setStatus("success", "복사됨");
    window.setTimeout(() => {
      if (prev === "에러") {
        setStatus("error", prev);
        return;
      }
      if (prev === "완료") {
        setStatus("success", prev);
        return;
      }
      setStatus("idle", "대기 중");
    }, 900);
  } catch (error) {
    console.error(error);
  }
}

function formatWeight(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }

  const num = Number(value);
  if (Number.isNaN(num)) {
    return String(value);
  }

  return num.toFixed(1).replace(/\.?0+$/, "");
}

function formatMaybeNumber(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }

  const num = Number(value);
  if (Number.isNaN(num)) {
    return String(value);
  }

  return num.toFixed(3).replace(/\.?0+$/, "");
}

function formatBooleanLabel(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return value ? "Y" : "N";
}

function renderSpecList(target, rows) {
  if (!Array.isArray(rows) || rows.length === 0) {
    target.innerHTML = '<div class="spec-empty">해당 스펙이 없습니다.</div>';
    return;
  }

  target.innerHTML = rows.map((row) => `
    <div class="spec-row">
      <dt>${row.label}</dt>
      <dd>${row.value ?? "-"}</dd>
    </div>
  `).join("");
}

function buildItemSpecRows(spec) {
  if (!spec || Object.keys(spec).length === 0) {
    return [];
  }

  return [
    { label: "상품코드", value: spec.product_code || "-" },
    { label: "상품명", value: spec.product_name || "-" },
    { label: "규격(cm)", value: spec.dimensions_cm_text || "-" },
    { label: "개당중량(kg)", value: formatMaybeNumber(spec.unit_weight_kg) },
  ];
}

function buildFullboxSpecRows(spec) {
  if (!spec || Object.keys(spec).length === 0) {
    return [];
  }

  return [
    { label: "상품코드", value: spec.product_code || "-" },
    { label: "상품명", value: spec.product_name || "-" },
    { label: "완박스입수량", value: formatMaybeNumber(spec.units_per_box) },
    { label: "완박스 규격(cm)", value: spec.dimensions_cm_text || "-" },
    { label: "완박스중량(kg)", value: formatMaybeNumber(spec.box_weight_kg) },
  ];
}

function renderSpecResult(data) {
  const matchedLabel = data.matched_name
    ? `${data.matched_name}${data.matched_code ? ` (${data.matched_code})` : ""}`
    : data.query || "-";
  const reasonLabel = data.match_reason || data.message || "-";

  specSummary.textContent = `조회: ${matchedLabel} / 상태: ${data.match_status || "-"} / 기준: ${reasonLabel}`;
  renderSpecList(itemSpecList, buildItemSpecRows(data.item_spec));
  renderSpecList(fullboxSpecList, buildFullboxSpecRows(data.fullbox_spec));

  if (data.success) {
    setSpecStatus("success", "조회 완료");
    return;
  }

  if (Array.isArray(data.candidates) && data.candidates.length > 0) {
    const candidateText = data.candidates
      .map((candidate) => `${candidate.product_name} (${candidate.product_code || "-"})`)
      .join(", ");
    specSummary.textContent += ` / 후보: ${candidateText}`;
  }
  setSpecStatus("error", "확인 필요");
}

function formatBoxNo(group) {
  const start = Number(group.box_no_start ?? 0);
  const end = Number(group.box_no_end ?? 0);

  if (!start) {
    return "";
  }
  if (!end || start === end) {
    return String(start);
  }
  return `${start}~${end}`;
}

function buildGroupRows(group) {
  const itemRows = Array.isArray(group.item_rows) && group.item_rows.length > 0
    ? group.item_rows
    : [{ product_name: "-", packing_unit: "-" }];

  return itemRows.map((item, index) => {
    const sharedCells = index === 0
      ? `
        <td rowspan="${itemRows.length}">${formatBoxNo(group)}</td>
      `
      : "";

    const trailingSharedCells = index === 0
      ? `
        <td rowspan="${itemRows.length}">${group.box_size_cm || ""}</td>
        <td rowspan="${itemRows.length}">${group.box_count ?? ""}</td>
        <td rowspan="${itemRows.length}">${group.each_qty ?? ""}</td>
        <td rowspan="${itemRows.length}">${formatWeight(group.weight_kg)}</td>
        <td rowspan="${itemRows.length}">${formatWeight(group.total_weight_kg)}</td>
        <td rowspan="${itemRows.length}" class="note-cell">${group.note || ""}</td>
      `
      : "";

    return `
      <tr>
        ${sharedCells}
        <td class="name-cell">${item.product_name || ""}</td>
        <td>${item.packing_unit ?? ""}</td>
        ${trailingSharedCells}
      </tr>
    `;
  }).join("");
}

function renderResultTable(groups, totals) {
  if (!Array.isArray(groups) || groups.length === 0) {
    tableSummary.textContent = "표로 표시할 결과가 없습니다.";
    resultTableBody.innerHTML = '<tr><td colspan="9" class="empty-cell">표로 표시할 결과가 없습니다.</td></tr>';
    return;
  }

  tableSummary.textContent =
    `총 ${totals.box_count ?? 0}박스 / 낱개수량 ${totals.each_qty ?? 0} / 합계 ${formatWeight(totals.total_weight_kg)}kg`;

  const bodyHtml = groups.map(buildGroupRows).join("")
    + `
      <tr class="total-row">
        <td colspan="3">총합계</td>
        <td></td>
        <td>${totals.box_count ?? 0}</td>
        <td>${totals.each_qty ?? 0}</td>
        <td></td>
        <td>${formatWeight(totals.total_weight_kg)}</td>
        <td></td>
      </tr>
    `;

  resultTableBody.innerHTML = bodyHtml;
}

async function submitPackingRequest(event) {
  event.preventDefault();

  const payload = {
    shipping_method: shippingMethod.value || null,
    packing_list_needed: packingListNeeded.value || "no",
    order_text: orderText.value.trim(),
  };

  if (!payload.order_text) {
    setStatus("error", "입력 필요");
    resultOutput.textContent = "[ERROR]\n주문 텍스트를 입력해주세요.";
    return;
  }

  setLoadingState();

  try {
    const response = await fetch("/pack", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const data = await response.json();

    successValue.textContent = data.success ? "성공" : "실패";
    shippingValue.textContent = data.shipping_method || "재포장";
    packingListValue.textContent = data.packing_list_needed || "-";
    engineValue.textContent = data.selected_engine || "-";
    normalizedOutput.textContent = data.normalized_order_text || "";
    resultOutput.textContent = data.result || "";
    renderResultTable(data.table_groups || [], data.table_totals || {});

    if (response.ok && data.success) {
      setStatus("success", "완료");
    } else {
      setStatus("error", "에러");
    }
  } catch (error) {
    console.error(error);
    successValue.textContent = "실패";
    engineValue.textContent = "-";
    resultOutput.textContent = `[ERROR]\n요청 중 오류가 발생했습니다.\n${error}`;
    tableSummary.textContent = "표로 표시할 결과를 불러오지 못했습니다.";
    resultTableBody.innerHTML = '<tr><td colspan="9" class="empty-cell">표로 표시할 결과를 불러오지 못했습니다.</td></tr>';
    setStatus("error", "에러");
  } finally {
    clearLoadingState();
  }
}

async function downloadExcelFile() {
  const payload = {
    shipping_method: shippingMethod.value || null,
    packing_list_needed: packingListNeeded.value || "no",
    order_text: orderText.value.trim(),
  };

  if (!payload.order_text) {
    setStatus("error", "입력 필요");
    resultOutput.textContent = "[ERROR]\n주문 텍스트를 입력해주세요.";
    return;
  }

  downloadBtn.disabled = true;
  downloadBtn.textContent = "다운로드 중...";
  setStatus("loading", "생성 중");

  try {
    const response = await fetch("/pack/export-xlsx", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      let message = "[ERROR]\n엑셀 생성에 실패했습니다.";
      try {
        const errorBody = await response.json();
        if (errorBody?.message) {
          message = `[ERROR]\n${errorBody.message}`;
        }
      } catch (error) {
        console.error(error);
      }
      resultOutput.textContent = message;
      setStatus("error", "에러");
      return;
    }

    const blob = await response.blob();
    const downloadUrl = window.URL.createObjectURL(blob);
    const contentDisposition = response.headers.get("Content-Disposition") || "";
    const matched = contentDisposition.match(/filename="?([^"]+)"?/i);
    const filename = matched?.[1] || "packing_result.xlsx";

    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(downloadUrl);

    setStatus("success", "다운로드");
  } catch (error) {
    console.error(error);
    resultOutput.textContent = `[ERROR]\n엑셀 다운로드 중 오류가 발생했습니다.\n${error}`;
    setStatus("error", "에러");
  } finally {
    clearLoadingState();
  }
}

async function submitSpecLookup(event) {
  event.preventDefault();

  const query = (specQuery.value || "").trim();
  if (!query) {
    setSpecStatus("error", "입력 필요");
    specSummary.textContent = "상품코드 또는 상품명을 입력해주세요.";
    return;
  }

  setSpecLoadingState();

  try {
    const response = await fetch("/product-spec", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ query }),
    });

    const data = await response.json();
    renderSpecResult(data);

    if (!response.ok && !data.success) {
      setSpecStatus("error", "에러");
    }
  } catch (error) {
    console.error(error);
    setSpecStatus("error", "에러");
    specSummary.textContent = `조회 중 오류가 발생했습니다. ${error}`;
    itemSpecList.innerHTML = '<div class="spec-empty">조회 중 오류가 발생했습니다.</div>';
    fullboxSpecList.innerHTML = '<div class="spec-empty">조회 중 오류가 발생했습니다.</div>';
  } finally {
    clearSpecLoadingState();
  }
}

sampleBtn.addEventListener("click", () => {
  shippingMethod.value = "재포장";
  packingListNeeded.value = "no";
  orderText.value = sampleOrder;
});

resetBtn.addEventListener("click", () => {
  form.reset();
  specForm.reset();
  shippingMethod.value = "재포장";
  setIdleState();
  setSpecIdleState();
});

downloadBtn.addEventListener("click", () => {
  downloadExcelFile();
});

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", () => {
    copyText(button.dataset.copyTarget);
  });
});

form.addEventListener("submit", submitPackingRequest);
specForm.addEventListener("submit", submitSpecLookup);

setIdleState();
setSpecIdleState();
shippingMethod.value = "재포장";
