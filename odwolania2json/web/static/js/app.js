/* Odwołania2JSON — frontend (bez frameworka, bez zależności).
   Routing oparty na hashu (#/pulpit, #/dokumenty, #/dokumenty/<id>, #/przetwarzanie, #/wyniki, #/informacje).
   Dane systemowe z GET /api/info, dokumenty z /api/documents.
   - Dokumenty: dodawanie PDF (drag & drop / wybór plików), lista, przetwarzanie (kolejka na serwerze), usuwanie.
     Statusy są rzeczywiste — odpytywanie co 1,5 s tylko wtedy, gdy coś jest w kolejce lub w trakcie.
   - Szczegóły dokumentu: dane w czytelnej tabeli, podgląd zapisanego output.json, pobieranie
     (GET /api/documents/<id>/output.json), kopiowanie i diagnostyka z diagnostics.json. JSON nie jest
     generowany ani przeformatowywany w przeglądarce — wyświetlana i kopiowana jest dokładna treść pliku.
   - Wyniki i eksport: lista zapisanych plików JSON do pobrania. */
(function () {
  "use strict";

  const ROUTES = ["pulpit", "dokumenty", "przetwarzanie", "wyniki", "informacje"];
  const LABELS = { odwolanie: "Odwołanie", pelnomocnictwo: "Pełnomocnictwo", przelew: "Potwierdzenie przelewu" };
  /* Czytelne nazwy sekcji i pól wyniku (wyłącznie prezentacja — klucze JSON pozostają bez zmian). */
  const SECTION_LABELS = {
    OdwolanieKIO: "Odwołanie", Odwolujacy: "Odwołujący", Zamawiajacy: "Zamawiający",
    ZamowieniePubliczne: "Zamówienie publiczne", mocodawca: "Mocodawca", pelnomocnicy: "Pełnomocnik",
    przelew_bankowy: "Przelew", podpisujacy: "Podpisujący", pelnomocnik: "Pełnomocnik", data: "Dane podmiotu",
  };
  const FIELD_LABELS = {
    data: "Data", czynnosc_dzien: "Data zaskarżonej czynności", czynnosc_powod_id: "Kategoria czynności (nr)",
    czynnosc_opis: "Zaskarżona czynność", miejscowosc: "Miejscowość", krs: "KRS", nazwa: "Nazwa", nip: "NIP",
    regon: "REGON", "przez-pelnomocnika": "Działa przez pełnomocnika", imie_nazwisko: "Imię i nazwisko", mail: "E-mail",
    prowadzacy_postepowanie: "Prowadzi postępowanie", numer_bzp: "Numer ogłoszenia BZP",
    numer_postepowania: "Numer postępowania", numer_ted: "Numer ogłoszenia TED", tytul: "Tytuł", typ: "Typ podmiotu",
    pesel: "PESEL", data_operacji: "Data operacji", kwota: "Kwota", z_rachunku: "Z rachunku", na_rachunek: "Na rachunek",
    numer_transakcji: "Numer transakcji",
  };
  const YES_NO_FIELDS = new Set(["przez-pelnomocnika", "prowadzacy_postepowanie"]);
  const view = document.getElementById("view");
  let info = null;
  let infoError = null;
  let docs = null;            // ostatnio pobrana lista dokumentów (null = jeszcze nie pobrano)
  let docsError = null;
  let maxBytes = 50 * 1024 * 1024;
  let uploadReport = null;    // podsumowanie ostatniego dodawania plików (pokazywane do następnej operacji)
  let docsSignature = "";     // do wykrywania zmian przy odpytywaniu (bez zbędnego przerysowania)
  let pollTimer = null;
  let processNotice = null;   // komunikat o kolejkowaniu / zakończeniu przetwarzania (widok listy)
  let watched = new Set();    // dokumenty bieżącej partii przetwarzania zleconej w tej karcie
  const selected = new Set(); // zaznaczone dokumenty (przetrwa odświeżenie listy)
  let detail = null;          // szczegóły: { id, resultFor, loading, result, missing, error, copyMsg, diagOpen, tab }
  const ACTIVE = ["queued", "processing"];
  const isActive = (d) => ACTIVE.includes(d.status);
  const isUnknown = (d) => d.status === "processed" && d.summary && d.summary.doc_class === "unknown";
  const warningsOf = (d) => (d.summary && d.summary.warnings) || 0;
  const needsReview = (d) => d.status === "processed" && !isUnknown(d) && warningsOf(d) > 0;
  const REASONS = {
    no_text: "nie odczytano tekstu",
    extraction_error: "nie udało się odczytać pliku",
    insufficient_signals: "brak cech obsługiwanych typów",
  };
  const API_HEADERS = { "X-Requested-With": "odwolania2json" };

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ------------------------------------------------------------ komponenty */
  const pageHeader = (eyebrow, title, lead, actions = "") => `
    <header class="page-header${actions ? " page-header--actions" : ""}">
      <div>
        <div class="page-header__eyebrow">${esc(eyebrow)}</div>
        <h1 tabindex="-1">${esc(title)}</h1>
        ${lead ? `<p class="page-header__lead">${esc(lead)}</p>` : ""}
      </div>
      ${actions ? `<div class="page-header__actions">${actions}</div>` : ""}
    </header>`;

  const panel = (title, body, aside = "") => `
    <section class="panel">
      <div class="panel__header"><h2 class="panel__title">${esc(title)}</h2>${aside}</div>
      <div class="panel__body">${body}</div>
    </section>`;

  const badge = (kind, text) => `<span class="badge badge--${kind}">${esc(text)}</span>`;

  const kv = (rows) => `<table class="kv"><tbody>${rows
    .map(([k, v]) => `<tr><th scope="row">${esc(k)}</th><td>${v}</td></tr>`).join("")}</tbody></table>`;

  const empty = (title, text, action = "") => `<div class="empty"><p class="empty__title">${esc(title)}</p><p>${esc(text)}</p>${action}</div>`;

  const ocrBadge = () => {
    if (infoError) return badge("error", "Brak danych");
    if (!info) return badge("neutral", "Sprawdzanie…");
    if (!info.ocr.enabled) return badge("warning", "Wyłączony");
    return info.ocr.available ? badge("success", "Dostępny") : badge("warning", "Niedostępny");
  };

  /* Stan systemu: wersja skrócona (pulpit) i techniczna (informacje o systemie). */
  const systemStatus = (technical) => {
    if (infoError) return `<div class="notice notice--warning"><p>Nie udało się pobrać informacji o systemie: ${esc(infoError)}</p></div>`;
    if (!info) return `<p class="muted">Wczytywanie informacji o systemie…</p>`;
    const rows = [
      ["Przetwarzanie", technical ? esc(info.processing) : "lokalnie, na tym komputerze — dokumenty nie są nigdzie wysyłane"],
      ["Rozpoznawanie tekstu ze skanów (OCR)", technical
        ? `${ocrBadge()}<br><span class="muted small">${esc(info.ocr.engine || "")}, język: ${esc(info.ocr.language)}, ${esc(info.ocr.dpi)} dpi</span>`
        : ocrBadge()],
      ["Rozpoznawane typy dokumentów", esc(info.classes.map((c) => LABELS[c.label] || c.label).join(", "))],
      ["Dokument spoza obsługiwanych typów", technical
        ? `typ <code>${esc(info.unknown_label)}</code> — wynik <code>${esc(info.unknown_output)}</code>`
        : `oznaczany jako „Nierozpoznany”; zapisywany jest pusty wynik <code>${esc(info.unknown_output)}</code>`],
    ];
    if (technical) rows.unshift(["Wersja", esc(info.version)]);
    return kv(rows);
  };

  /* ------------------------------------------------------------ formatowanie */
  /* Polska odmiana: 1 dokument, 2–4 dokumenty, 5+ dokumentów (z wyjątkiem 12–14). */
  function plural(n, one, few, many) {
    if (n === 1) return one;
    const d = n % 10, h = n % 100;
    return d >= 2 && d <= 4 && (h < 12 || h > 14) ? few : many;
  }
  const docWord = (n) => plural(n, "dokument", "dokumenty", "dokumentów");
  const pageWord = (n) => plural(n, "strona", "strony", "stron");

  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    const mb = bytes / (1024 * 1024);
    return `${Number.isInteger(mb) ? mb : mb.toFixed(1).replace(".", ",")} MB`;
  }

  function formatDate(iso) {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("pl-PL", { dateStyle: "short", timeStyle: "short" });
  }

  function formatElapsed(iso) {
    const ms = Date.now() - Date.parse(iso);
    if (!Number.isFinite(ms) || ms < 0) return "0:00";
    const sec = Math.floor(ms / 1000);
    return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  }

  const formatSeconds = (s) => (s < 1 ? "poniżej 1 s" : `${s < 10 ? s.toFixed(1).replace(".", ",") : Math.round(s)} s`);

  /* Zakresy stron: [1,2,3,5] -> "1–3, 5". */
  function pageRanges(pages) {
    const out = [];
    for (let i = 0; i < pages.length; i += 1) {
      let j = i;
      while (j + 1 < pages.length && pages[j + 1] === pages[j] + 1) j += 1;
      out.push(i === j ? `${pages[i]}` : `${pages[i]}–${pages[j]}`);
      i = j;
    }
    return out.join(", ");
  }

  /* ------------------------------------------------------------ stan dokumentu */
  function classReason(reason) {
    if (!reason) return "";
    if (reason === "match") return "dopasowanie do jednego typu dokumentu";
    if (REASONS[reason]) return REASONS[reason];
    if (reason.startsWith("veto:")) return "wykluczony regułą — dokument innego rodzaju (np. orzeczenie lub ogłoszenie)";
    if (reason.startsWith("conflict:")) return "cechy kilku typów jednocześnie";
    return reason;
  }

  /* Krótka informacja o OCR (pusta, gdy dokument miał warstwę tekstową). */
  function ocrShort(s) {
    if (!s) return "";
    const ocrPages = (s.page_sources || []).map((src, i) => (src === "ocr" ? i + 1 : 0)).filter(Boolean);
    if (ocrPages.length) {
      return ocrPages.length === (s.page_sources || []).length ? "OCR: wszystkie strony"
        : `OCR: ${ocrPages.length > 1 ? "strony" : "strona"} ${pageRanges(ocrPages)}`;
    }
    return { disabled: "OCR wyłączony", unavailable: "OCR niedostępny", failed: "OCR nieudany" }[s.ocr_status] || "";
  }

  function ocrFull(d) {
    const s = d.summary;
    if (!s || isActive(d) || s.processing_status === "error") return `<span class="muted">—</span>`;
    const short = ocrShort(s);
    if (short.startsWith("OCR: ")) return `Tak — ${esc(short.slice(5))}${s.ocr_seconds ? `, ${esc(formatSeconds(s.ocr_seconds))}` : ""}`;
    if (short) return esc(short);
    return s.ocr_status === "not_needed" ? "Nie był potrzebny (tekst z pliku PDF)" : "Uruchomiony, bez odczytanego tekstu";
  }

  /* Status z rozróżnieniem: gotowe / do weryfikacji / nierozpoznany / błąd. */
  function statusInfo(d) {
    if (d.status === "queued") return { kind: "info", text: d.queue_position ? `W kolejce (${d.queue_position})` : "W kolejce" };
    if (d.status === "processing") return { kind: "info", text: "Przetwarzanie…", elapsed: true };
    if (d.status === "failed") return { kind: "error", text: "Błąd przetwarzania", detail: d.error || "" };
    if (d.status === "added") return { kind: "neutral", text: "Oczekuje", detail: d.note || "" };
    const meta = [d.seconds != null ? formatSeconds(d.seconds) : "", ocrShort(d.summary)].filter(Boolean).join(" · ");
    if (isUnknown(d)) return { kind: "neutral", text: "Nierozpoznany", detail: meta };
    if (needsReview(d)) {
      const w = warningsOf(d);
      return { kind: "warning", text: "Do weryfikacji", detail: [`${w} ${plural(w, "brakujące pole", "brakujące pola", "brakujących pól")}`, meta].filter(Boolean).join(" · ") };
    }
    return { kind: "success", text: "Gotowe", detail: meta };
  }

  function statusCell(d) {
    const st = statusInfo(d);
    const main = st.elapsed
      ? `<span class="badge badge--info">Przetwarzanie… <span data-elapsed="${esc(d.started_at)}">${esc(formatElapsed(d.started_at))}</span></span>`
      : badge(st.kind, st.text);
    return `${main}${st.detail ? `<div class="cell-detail">${esc(st.detail)}</div>` : ""}`;
  }

  function typeCell(d) {
    const s = d.summary;
    if (!s || isActive(d) || s.processing_status === "error") return `<span class="muted">—</span>`;
    if (s.doc_class !== "unknown") return `<strong>${esc(LABELS[s.doc_class] || s.doc_class)}</strong>`;
    return `<span class="muted">${esc(classReason(s.classification_reason))}</span>`;
  }

  const findDoc = (id) => (docs || []).find((d) => d.id === id) || null;
  const jsonFileName = (name) => `${String(name).replace(/\.pdf$/i, "") || "wynik"}.json`;
  const hasResult = (d) => d && ["processed", "failed"].includes(d.status);
  const hasOutput = (d) => hasResult(d) && d.summary && d.summary.output_written;
  const downloadLink = (d, cls, label) => `<a class="btn ${cls}" href="/api/documents/${esc(d.id)}/output.json" download="${esc(jsonFileName(d.original_name))}">${label}</a>`;

  /* ------------------------------------------------------------ pulpit */
  function docsSummary() {
    if (docsError) return `<p class="muted">Nie udało się pobrać listy dokumentów.</p>`;
    if (docs === null) return `<p class="muted">Wczytywanie…</p>`;
    if (!docs.length) {
      return empty("Brak dokumentów", "Dodaj pliki PDF, aby rozpoznać ich typ i wyodrębnić dane.",
        `<p><a class="btn btn--primary" href="#/dokumenty">Dodaj dokumenty</a></p>`);
    }
    const count = (fn) => docs.filter(fn).length;
    const tiles = [
      ["Wszystkie", docs.length, ""],
      ["Oczekujące", count((d) => d.status === "added"), ""],
      ["W trakcie", count(isActive), "info"],
      ["Gotowe", count((d) => d.status === "processed" && !isUnknown(d) && !needsReview(d)), "success"],
      ["Do weryfikacji", count(needsReview), "warning"],
      ["Nierozpoznane", count(isUnknown), ""],
      ["Błędy", count((d) => d.status === "failed"), "error"],
    ];
    return `<div class="stats">${tiles.map(([label, n, kind]) => `<a class="stat${kind && n ? ` stat--${kind}` : ""}" href="#/dokumenty">
      <span class="stat__value">${n}</span><span class="stat__label">${esc(label)}</span></a>`).join("")}</div>`;
  }

  /* ------------------------------------------------------------ lista dokumentów */
  function progressBlock() {
    if (!docs || !docs.some(isActive)) return "";
    const batch = [...watched].map(findDoc).filter(Boolean);
    const total = batch.length;
    const done = batch.filter((d) => !isActive(d)).length;
    const head = total
      ? `Przetwarzanie: ukończono ${done} z ${total} ${plural(total, "dokumentu", "dokumentów", "dokumentów")}`
      : `Przetwarzanie: ${docs.filter(isActive).length} ${docWord(docs.filter(isActive).length)} w kolejce lub w trakcie`;
    return `<div class="progress" role="status">
      <p class="progress__text"><strong>${esc(head)}</strong>
        <span class="muted small">Dokumenty tekstowe trwają poniżej sekundy; skany wymagają rozpoznawania tekstu (OCR) — kilka–kilkanaście sekund na stronę.</span></p>
      ${total ? `<div class="progress__bar" aria-hidden="true"><span style="width:${Math.round((done / total) * 100)}%"></span></div>` : ""}
    </div>`;
  }

  function rowActions(d) {
    const name = esc(d.original_name);
    const del = `<button type="button" class="btn btn--ghost btn--small btn--danger" data-delete="${esc(d.id)}" data-key="del-${esc(d.id)}"
      ${d.status === "processing" ? "disabled title=\"Nie można usunąć dokumentu w trakcie przetwarzania\"" : ""} aria-label="Usuń dokument ${name}">Usuń</button>`;
    if (isActive(d)) return `<span class="muted small">w toku…</span>${d.status === "queued" ? del : ""}`;
    if (hasResult(d)) {
      return `<a class="btn btn--primary btn--small" href="#/dokumenty/${esc(d.id)}" data-key="open-${esc(d.id)}" aria-label="Wynik dokumentu ${name}">Wynik</a>
        <button type="button" class="btn btn--ghost btn--small" data-process="${esc(d.id)}" data-key="proc-${esc(d.id)}"
          title="Przetwórz ponownie" aria-label="Przetwórz ponownie dokument ${name}">Ponów</button>${del}`;
    }
    return `<button type="button" class="btn btn--primary btn--small" data-process="${esc(d.id)}" data-key="proc-${esc(d.id)}"
      aria-label="Przetwórz dokument ${name}">Przetwórz</button>${del}`;
  }

  function docsTable() {
    if (docsError) return `<div class="notice notice--warning"><p>Nie udało się pobrać listy dokumentów: ${esc(docsError)}</p></div>`;
    if (docs === null) return `<p class="muted">Wczytywanie listy dokumentów…</p>`;
    if (!docs.length) return empty("Brak dokumentów", "Dodane pliki PDF pojawią się w tym miejscu.");
    const selectable = docs.filter((d) => !isActive(d));
    const allChecked = selectable.length > 0 && selectable.every((d) => selected.has(d.id));
    const n = selected.size;
    const pending = docs.filter((d) => d.status === "added").length;
    const label = n ? `Przetwórz zaznaczone (${n})` : `Przetwórz oczekujące${pending ? ` (${pending})` : ""}`;
    const hint = n ? `Zaznaczono ${n} ${docWord(n)}.`
      : pending ? "Przetworzone zostaną wszystkie dokumenty ze statusem „Oczekuje”. Możesz też zaznaczyć wybrane."
        : docs.some(isActive) ? "Dokumenty są przetwarzane — status odświeża się automatycznie."
          : "Wszystkie dokumenty zostały przetworzone. Zaznacz dokumenty, aby przetworzyć je ponownie.";
    return `<div class="docs-toolbar">
        <button type="button" class="btn btn--primary" id="process-selected" data-key="process-selected" ${n || pending ? "" : "disabled"}>${esc(label)}</button>
        <span class="muted small">${esc(hint)}</span>
      </div>
      ${progressBlock()}
      <div class="table-wrap"><table class="table table--stack table--docs">
      <thead><tr><th scope="col" class="cell-check"><input type="checkbox" id="select-all" data-key="select-all" ${allChecked ? "checked" : ""}
          ${selectable.length ? "" : "disabled"} aria-label="Zaznacz wszystkie dokumenty"></th>
        <th scope="col">Dokument</th><th scope="col">Status</th><th scope="col">Typ dokumentu</th>
        <th scope="col"><span class="visually-hidden">Akcje</span></th></tr></thead>
      <tbody>${docs.map((d) => `<tr>
        <td class="cell-check" data-label="Zaznacz"><input type="checkbox" data-select="${esc(d.id)}" data-key="sel-${esc(d.id)}"
          ${selected.has(d.id) ? "checked" : ""} ${isActive(d) ? "disabled" : ""} aria-label="Zaznacz dokument ${esc(d.original_name)}"></td>
        <td class="cell-name" data-label="Dokument"><div><a href="#/dokumenty/${esc(d.id)}" class="doc-link">${esc(d.original_name)}</a>
          <div class="cell-detail">${esc(formatSize(d.size))} · ${esc(d.pages ?? "—")} ${esc(pageWord(d.pages || 0))} · dodano ${esc(formatDate(d.added_at))}</div></div></td>
        <td data-label="Status"><div>${statusCell(d)}</div></td>
        <td data-label="Typ dokumentu"><div>${typeCell(d)}</div></td>
        <td class="cell-actions"><div class="row-actions">${rowActions(d)}</div></td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function renderProcessNotice() {
    const box = document.getElementById("process-status");
    if (!box) return;
    box.innerHTML = processNotice
      ? `<div class="notice notice--${processNotice.kind}"><p>${processNotice.html}</p></div>` : "";
  }

  function refreshDocsList() {
    const list = document.getElementById("docs-list");
    const focusKey = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.key : null;
    if (list) list.innerHTML = docsTable();
    if (focusKey) {
      const el = document.querySelector(`[data-key="${CSS.escape(focusKey)}"]`);
      if (el && !el.disabled) el.focus();
    }
    renderProcessNotice();
    const count = document.getElementById("docs-count");
    if (count) count.textContent = docs && docs.length ? `${docs.length} ${docWord(docs.length)}` : "";
    const zone = document.getElementById("dropzone");
    if (zone) zone.classList.toggle("dropzone--compact", Boolean(docs && docs.length));
  }

  async function loadDocs() {
    try {
      const r = await fetch("/api/documents", { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      docs = data.documents;
      maxBytes = data.max_bytes || maxBytes;
      docsError = null;
      const ids = new Set(docs.map((d) => d.id));
      [...selected].forEach((id) => {
        const d = docs.find((x) => x.id === id);
        if (!ids.has(id) || (d && isActive(d))) selected.delete(id);
      });
      trackCompletion();
    } catch (err) {
      docsError = err.message;
    }
  }

  /* Komunikat po zakończeniu przetwarzania partii dokumentów zleconej w tej karcie. */
  function trackCompletion() {
    if (!watched.size) return;
    const tracked = docs.filter((d) => watched.has(d.id));
    if (tracked.some(isActive)) return;
    const ok = tracked.filter((d) => d.status === "processed" && !isUnknown(d) && !needsReview(d)).length;
    const review = tracked.filter(needsReview).length;
    const unknown = tracked.filter(isUnknown).length;
    const bad = tracked.filter((d) => d.status === "failed").length;
    watched = new Set();
    if (!tracked.length) { processNotice = null; return; }
    const parts = [`gotowe: ${ok}`];
    if (review) parts.push(`do weryfikacji: ${review}`);
    if (unknown) parts.push(`nierozpoznane: ${unknown}`);
    if (bad) parts.push(`błędy: ${bad}`);
    processNotice = {
      kind: bad ? (ok ? "warning" : "error") : review ? "warning" : "success",
      html: `<strong>Przetwarzanie zakończone</strong> — ${parts.join(", ")}. Kliknij „Wynik”, aby zobaczyć dane i pobrać JSON.`,
    };
  }

  /* Odpytywanie tylko wtedy, gdy na serwerze coś jest w kolejce lub w trakcie przetwarzania. */
  function schedulePoll() {
    clearTimeout(pollTimer);
    pollTimer = null;
    if (docs && docs.some(isActive)) pollTimer = setTimeout(poll, 1500);
  }

  async function poll() {
    await loadDocs();
    const sig = JSON.stringify(docs) + JSON.stringify(processNotice);
    if (sig !== docsSignature) {
      docsSignature = sig;
      const route = currentRoute();
      if (route === "dokumenty" && routeParam()) { refreshDetail(); ensureResult(); }
      else if (route === "dokumenty") refreshDocsList();
      else if (["pulpit", "przetwarzanie", "wyniki"].includes(route)) render(false);
    }
    schedulePoll();
  }

  async function handleProcess(ids) {
    if (!ids.length) return;
    let notice = null;
    uploadReport = null;
    renderUploadReport();
    try {
      const r = await fetch("/api/documents/process", {
        method: "POST", body: JSON.stringify({ ids }),
        headers: { ...API_HEADERS, "Content-Type": "application/json" },
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) notice = { kind: "error", html: esc(data.message || `Nie udało się rozpocząć przetwarzania (HTTP ${r.status}).`) };
      else {
        data.queued.forEach((id) => { watched.add(id); selected.delete(id); });
        if (data.skipped.length) notice = { kind: "warning", html: `Pominięto ${data.skipped.length}: ${esc(data.skipped[0].message)}` };
      }
    } catch {
      notice = { kind: "error", html: "Brak połączenia z aplikacją. Sprawdź, czy serwer jest uruchomiony." };
    }
    processNotice = notice;
    await loadDocs();
    if (routeParam()) refreshDetail(); else refreshDocsList();
    schedulePoll();
  }

  /* Licznik czasu przetwarzania (co 1 s, bez zapytań do serwera). */
  setInterval(() => {
    document.querySelectorAll("[data-elapsed]").forEach((el) => { el.textContent = formatElapsed(el.dataset.elapsed); });
  }, 1000);

  /* ------------------------------------------------------------ dodawanie plików */
  /* Walidacja po stronie przeglądarki (serwer i tak sprawdza wszystko ponownie). */
  async function precheck(file) {
    if (!/\.pdf$/i.test(file.name)) return "Obsługiwane są wyłącznie pliki PDF (rozszerzenie .pdf).";
    if (file.size === 0) return "Plik jest pusty.";
    if (file.size > maxBytes) return `Plik przekracza dopuszczalny rozmiar ${formatSize(maxBytes)}.`;
    const head = new TextDecoder("latin1").decode(await file.slice(0, 1024).arrayBuffer());
    if (!head.includes("%PDF-")) return "Plik nie jest dokumentem PDF (nieprawidłowa zawartość).";
    return null;
  }

  async function uploadOne(file) {
    const problem = await precheck(file);
    if (problem) return { name: file.name, ok: false, message: problem };
    try {
      const r = await fetch("/api/documents", {
        method: "POST", body: file,
        headers: { ...API_HEADERS, "X-Filename": encodeURIComponent(file.name), "Content-Type": "application/pdf" },
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) return { name: file.name, ok: false, message: data.message || `Błąd serwera (HTTP ${r.status}).` };
      const note = data.duplicate_of && data.duplicate_of.length ? "Uwaga: identyczny plik był już dodany wcześniej." : "";
      return { name: file.name, ok: true, message: note };
    } catch {
      return { name: file.name, ok: false, message: "Brak połączenia z aplikacją. Sprawdź, czy serwer jest uruchomiony." };
    }
  }

  function renderUploadReport() {
    const box = document.getElementById("upload-status");
    if (!box) return;
    if (!uploadReport) { box.innerHTML = ""; return; }
    if (uploadReport.progress) { box.innerHTML = `<div class="notice"><p>${esc(uploadReport.progress)}</p></div>`; return; }
    const ok = uploadReport.results.filter((r) => r.ok);
    const failed = uploadReport.results.filter((r) => !r.ok);
    const kind = failed.length === 0 ? "success" : ok.length === 0 ? "error" : "warning";
    const total = uploadReport.results.length;
    const next = ok.length ? " Kliknij „Przetwórz oczekujące”, aby rozpocząć przetwarzanie." : "";
    const title = failed.length === 0
      ? `Dodano ${ok.length} ${docWord(ok.length)}.${next}`
      : ok.length === 0
        ? (failed.length === 1 ? "Nie udało się dodać dokumentu." : "Nie udało się dodać żadnego z dokumentów.")
        : `Dodano ${ok.length} z ${total} ${plural(total, "dokumentu", "dokumentów", "dokumentów")}. Pozostałe nie zostały dodane — szczegóły poniżej.`;
    const details = uploadReport.results.filter((r) => !r.ok || r.message)
      .map((r) => `<li><strong>${esc(r.name)}</strong> — ${esc(r.message)}</li>`).join("");
    box.innerHTML = `<div class="notice notice--${kind}"><p><strong>${esc(title)}</strong></p>${details ? `<ul class="list">${details}</ul>` : ""}</div>`;
  }

  async function handleFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    const results = [];
    processNotice = null;
    for (let i = 0; i < files.length; i += 1) {
      uploadReport = { progress: `Przesyłanie ${i + 1} z ${files.length}: ${files[i].name}…` };
      renderUploadReport();
      results.push(await uploadOne(files[i]));      // kolejno — błąd jednego pliku nie blokuje pozostałych
    }
    uploadReport = { results };
    await loadDocs();
    renderUploadReport();
    refreshDocsList();
  }

  async function handleDelete(id) {
    const doc = findDoc(id);
    if (!doc || !window.confirm(`Usunąć dokument „${doc.original_name}”? Plik i jego wynik zostaną usunięte z aplikacji.`)) return;
    let message;
    try {
      const r = await fetch(`/api/documents/${encodeURIComponent(id)}`, { method: "DELETE", headers: API_HEADERS });
      if (r.ok) message = { kind: "success", text: `Usunięto dokument „${doc.original_name}”.` };
      else {
        const data = await r.json().catch(() => ({}));
        message = { kind: "error", text: data.message || `Nie udało się usunąć dokumentu (HTTP ${r.status}).` };
      }
    } catch {
      message = { kind: "error", text: "Brak połączenia z aplikacją. Sprawdź, czy serwer jest uruchomiony." };
    }
    uploadReport = null;
    processNotice = null;
    await loadDocs();
    refreshDocsList();
    const box = document.getElementById("upload-status");
    if (box) box.innerHTML = `<div class="notice notice--${message.kind}"><p>${esc(message.text)}</p></div>`;
  }

  function mountDocuments() {
    const zone = document.getElementById("dropzone");
    const input = document.getElementById("file-input");
    document.getElementById("pick-files").addEventListener("click", () => input.click());
    input.addEventListener("change", () => { handleFiles(input.files); input.value = ""; });
    ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => {
      e.preventDefault(); zone.classList.add("is-dragover");
    }));
    ["dragleave", "dragend"].forEach((t) => zone.addEventListener(t, (e) => {
      if (!zone.contains(e.relatedTarget)) zone.classList.remove("is-dragover");
    }));
    zone.addEventListener("drop", (e) => {
      e.preventDefault(); zone.classList.remove("is-dragover"); handleFiles(e.dataTransfer.files);
    });
    const list = document.getElementById("docs-list");
    list.addEventListener("click", (e) => {
      const del = e.target.closest("[data-delete]");
      if (del) handleDelete(del.dataset.delete);
      const proc = e.target.closest("[data-process]");
      if (proc) handleProcess([proc.dataset.process]);
      if (e.target.closest("#process-selected")) {
        handleProcess(selected.size ? [...selected] : (docs || []).filter((d) => d.status === "added").map((d) => d.id));
      }
    });
    list.addEventListener("change", (e) => {
      if (e.target.dataset.select) {
        if (e.target.checked) selected.add(e.target.dataset.select); else selected.delete(e.target.dataset.select);
      } else if (e.target.id === "select-all") {
        (docs || []).filter((d) => !isActive(d)).forEach((d) => (e.target.checked ? selected.add(d.id) : selected.delete(d.id)));
      } else return;
      refreshDocsList();
    });
    renderUploadReport();
    refreshDocsList();
  }

  // Upuszczenie pliku poza obszarem dodawania nie może otworzyć PDF w karcie przeglądarki.
  ["dragover", "drop"].forEach((t) => window.addEventListener(t, (e) => {
    if (!e.target.closest || !e.target.closest("#dropzone")) e.preventDefault();
  }));

  function activeList() {
    if (docs === null) return `<p class="muted">Wczytywanie…</p>`;
    const active = docs.filter(isActive)
      .sort((a, b) => (a.status === "processing" ? -1 : b.status === "processing" ? 1 : (a.queue_position || 0) - (b.queue_position || 0)));
    if (!active.length) return empty("Brak aktywnych zadań", "Dodaj dokumenty w sekcji Dokumenty i wybierz „Przetwórz oczekujące”.");
    return `<ul class="list">${active.map((d) => `<li><a href="#/dokumenty/${esc(d.id)}"><strong>${esc(d.original_name)}</strong></a> — ${statusCell(d)}</li>`).join("")}</ul>`;
  }

  /* ------------------------------------------------------------ wyniki i eksport */
  function resultsTable() {
    if (docsError) return `<div class="notice notice--warning"><p>Nie udało się pobrać listy dokumentów: ${esc(docsError)}</p></div>`;
    if (docs === null) return `<p class="muted">Wczytywanie…</p>`;
    const ready = docs.filter(hasOutput);
    if (!ready.length) {
      return empty("Brak wyników", "Pliki JSON pojawią się tutaj po przetworzeniu dokumentów.",
        `<p><a class="btn btn--secondary" href="#/dokumenty">Przejdź do dokumentów</a></p>`);
    }
    return `<div class="table-wrap"><table class="table table--stack table--docs">
      <thead><tr><th scope="col">Plik JSON</th><th scope="col">Status</th><th scope="col">Typ dokumentu</th>
        <th scope="col"><span class="visually-hidden">Akcje</span></th></tr></thead>
      <tbody>${ready.map((d) => `<tr>
        <td class="cell-name" data-label="Plik JSON"><div><strong class="break">${esc(jsonFileName(d.original_name))}</strong>
          <div class="cell-detail">z pliku <a href="#/dokumenty/${esc(d.id)}">${esc(d.original_name)}</a></div></div></td>
        <td data-label="Status"><div>${badge(statusInfo(d).kind, statusInfo(d).text)}</div></td>
        <td data-label="Typ dokumentu"><div>${typeCell(d)}</div></td>
        <td class="cell-actions"><div class="row-actions">${downloadLink(d, "btn--secondary btn--small", "Pobierz")}
          <a class="btn btn--ghost btn--small" href="#/dokumenty/${esc(d.id)}">Podgląd</a></div></td></tr>`).join("")}
      </tbody></table></div>`;
  }

  /* ------------------------------------------------------------ szczegóły dokumentu */
  const PROCESSING_STATUS = {
    ok: "Tekst odczytany, dokument sklasyfikowany",
    no_text: "Brak wystarczającej ilości tekstu",
    error: "Nie udało się odczytać pliku PDF",
  };
  const TEXT_SOURCE = { native: "warstwa tekstowa PDF", ocr: "OCR", mixed: "warstwa tekstowa i OCR", none: "brak tekstu" };
  const OCR_STATUS = {
    not_needed: "niepotrzebny", disabled: "wyłączony", unavailable: "niedostępny",
    ok: "wykonany", partial: "wykonany częściowo (z błędami)", failed: "nieudany",
  };
  const OCR_REASON = { no_text: "brak warstwy tekstowej", low_text: "za mało tekstu", scanned_pages: "strony skanowane" };

  /* Kolorowanie składni bez zmiany treści: textContent podglądu = treść pliku. */
  function highlightJson(text) {
    const re = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
    let out = "";
    let last = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      out += esc(text.slice(last, m.index));
      if (m[1]) out += m[2] ? `<span class="j-key">${esc(m[1])}</span>${esc(m[2])}` : `<span class="j-str">${esc(m[1])}</span>`;
      else if (m[3]) out += `<span class="j-lit">${m[3]}</span>`;
      else out += `<span class="j-num">${esc(m[4])}</span>`;
      last = m.index + m[0].length;
    }
    out += esc(text.slice(last));
    const lines = out.split("\n");
    if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
    return lines.map((l) => `<span class="ln">${l}</span>`).join("\n");
  }

  const humanize = (key) => {
    const s = String(key).replace(/[_-]+/g, " ");
    return s.charAt(0).toUpperCase() + s.slice(1);
  };

  /* Liczba pól (liści) i pól z wartością w wyniku. */
  function completeness(node) {
    if (node === null || typeof node !== "object") return { total: 1, filled: node === null ? 0 : 1 };
    const parts = (Array.isArray(node) ? node : Object.values(node)).map(completeness);
    return parts.reduce((a, b) => ({ total: a.total + b.total, filled: a.filled + b.filled }), { total: 0, filled: 0 });
  }

  function displayValue(key, value) {
    if (value === null || value === undefined) return `<span class="muted value-empty">brak danych</span>`;
    if (YES_NO_FIELDS.has(key) && (value === 0 || value === 1)) return `${value === 1 ? "tak" : "nie"} <span class="muted small">(${value})</span>`;
    if (key === "kwota" && typeof value === "number") {
      return `${esc(value.toLocaleString("pl-PL", { minimumFractionDigits: 2, maximumFractionDigits: 2 }))} <span class="muted small">PLN</span>`;
    }
    return `<span class="break">${esc(value)}</span>`;
  }

  /* Wiersze tabeli „Dane” dla obiektu (rekurencyjnie); źródło wartości z diagnostyki (evidence). */
  function dataRows(obj, path, evidence, depth) {
    let html = "";
    Object.entries(obj).forEach(([key, value]) => {
      const p = path ? `${path}.${key}` : key;
      if (value !== null && typeof value === "object") {
        const items = Array.isArray(value) ? value : [value];
        items.forEach((item, i) => {
          const ip = Array.isArray(value) ? `${p}[${i}]` : p;
          const title = `${SECTION_LABELS[key] || humanize(key)}${Array.isArray(value) && items.length > 1 ? ` ${i + 1}` : ""}`;
          const ev = evidence[ip];
          html += `<tr class="data-group data-group--${Math.min(depth, 2)}"><th colspan="2" scope="rowgroup">${esc(title)}
            ${ev ? `<div class="data-source">Źródło: „${esc(ev)}”</div>` : ""}</th></tr>`;
          if (item !== null && typeof item === "object") html += dataRows(item, ip, evidence, depth + 1);
        });
      } else {
        const ev = evidence[p];
        html += `<tr><th scope="row">${esc(FIELD_LABELS[key] || humanize(key))}</th>
          <td>${displayValue(key, value)}${ev ? `<div class="data-source">Źródło: „${esc(ev)}”</div>` : ""}</td></tr>`;
      }
    });
    return html;
  }

  function dataTab(d, parsed, evidence) {
    if (!parsed || !Object.keys(parsed).length) {
      return empty("Brak danych do wyświetlenia", isUnknown(d)
        ? "Dokument nie należy do obsługiwanych typów, dlatego wynik JSON jest pustym obiektem {}."
        : "Wynik nie zawiera danych.");
    }
    const c = completeness(parsed);
    return `<p class="data-summary">Uzupełniono <strong>${c.filled} z ${c.total}</strong> ${plural(c.total, "pola", "pól", "pól")}.
        <span class="muted small">Puste pola oznaczają, że informacji nie znaleziono w dokumencie (w JSON: <code>null</code>).</span></p>
      <table class="kv kv--data"><tbody>${dataRows(parsed, "", evidence, 0)}</tbody></table>`;
  }

  function ensureResult() {
    const id = routeParam();
    if (!id || docs === null) return;
    const d = findDoc(id);
    if (!detail || detail.id !== id) detail = { id, diagOpen: false, tab: "dane" };
    if (!hasResult(d)) {                       // brak wyniku albo dokument w trakcie — nie pobieramy
      Object.assign(detail, { resultFor: null, loading: false, result: null, missing: false, error: null });
      return;
    }
    if (detail.resultFor === d.finished_at && (detail.result || detail.loading || detail.missing || detail.error)) return;
    Object.assign(detail, { resultFor: d.finished_at, loading: true, result: null, missing: false, error: null, copyMsg: null });
    refreshDetail();
    fetch(`/api/documents/${encodeURIComponent(id)}/result`, { cache: "no-store" })
      .then(async (r) => {
        if (!detail || detail.id !== id) return;
        if (r.status === 404) detail.missing = true;
        else if (!r.ok) detail.error = `HTTP ${r.status}`;
        else detail.result = await r.json();
      })
      .catch(() => { if (detail && detail.id === id) detail.error = "Brak połączenia z aplikacją."; })
      .finally(() => { if (detail && detail.id === id) { detail.loading = false; refreshDetail(); } });
  }

  function fileInfoRows(d) {
    const s = d.summary;
    const rows = [
      ["Nazwa pliku", `<span class="break">${esc(d.original_name)}</span>`],
      ["Rozmiar i strony", `${esc(formatSize(d.size))} · ${esc(d.pages ?? "—")} ${esc(pageWord(d.pages || 0))}`],
      ["Dodano", esc(formatDate(d.added_at))],
    ];
    if (d.finished_at && !isActive(d)) rows.push(["Zakończono", esc(formatDate(d.finished_at))]);
    if (d.seconds != null && !isActive(d)) rows.push(["Czas przetwarzania", esc(formatSeconds(d.seconds))]);
    rows.push(["OCR", ocrFull(d)]);
    if (s && !isActive(d) && s.ocr_pages_requested && s.ocr_pages_requested.length) {
      rows.push(["Strony przetworzone przez OCR", `${esc(s.ocr_pages_processed)} z ${esc(s.ocr_pages_requested.length)}
        <span class="muted small">(wymagane: ${esc(pageRanges(s.ocr_pages_requested))})</span>`]);
    }
    return kv(rows);
  }

  function statusNotice(d) {
    if (d.status === "added") {
      return `<div class="notice"><p>Dokument nie został jeszcze przetworzony. Kliknij „Przetwórz dokument” — wynik pojawi się w tym miejscu.</p>${d.note ? `<p class="small">${esc(d.note)}</p>` : ""}</div>`;
    }
    if (isActive(d)) {
      return `<div class="notice notice--info"><p>${d.status === "queued" ? "Dokument oczekuje w kolejce" : "Trwa przetwarzanie dokumentu"}. Wynik pojawi się automatycznie po zakończeniu.</p></div>`;
    }
    if (d.status === "failed") return `<div class="notice notice--error"><p><strong>Błąd przetwarzania.</strong> ${esc(d.error || "")} Możesz spróbować przetworzyć dokument ponownie.</p></div>`;
    if (isUnknown(d)) {
      return `<div class="notice notice--warning"><p><strong>Dokument nierozpoznany</strong> — ${esc(classReason(d.summary.classification_reason))}.
        Nie jest to odwołanie, pełnomocnictwo ani potwierdzenie przelewu, dlatego zgodnie z formatem organizatora zapisano pusty wynik <code>{}</code>.</p></div>`;
    }
    const fields = detail && detail.result && detail.result.diagnostics && detail.result.diagnostics.result && detail.result.diagnostics.result.fields;
    if (needsReview(d) && fields && fields.warnings && fields.warnings.length) {
      return `<div class="notice notice--warning"><p><strong>Do weryfikacji</strong> — części danych nie znaleziono w dokumencie:</p>
        <ul class="list">${fields.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`;
    }
    return "";
  }

  function resultSection(d) {
    if (!hasResult(d)) {
      const text = isActive(d) ? "Wynik będzie dostępny po zakończeniu przetwarzania." : "Przetwórz dokument, aby zobaczyć wyodrębnione dane i wynik JSON.";
      return panel("Wynik", empty("Brak wyniku", text));
    }
    if (detail.loading) return panel("Wynik", `<p class="muted">Wczytywanie wyniku…</p>`);
    if (detail.error) return panel("Wynik", `<div class="notice notice--warning"><p>Nie udało się pobrać wyniku: ${esc(detail.error)}</p></div>`);
    const out = detail.result && detail.result.output_text;
    if (out == null) {
      const rejected = detail.result && detail.result.diagnostics && detail.result.diagnostics.rejected && detail.result.diagnostics.rejected.length;
      return panel("Wynik", empty("Brak pliku JSON", rejected
        ? "Wynik nie przeszedł walidacji ze wzorcem JSON i nie został zapisany. Szczegóły w diagnostyce poniżej."
        : "Dla tego dokumentu nie zapisano wyniku JSON."));
    }
    let parsed = null;
    try { parsed = JSON.parse(out); } catch { parsed = null; }
    const fields = detail.result.diagnostics && detail.result.diagnostics.result && detail.result.diagnostics.result.fields;
    const evidence = (fields && fields.evidence) || {};
    const lines = out.split("\n").length - (out.endsWith("\n") ? 1 : 0);
    const tab = detail.tab === "json" ? "json" : "dane";
    const tabBtn = (key, label) => `<button type="button" role="tab" class="tab${tab === key ? " is-active" : ""}" aria-selected="${tab === key}"
      data-tab="${key}" data-key="tab-${key}">${label}</button>`;
    return `<section class="panel panel--result">
      <div class="panel__header panel__header--tabs">
        <div class="tabs" role="tablist" aria-label="Widok wyniku">${tabBtn("dane", "Dane")}${tabBtn("json", "JSON")}</div>
        <span class="muted small json-meta">${esc(jsonFileName(d.original_name))} · ${esc(formatSize(new Blob([out]).size))} · ${esc(lines)} ${esc(plural(lines, "wiersz", "wiersze", "wierszy"))}</span>
      </div>
      <div class="panel__body" role="tabpanel">
        ${d.status === "failed" ? `<p class="muted small">Mimo błędu zapisano plik output.json — tak samo zachowuje się wiersz poleceń (dla pliku, którego nie udało się odczytać, wynikiem jest pusty obiekt JSON).</p>` : ""}
        ${tab === "dane" ? dataTab(d, parsed, evidence)
          : `<pre class="json-view" id="json-view" tabindex="0" aria-label="Podgląd wyniku JSON">${highlightJson(out)}</pre>`}
      </div>
    </section>`;
  }

  function diagnosticsBlock(d) {
    const diag = detail && detail.result && detail.result.diagnostics;
    const hasDiag = hasResult(d) && diag && diag.result;
    const fileInfo = `<h3 class="subhead">Plik</h3>${fileInfoRows(d)}`;
    if (!hasDiag) {
      return `<details class="disclosure" id="diagnostics" ${detail.diagOpen ? "open" : ""}>
        <summary>Informacje o pliku</summary><div class="disclosure__body">${fileInfo}</div></details>`;
    }
    const r = diag.result;
    const c = r.classification || {};
    const ex = r.extraction || {};
    const ocr = ex.ocr || {};
    const fields = r.fields;
    const list = (items, none) => (items && items.length
      ? `<ul class="list small">${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<span class="muted">${esc(none)}</span>`);
    const classes = Object.keys(c.scores || {});
    const scoreTable = classes.length ? `<table class="table table--compact">
        <thead><tr><th scope="col">Typ</th><th scope="col">Punkty</th><th scope="col">Dopasowane sygnały</th></tr></thead>
        <tbody>${classes.map((k) => `<tr><td>${esc(LABELS[k] || k)}</td><td>${esc(c.scores[k])}</td>
          <td>${(c.signals[k] || []).length ? (c.signals[k] || []).map((x) => `<code>${esc(x)}</code>`).join(" ") : `<span class="muted">—</span>`}</td></tr>`).join("")}</tbody></table>`
      : `<p class="muted small">Klasyfikator nie oceniał typów (${esc(classReason(c.reason))}).</p>`;
    const validation = (fields && fields.validation_errors && fields.validation_errors.length) ? fields.validation_errors : (diag.rejected || []);
    const ocrRows = [["Stan", `${esc(OCR_STATUS[ocr.status] || ocr.status || "—")} <code>${esc(ocr.status || "")}</code>`]];
    if (ocr.reason) ocrRows.push(["Powód uruchomienia", esc(OCR_REASON[ocr.reason] || ocr.reason)]);
    if (ocr.engine) ocrRows.push(["Silnik", `${esc(ocr.engine)}, język ${esc(ocr.language)}, ${esc(ocr.dpi)} dpi`]);
    if (ocr.pages_requested && ocr.pages_requested.length) {
      ocrRows.push(["Strony", `wymagane: ${esc(pageRanges(ocr.pages_requested))}; przetworzone: ${esc(ocr.pages_processed)}; z tekstem: ${esc(ocr.pages_with_text)}`]);
      ocrRows.push(["Czas OCR", esc(formatSeconds(ocr.seconds || 0))]);
    }
    if (ocr.errors && ocr.errors.length) ocrRows.push(["Błędy OCR", list(ocr.errors, "")]);
    return `<details class="disclosure" id="diagnostics" ${detail.diagOpen ? "open" : ""}>
      <summary>Diagnostyka przetwarzania <span class="muted small">— klasyfikacja, OCR, ostrzeżenia, walidacja i plik</span></summary>
      <div class="disclosure__body">
        <h3 class="subhead">Klasyfikacja</h3>
        ${kv([
          ["Wynik", `${esc(c.label === "unknown" ? "Nierozpoznany" : (LABELS[c.label] || c.label))} <code>${esc(c.label)}</code>`],
          ["Powód", `${esc(classReason(c.reason))} <code>${esc(c.reason)}</code>`],
        ])}
        ${scoreTable}
        <h3 class="subhead">Przetwarzanie</h3>
        ${kv([
          ["Status przetwarzania", `${esc(PROCESSING_STATUS[r.status] || r.status)} <code>${esc(r.status)}</code>`],
          ["Źródło tekstu", esc(TEXT_SOURCE[ex.text_source] || ex.text_source || "—")],
          ["Strony z tekstem", `${esc(ex.pages_with_text ?? "—")} z ${esc(ex.page_count ?? "—")}`],
          ["Znaki alfanumeryczne", esc(ex.meaningful_chars ?? "—")],
          ...(ex.error ? [["Błąd odczytu", esc(ex.error)]] : []),
          ["Wersja aplikacji", `${esc(diag.app_version)} · ${esc(formatDate(diag.processed_at))}`],
        ])}
        <h3 class="subhead">OCR</h3>
        ${kv(ocrRows)}
        <h3 class="subhead">Ekstrakcja danych</h3>
        ${kv([
          ["Ostrzeżenia ekstrakcji", fields ? list(fields.warnings, "brak") : `<span class="muted">nie dotyczy (dokument nierozpoznany)</span>`],
          ["Błędy walidacji", list(validation, "brak")],
        ])}
        ${fileInfo}
      </div>
    </details>`;
  }

  /* Poprzedni / następny dokument (kolejność jak na liście). */
  function docNav(d) {
    const i = docs.findIndex((x) => x.id === d.id);
    const prev = docs[i - 1];
    const next = docs[i + 1];
    if (!prev && !next) return "";
    return `<nav class="doc-nav" aria-label="Nawigacja między dokumentami">
      ${prev ? `<a href="#/dokumenty/${esc(prev.id)}" title="${esc(prev.original_name)}">← Poprzedni</a>` : `<span></span>`}
      <span class="muted small">${i + 1} z ${docs.length}</span>
      ${next ? `<a href="#/dokumenty/${esc(next.id)}" title="${esc(next.original_name)}">Następny →</a>` : `<span></span>`}
    </nav>`;
  }

  function detailBody() {
    const id = routeParam();
    if (docsError) return `<div class="notice notice--warning"><p>Nie udało się pobrać danych dokumentu: ${esc(docsError)}</p></div>`;
    if (docs === null) return `<p class="muted">Wczytywanie…</p>`;
    const d = findDoc(id);
    if (!d) {
      return empty("Nie znaleziono dokumentu", "Dokument nie istnieje lub został usunięty.",
        `<p><a class="btn btn--secondary" href="#/dokumenty">Wróć do listy dokumentów</a></p>`);
    }
    if (!detail || detail.id !== id) detail = { id, diagOpen: false, tab: "dane" };
    const st = statusInfo(d);
    const s = d.summary;
    const typeBadge = s && !isActive(d) && s.processing_status !== "error"
      && s.doc_class !== "unknown" ? `<span class="type-label">${esc(LABELS[s.doc_class] || s.doc_class)}</span>` : "";
    const statusBadge = st.elapsed
      ? `<span class="badge badge--info">Przetwarzanie… <span data-elapsed="${esc(d.started_at)}">${esc(formatElapsed(d.started_at))}</span></span>`
      : badge(st.kind, st.text);
    const meta = [`${d.pages ?? "—"} ${pageWord(d.pages || 0)}`, ocrShort(s), d.seconds != null && !isActive(d) ? `czas ${formatSeconds(d.seconds)}` : ""]
      .filter(Boolean).join(" · ");
    const copyReady = detail.result && detail.result.output_text != null;
    const actions = `
      ${hasOutput(d) ? downloadLink(d, "btn--primary", "Pobierz JSON") : ""}
      ${hasOutput(d) ? `<button type="button" class="btn btn--secondary" data-copy data-key="copy" ${copyReady ? "" : "disabled"}>Kopiuj JSON</button>` : ""}
      <span class="detail-actions__spacer"></span>
      <button type="button" class="btn ${hasResult(d) ? "btn--ghost" : "btn--primary"}" data-process="${esc(d.id)}" data-key="detail-process" ${isActive(d) ? "disabled" : ""}>
        ${isActive(d) ? "Trwa przetwarzanie…" : hasResult(d) ? "Przetwórz ponownie" : "Przetwórz dokument"}</button>`;
    return `
      <div class="detail-summary">${typeBadge}${statusBadge}<span class="muted small">${esc(meta)}</span></div>
      <div class="detail-actions">${actions}</div>
      <div id="copy-status" role="status" aria-live="polite">${detail.copyMsg ? `<div class="notice notice--${detail.copyMsg.kind}"><p>${esc(detail.copyMsg.text)}</p></div>` : ""}</div>
      <div id="process-status" role="status" aria-live="polite"></div>
      ${statusNotice(d)}
      ${resultSection(d)}
      ${diagnosticsBlock(d)}
      ${docNav(d)}`;
  }

  function detailView() {
    const d = docs ? findDoc(routeParam()) : null;
    return `
      <p class="backlink"><a href="#/dokumenty">← Lista dokumentów</a></p>
      ${pageHeader("Szczegóły dokumentu", d ? d.original_name : "Szczegóły dokumentu")}
      <div id="detail">${detailBody()}</div>`;
  }

  function refreshDetail() {
    const box = document.getElementById("detail");
    if (!box) return;
    const focusKey = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.key : null;
    const pre = document.getElementById("json-view");
    const scroll = pre ? [pre.scrollTop, pre.scrollLeft] : null;
    box.innerHTML = detailBody();
    const newPre = document.getElementById("json-view");
    if (newPre && scroll) [newPre.scrollTop, newPre.scrollLeft] = scroll;
    if (focusKey) {
      const el = box.querySelector(`[data-key="${CSS.escape(focusKey)}"]`);
      if (el && !el.disabled) el.focus();
    }
    renderProcessNotice();
    const h1 = view.querySelector("h1");
    const d = findDoc(routeParam());
    if (h1 && d && h1.textContent !== d.original_name) { h1.textContent = d.original_name; document.title = `${d.original_name} — Odwołania2JSON`; }
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
    } catch { /* przejście do metody zapasowej */ }
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch {
      return false;
    }
  }

  async function handleCopy() {
    const text = detail && detail.result && detail.result.output_text;
    if (text == null) return;
    const ok = await copyText(text);
    detail.copyMsg = ok
      ? { kind: "success", text: "Skopiowano wynik JSON do schowka." }
      : { kind: "warning", text: "Schowek jest niedostępny w tej przeglądarce. Treść JSON została zaznaczona w zakładce „JSON” — skopiuj ją skrótem Ctrl+C (⌘C na Macu)." };
    if (!ok) { detail.tab = "json"; refreshDetail(); }
    const box = document.getElementById("copy-status");
    if (box) box.innerHTML = `<div class="notice notice--${detail.copyMsg.kind}"><p>${esc(detail.copyMsg.text)}</p></div>`;
    if (!ok) {
      const pre = document.getElementById("json-view");
      if (pre) { const range = document.createRange(); range.selectNodeContents(pre); const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range); }
    }
  }

  function mountDetail() {
    const box = document.getElementById("detail");
    box.addEventListener("click", (e) => {
      const proc = e.target.closest("[data-process]");
      if (proc) { detail.copyMsg = null; handleProcess([proc.dataset.process]); }
      if (e.target.closest("[data-copy]")) handleCopy();
      const tab = e.target.closest("[data-tab]");
      if (tab && detail) { detail.tab = tab.dataset.tab; refreshDetail(); }
    });
    box.addEventListener("toggle", (e) => {
      if (e.target.id === "diagnostics" && detail) detail.diagOpen = e.target.open;
    }, true);
    processNotice = null;
    renderProcessNotice();
  }

  const MOUNT = { dokumenty: () => (routeParam() ? mountDetail() : mountDocuments()) };

  /* ------------------------------------------------------------ widoki */
  const views = {
    pulpit: () => `
      ${pageHeader("Pulpit", "Przetwarzanie dokumentów spraw odwoławczych",
        "System rozpoznaje rodzaj dokumentu PDF, wyodrębnia z niego dane i zapisuje je w formacie JSON zgodnym z szablonami Urzędu Zamówień Publicznych.",
        `<a class="btn btn--primary" href="#/dokumenty">Dodaj dokumenty</a>`)}
      ${panel("Dokumenty w systemie", docsSummary(), `<a href="#/dokumenty">Przejdź do dokumentów</a>`)}
      <div class="grid-2">
        ${panel("Jak to działa", `
          <ol class="steps">
            <li><strong>Dodaj dokumenty PDF</strong><span>Pliki tekstowe lub skany — odwołanie, pełnomocnictwo, potwierdzenie przelewu.</span></li>
            <li><strong>Rozpocznij przetwarzanie</strong><span>Odczyt tekstu, a dla skanów rozpoznawanie tekstu (OCR).</span></li>
            <li><strong>Sprawdź rozpoznany typ i dane</strong><span>Typ dokumentu oraz wyodrębnione pola wraz z ich źródłem w dokumencie.</span></li>
            <li><strong>Pobierz wynik JSON</strong><span>Jeden plik JSON dla każdego dokumentu, o nazwie zgodnej z plikiem PDF.</span></li>
          </ol>`)}
        ${panel("Stan systemu", systemStatus(false))}
      </div>`,

    dokumenty: () => (routeParam() ? detailView() : `
      ${pageHeader("Dokumenty", "Dokumenty",
        "Dodaj dokumenty PDF, przetwórz je i sprawdź wynik. Wszystko odbywa się lokalnie na tym komputerze.")}
      <section class="panel">
        <div class="panel__body">
          <div class="dropzone${docs && docs.length ? " dropzone--compact" : ""}" id="dropzone">
            <p class="dropzone__title">Przeciągnij pliki PDF w to miejsce</p>
            <p class="muted small dropzone__or">lub</p>
            <button type="button" class="btn btn--primary" id="pick-files">Wybierz pliki</button>
            <input type="file" id="file-input" class="visually-hidden" accept=".pdf,application/pdf" multiple tabindex="-1" aria-hidden="true">
            <p class="muted small dropzone__hint">Pliki PDF (tekstowe i skany), maks. ${esc(formatSize(maxBytes))} na plik. Można dodać kilka plików jednocześnie.</p>
          </div>
          <div id="upload-status" role="status" aria-live="polite"></div>
        </div>
      </section>
      <section class="panel">
        <div class="panel__header"><h2 class="panel__title">Lista dokumentów</h2><span class="muted small" id="docs-count"></span></div>
        <div class="panel__body">
          <div id="process-status" role="status" aria-live="polite"></div>
          <div id="docs-list">${docsTable()}</div>
        </div>
      </section>`),

    przetwarzanie: () => `
      ${pageHeader("Przetwarzanie dokumentów", "Przebieg przetwarzania",
        "Każdy dokument przechodzi te same etapy. Wszystkie wykonywane są lokalnie.")}
      ${panel("Bieżące przetwarzanie", activeList(), `<a href="#/dokumenty">Przejdź do dokumentów</a>`)}
      ${panel("Etapy", `
        <ol class="steps">
          <li><strong>Odczyt tekstu</strong><span>Z warstwy tekstowej PDF; strony bez tekstu (skany) są rozpoznawane przez OCR.</span></li>
          <li><strong>Rozpoznanie typu dokumentu</strong><span>Reguły oparte na charakterystycznych sformułowaniach (plik konfiguracyjny); dokument niepasujący do żadnego typu jest oznaczany jako „Nierozpoznany”.</span></li>
          <li><strong>Wyodrębnienie danych</strong><span>Pola właściwe dla typu dokumentu wraz ze źródłem każdej wartości; brak informacji oznaczany jest wartością null.</span></li>
          <li><strong>Walidacja i zapis</strong><span>Wynik sprawdzany jest ze wzorcem przed zapisem pliku JSON.</span></li>
        </ol>`)}`,

    wyniki: () => `
      ${pageHeader("Wyniki i eksport", "Wyniki w formacie JSON",
        "Dla każdego dokumentu powstaje jeden plik JSON o nazwie zgodnej z nazwą pliku PDF.")}
      ${panel("Pliki do pobrania", resultsTable(), docs && docs.filter(hasOutput).length ? `<span class="muted small">${docs.filter(hasOutput).length} ${plural(docs.filter(hasOutput).length, "plik", "pliki", "plików")}</span>` : "")}
      ${panel("Struktura wyników", info ? `
        <table class="table">
          <thead><tr><th scope="col">Typ dokumentu</th><th scope="col">Główne sekcje JSON</th></tr></thead>
          <tbody>${info.classes.map((c) => `<tr><td>${esc(LABELS[c.label] || c.label)}</td><td><code>${c.template_keys.map(esc).join("</code>, <code>")}</code></td></tr>`).join("")}
            <tr><td>Dokument nierozpoznany</td><td><code>${esc(info.unknown_output)}</code></td></tr>
          </tbody>
        </table>` : `<p class="muted">Wczytywanie…</p>`)}
      <p class="muted small">Przetwarzanie całych folderów spraw (<code>input/</code> → <code>output/</code>) jest dostępne także z wiersza poleceń: <code>start.bat</code> / <code>./start.sh</code>.</p>`,

    informacje: () => `
      ${pageHeader("Informacje o systemie", "O systemie Odwołania2JSON",
        "Narzędzie przygotowane w ramach wyzwania Urzędu Zamówień Publicznych podczas HackNation 2026.")}
      ${panel("Stan systemu", systemStatus(true))}
      <div class="grid-2">
        ${panel("Rozpoznawane typy dokumentów", info ? `<ul class="list">${info.classes.map((c) =>
          `<li><strong>${esc(LABELS[c.label] || c.label)}</strong> <span class="muted small">— ${esc(c.description)}</span></li>`).join("")}
          <li><strong>Nierozpoznany</strong> <span class="muted small">— każdy inny dokument, np. orzeczenie Krajowej Izby Odwoławczej, ogłoszenie BZP lub TED, korespondencja</span></li></ul>` : "")}
        ${panel("Kategorie zaskarżonej czynności", info ? `<ol class="list small">${info.czynnosci.map((c) =>
          `<li value="${esc(c.id)}">${esc(c.opis)}</li>`).join("")}</ol>` : "")}
      </div>
      ${panel("Ograniczenia", `<ul class="list">
        <li>Błędy rozpoznawania tekstu w skanach mogą przenosić się do wyników (np. zniekształcone nazwy).</li>
        <li>Imiona i nazwiska w przypadkach zależnych nie są odmieniane do mianownika.</li>
        <li>Plik PDF zawierający kilka dokumentów jest rozpoznawany jako całość.</li>
        <li>Metryki jakości mierzone przez zespół są wewnętrzne — nie zastępują oceny organizatora.</li></ul>`)}`,
  };

  /* ------------------------------------------------------------ routing */
  function currentRoute() {
    const route = (location.hash.replace(/^#\/?/, "") || "pulpit").split("/")[0];
    return ROUTES.includes(route) ? route : "pulpit";
  }

  /* Identyfikator dokumentu w adresie #/dokumenty/<id> (tylko poprawny format). */
  function routeParam() {
    const parts = location.hash.replace(/^#\/?/, "").split("/");
    return parts[0] === "dokumenty" && /^[0-9a-f]{32}$/.test(parts[1] || "") ? parts[1] : null;
  }

  function render(focus) {
    const route = currentRoute();
    if (route !== "dokumenty") uploadReport = null;
    view.innerHTML = views[route]();
    if (MOUNT[route]) MOUNT[route]();
    document.querySelectorAll(".sidenav__link").forEach((a) => {
      if (a.dataset.route === route) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    const title = view.querySelector("h1");
    document.title = `${title ? title.textContent : ""} — Odwołania2JSON`;
    if (focus) {
      window.scrollTo(0, 0);
      if (title) title.focus({ preventScroll: true });
    }
  }

  /* ------------------------------------------------------------ menu mobilne */
  const toggle = document.querySelector(".nav-toggle");
  const sidenav = document.getElementById("sidenav");
  toggle.addEventListener("click", () => {
    const open = sidenav.classList.toggle("is-open");
    toggle.setAttribute("aria-expanded", String(open));
  });
  sidenav.addEventListener("click", (e) => {
    if (e.target.closest("a")) { sidenav.classList.remove("is-open"); toggle.setAttribute("aria-expanded", "false"); }
  });

  const afterLoad = () => {
    docsSignature = JSON.stringify(docs) + JSON.stringify(processNotice);
    if (currentRoute() === "dokumenty" && !routeParam()) refreshDocsList();
    else if (currentRoute() === "dokumenty") { refreshDetail(); ensureResult(); }
    else render(false);
    schedulePoll();
  };
  window.addEventListener("hashchange", () => {
    if (detail) detail.copyMsg = null;
    render(true);
    loadDocs().then(afterLoad);
  });
  render(false);
  loadDocs().then(afterLoad);

  fetch("/api/info")
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then((data) => { info = data; if (!(currentRoute() === "dokumenty")) render(false); })
    .catch((err) => { infoError = err.message; render(false); });
})();
