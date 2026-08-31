let totalPages = 1;
let pagesData = [];
let currentPage = 1;
let currentBookId = null;
let currentSpread = localStorage.getItem("buukuu-cbz-spread") || "single";
let currentDirection = localStorage.getItem("buukuu-cbz-direction") || "ltr";
let currentWebtoonWidth = localStorage.getItem("buukuu-cbz-webtoon-width") || "medium";
let progressDebounceTimer;
let webtoonObserver = null;
let isWebtoonRendered = false;
let isProgrammaticScroll = false;
let scrollTimeout = null;
const preloadedImages = new Map();

// Migrate legacy buukuu-cbz-mode if new keys are not yet set
if (!localStorage.getItem("buukuu-cbz-spread") && !localStorage.getItem("buukuu-cbz-direction")) {
  const legacyMode = localStorage.getItem("buukuu-cbz-mode");
  if (legacyMode === "manga") {
    currentSpread = "double";
    currentDirection = "rtl";
  } else if (legacyMode === "double") {
    currentSpread = "double";
    currentDirection = "ltr";
  } else if (legacyMode === "webtoon") {
    currentSpread = "webtoon";
    currentDirection = "ltr";
  } else {
    currentSpread = "single";
    currentDirection = "ltr";
  }
}

function getBookId() {
  if (typeof BOOK_ID !== "undefined" && BOOK_ID) return BOOK_ID;
  if (typeof window !== "undefined" && window.BOOK_ID) return window.BOOK_ID;
  const body = document.querySelector("body");
  if (body) {
    const dataId = body.getAttribute("data-book-id");
    if (dataId) return parseInt(dataId, 10);
  }
  const match = window.location.pathname.match(/\/reader\/cbz\/(\d+)/);
  if (match) return parseInt(match[1], 10);
  return null;
}

function getInitialPage() {
  if (typeof INITIAL_PAGE !== "undefined" && INITIAL_PAGE) return parseInt(INITIAL_PAGE, 10);
  if (typeof window !== "undefined" && window.INITIAL_PAGE) return parseInt(window.INITIAL_PAGE, 10);
  const body = document.querySelector("body");
  if (body) {
    const dataPage = body.getAttribute("data-initial-page");
    if (dataPage) return parseInt(dataPage, 10);
  }
  return 1;
}

async function initCBZReader() {
  currentBookId = getBookId();
  currentPage = getInitialPage();

  if (currentPage <= 1 && currentBookId) {
    const localPage = parseInt(localStorage.getItem("buukuu-cbz-progress-" + currentBookId), 10);
    if (localPage && localPage > 1) {
      currentPage = localPage;
    }
  }

  if (!currentBookId) {
    showError("Could not determine Book ID.");
    return;
  }

  showLoading("Opening comic pages...");

  try {
    const res = await fetch(`/api/books/${currentBookId}/pages`);
    if (!res.ok) {
      const errJson = await res.json().catch(() => ({}));
      throw new Error(errJson.error || `HTTP ${res.status}: Failed to load comic`);
    }

    const data = await res.json();
    if (!data || !Array.isArray(data.pages) || data.pages.length === 0) {
      throw new Error("No comic pages found in this archive.");
    }

    totalPages = data.total_pages || data.pages.length;
    pagesData = data.pages;

    // Validate current page bounds
    currentPage = Math.max(1, Math.min(totalPages, currentPage));

    // Setup spread dropdown
    const spreadSelect = document.getElementById("spread-select");
    if (spreadSelect) {
      spreadSelect.value = currentSpread;
    }

    // Setup webtoon width dropdown
    const widthSelect = document.getElementById("webtoon-width-select");
    if (widthSelect) {
      widthSelect.value = currentWebtoonWidth;
    }

    // Setup direction dropdown
    const directionSelect = document.getElementById("direction-select");
    if (directionSelect) {
      directionSelect.value = currentDirection;
    }

    // Setup page slider
    const slider = document.getElementById("page-slider");
    if (slider) {
      slider.min = 1;
      slider.max = totalPages;
      slider.value = currentPage;
    }
    updateSliderDirection();

    setupEvents();
    hideLoading();
    renderCurrentView(true);
  } catch (err) {
    console.error("Failed to initialize comic reader:", err);
    showError(err.message || "Failed to load comic pages.");
  }
}

function showLoading(msg = "Opening comic...") {
  const loading = document.getElementById("cbz-loading-state");
  if (loading) {
    loading.style.display = "flex";
    const msgElem = document.getElementById("cbz-loading-msg");
    if (msgElem) msgElem.innerText = msg;
  }
}

function hideLoading() {
  const loading = document.getElementById("cbz-loading-state");
  if (loading) {
    loading.style.display = "none";
  }
}

function showError(msg) {
  const container = document.getElementById("cbz-canvas-container");
  if (container) {
    container.innerHTML = `
      <div class="cbz-loading-state" style="color: #f87171;">
        <div style="font-size: 2rem;">⚠️</div>
        <div style="font-weight: 600; max-width: 80vw;">${escapeHtml(msg)}</div>
        <button class="btn btn-primary" onclick="initCBZReader()" style="margin-top: 0.5rem;">Retry</button>
      </div>
    `;
  }
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function updateSliderDirection() {
  const slider = document.getElementById("page-slider");
  if (slider) {
    if (currentDirection === "rtl" && currentSpread !== "webtoon") {
      slider.setAttribute("dir", "rtl");
    } else {
      slider.removeAttribute("dir");
    }
  }
}

function setupEvents() {
  const btnPrev = document.getElementById("btn-prev-page");
  const btnNext = document.getElementById("btn-next-page");
  if (btnPrev) btnPrev.addEventListener("click", () => { hideControls(); prevPage(); });
  if (btnNext) btnNext.addEventListener("click", () => { hideControls(); nextPage(); });

  const touchPrev = document.getElementById("touch-prev");
  const touchNext = document.getElementById("touch-next");
  const touchMenu = document.getElementById("touch-menu");

  if (touchPrev) {
    touchPrev.addEventListener("click", () => {
      if (currentSpread === "webtoon") return;
      hideControls();
      if (currentDirection === "rtl") nextPage(); else prevPage();
    });
  }
  if (touchNext) {
    touchNext.addEventListener("click", () => {
      if (currentSpread === "webtoon") return;
      hideControls();
      if (currentDirection === "rtl") prevPage(); else nextPage();
    });
  }
  if (touchMenu) {
    touchMenu.addEventListener("click", () => {
      if (currentSpread === "webtoon") return;
      toggleControls();
    });
  }

  const slider = document.getElementById("page-slider");
  if (slider) {
    slider.addEventListener("input", (e) => {
      goToPage(parseInt(e.target.value, 10));
    });
  }

  const spreadSelect = document.getElementById("spread-select");
  if (spreadSelect) {
    spreadSelect.addEventListener("change", (e) => {
      currentSpread = e.target.value;
      localStorage.setItem("buukuu-cbz-spread", currentSpread);
      isWebtoonRendered = false;
      renderCurrentView(true);
    });
  }

  const widthSelect = document.getElementById("webtoon-width-select");
  if (widthSelect) {
    widthSelect.addEventListener("change", (e) => {
      currentWebtoonWidth = e.target.value;
      localStorage.setItem("buukuu-cbz-webtoon-width", currentWebtoonWidth);
      applyWebtoonWidth();
    });
  }

  const directionSelect = document.getElementById("direction-select");
  if (directionSelect) {
    directionSelect.addEventListener("change", (e) => {
      currentDirection = e.target.value;
      localStorage.setItem("buukuu-cbz-direction", currentDirection);
      updateSliderDirection();
      renderCurrentView(true);
    });
  }

  const fullscreenBtn = document.getElementById("fullscreen-btn");
  if (fullscreenBtn) {
    fullscreenBtn.addEventListener("click", toggleFullscreen);
  }

  document.addEventListener("keydown", handleKeyNavigation);

  // Setup Webtoon Touch & Click Tap handling on viewport
  const viewport = document.getElementById("cbz-viewport");
  if (viewport) {
    let touchStartX = 0;
    let touchStartY = 0;
    let touchStartTime = 0;

    viewport.addEventListener("touchstart", (e) => {
      if (e.touches.length === 1) {
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
        touchStartTime = Date.now();
      }
    }, { passive: true });

    viewport.addEventListener("touchend", (e) => {
      if (currentSpread !== "webtoon") return;
      if (e.changedTouches.length === 1) {
        const deltaX = Math.abs(e.changedTouches[0].clientX - touchStartX);
        const deltaY = Math.abs(e.changedTouches[0].clientY - touchStartY);
        const deltaTime = Date.now() - touchStartTime;
        if (deltaX < 12 && deltaY < 12 && deltaTime < 350) {
          toggleControls();
        }
      }
    });

    viewport.addEventListener("click", (e) => {
      if (currentSpread === "webtoon") {
        // Toggle controls if clicking on the background or comic strip
        toggleControls();
      }
    });
  }
}

function handleKeyNavigation(e) {
  const isRTL = currentDirection === "rtl";

  if (currentSpread === "webtoon") {
    const viewport = document.getElementById("cbz-viewport");
    if (!viewport) return;
    const scrollStep = Math.round(viewport.clientHeight * 0.8);

    if (e.key === "ArrowDown" || e.key === "j" || e.key === " " || e.key === "PageDown") {
      e.preventDefault();
      viewport.scrollBy({ top: scrollStep, behavior: "smooth" });
    } else if (e.key === "ArrowUp" || e.key === "k" || e.key === "PageUp") {
      e.preventDefault();
      viewport.scrollBy({ top: -scrollStep, behavior: "smooth" });
    } else if (e.key === "Home") {
      e.preventDefault();
      goToPage(1);
    } else if (e.key === "End") {
      e.preventDefault();
      goToPage(totalPages);
    } else if (e.key === "f" || e.key === "F") {
      toggleFullscreen();
    } else if (e.key === "Escape") {
      hideControls();
    }
    return;
  }

  // Paginated modes (Single & Double)
  if (e.key === "ArrowLeft" || e.key === "h") {
    hideControls();
    if (isRTL) nextPage(); else prevPage();
  } else if (e.key === "ArrowRight" || e.key === "l") {
    hideControls();
    if (isRTL) prevPage(); else nextPage();
  } else if (e.key === "PageDown" || e.key === " ") {
    hideControls();
    nextPage();
  } else if (e.key === "PageUp") {
    hideControls();
    prevPage();
  } else if (e.key === "Home") {
    hideControls();
    goToPage(1);
  } else if (e.key === "End") {
    hideControls();
    goToPage(totalPages);
  } else if (e.key === "f" || e.key === "F") {
    toggleFullscreen();
  } else if (e.key === "Escape") {
    hideControls();
  }
}

function applyWebtoonWidth() {
  const viewport = document.getElementById("cbz-viewport");
  if (!viewport) return;
  viewport.classList.remove("width-compact", "width-medium", "width-large", "width-full");
  viewport.classList.add(`width-${currentWebtoonWidth}`);
}

function renderCurrentView(forceRebuild = false) {
  const container = document.getElementById("cbz-canvas-container");
  const viewport = document.getElementById("cbz-viewport");
  const widthSelect = document.getElementById("webtoon-width-select");
  const directionSelect = document.getElementById("direction-select");

  if (!container || !viewport) return;

  if (!pagesData || pagesData.length === 0) return;

  currentPage = Math.max(1, Math.min(totalPages, currentPage));
  updateSliderDirection();

  // 1. Continuous Scroll (Webtoon) Mode
  if (currentSpread === "webtoon") {
    document.body.classList.add("is-webtoon");
    viewport.classList.add("mode-webtoon");
    applyWebtoonWidth();

    if (widthSelect) widthSelect.style.display = "inline-block";
    if (directionSelect) directionSelect.style.display = "none";

    if (!isWebtoonRendered || forceRebuild) {
      container.innerHTML = pagesData.map(p => `
        <img src="${p.url}" alt="Page ${p.page_number}" class="cbz-page-img" loading="lazy" data-page="${p.page_number}" onerror="handleImageError(this)">
      `).join("");
      isWebtoonRendered = true;
      setupWebtoonObserver();
      setTimeout(() => scrollToWebtoonPage(currentPage), 50);
    }

    updatePageIndicator();
    syncProgressDebounced();
    return;
  }

  // Paginated modes (Single or Double)
  document.body.classList.remove("is-webtoon");
  viewport.classList.remove("mode-webtoon", "width-compact", "width-medium", "width-large", "width-full");
  container.className = "cbz-canvas-container";
  isWebtoonRendered = false;

  if (widthSelect) widthSelect.style.display = "none";
  if (directionSelect) directionSelect.style.display = "inline-block";

  if (webtoonObserver) {
    webtoonObserver.disconnect();
    webtoonObserver = null;
  }

  // 2. Double Page Spread Mode
  if (currentSpread === "double" && currentPage > 1) {
    container.classList.add("mode-double");
    const p1 = pagesData[currentPage - 1];
    const p2 = currentPage < totalPages ? pagesData[currentPage] : null;

    if (p1 && p2) {
      if (currentDirection === "rtl") {
        // Right-to-Left (Manga): Page N+1 on Left, Page N on Right
        container.innerHTML = `
          <img src="${p2.url}" alt="Page ${p2.page_number}" class="cbz-page-img" onerror="handleImageError(this)">
          <img src="${p1.url}" alt="Page ${p1.page_number}" class="cbz-page-img" onerror="handleImageError(this)">
        `;
      } else {
        // Left-to-Right (Western): Page N on Left, Page N+1 on Right
        container.innerHTML = `
          <img src="${p1.url}" alt="Page ${p1.page_number}" class="cbz-page-img" onerror="handleImageError(this)">
          <img src="${p2.url}" alt="Page ${p2.page_number}" class="cbz-page-img" onerror="handleImageError(this)">
        `;
      }
    } else if (p1) {
      container.innerHTML = `
        <img src="${p1.url}" alt="Page ${p1.page_number}" class="cbz-page-img" onerror="handleImageError(this)">
      `;
    }
  } else {
    // 3. Single Page Mode (or Cover Page 1)
    const p = pagesData[currentPage - 1];
    if (p) {
      container.innerHTML = `
        <img src="${p.url}" alt="Page ${p.page_number}" class="cbz-page-img" onerror="handleImageError(this)">
      `;
    }
  }

  updatePageIndicator();
  preloadAdjacentPages();
  syncProgressDebounced();
}

function setupWebtoonObserver() {
  if (webtoonObserver) {
    webtoonObserver.disconnect();
  }

  const viewport = document.getElementById("cbz-viewport");
  if (!viewport) return;

  const options = {
    root: viewport,
    rootMargin: "-30% 0px -30% 0px",
    threshold: [0, 0.25, 0.5, 0.75, 1.0]
  };

  webtoonObserver = new IntersectionObserver((entries) => {
    if (isProgrammaticScroll || currentSpread !== "webtoon") return;

    let bestEntry = null;
    let maxRatio = -1;

    entries.forEach(entry => {
      if (entry.isIntersecting && entry.intersectionRatio > maxRatio) {
        maxRatio = entry.intersectionRatio;
        bestEntry = entry;
      }
    });

    if (bestEntry && bestEntry.target) {
      const pageNum = parseInt(bestEntry.target.getAttribute("data-page"), 10);
      if (pageNum && pageNum !== currentPage) {
        currentPage = pageNum;
        const slider = document.getElementById("page-slider");
        if (slider) slider.value = currentPage;
        updatePageIndicator();
        syncProgressDebounced();
      }
    }
  }, options);

  const images = document.querySelectorAll(".mode-webtoon .cbz-page-img");
  images.forEach(img => webtoonObserver.observe(img));
}

function handleImageError(img) {
  img.style.display = "none";
  const parent = img.parentElement;
  if (parent && !parent.querySelector(".img-error-box")) {
    const errDiv = document.createElement("div");
    errDiv.className = "img-error-box";
    errDiv.style.cssText = "padding: 1.5rem; color: #f87171; text-align: center;";
    errDiv.innerHTML = `Failed to load page image.<br><button class="btn btn-secondary btn-sm" onclick="renderCurrentView(true)" style="margin-top: 0.5rem;">Reload</button>`;
    parent.appendChild(errDiv);
  }
}

function preloadAdjacentPages() {
  const nextIdx = currentPage;
  if (nextIdx < totalPages && pagesData[nextIdx]) {
    preloadImage(pagesData[nextIdx].url);
  }
  const nextNextIdx = currentPage + 1;
  if (nextNextIdx < totalPages && pagesData[nextNextIdx]) {
    preloadImage(pagesData[nextNextIdx].url);
  }
  const prevIdx = currentPage - 2;
  if (prevIdx >= 0 && pagesData[prevIdx]) {
    preloadImage(pagesData[prevIdx].url);
  }
}

function preloadImage(url) {
  if (!url || preloadedImages.has(url)) return;
  const img = new Image();
  img.src = url;
  preloadedImages.set(url, img);
}

function nextPage() {
  if (currentSpread === "webtoon") {
    goToPage(Math.min(totalPages, currentPage + 1));
    return;
  }
  if (currentSpread === "double" && currentPage > 1) {
    goToPage(Math.min(totalPages, currentPage + 2));
  } else {
    goToPage(Math.min(totalPages, currentPage + 1));
  }
}

function prevPage() {
  if (currentSpread === "webtoon") {
    goToPage(Math.max(1, currentPage - 1));
    return;
  }
  if (currentSpread === "double" && currentPage > 2) {
    goToPage(Math.max(1, currentPage - 2));
  } else {
    goToPage(Math.max(1, currentPage - 1));
  }
}

function goToPage(page) {
  currentPage = Math.max(1, Math.min(totalPages, page));
  const slider = document.getElementById("page-slider");
  if (slider) slider.value = currentPage;

  if (currentSpread === "webtoon") {
    updatePageIndicator();
    scrollToWebtoonPage(currentPage);
    syncProgressDebounced();
  } else {
    renderCurrentView(false);
  }
}

function updatePageIndicator() {
  const indicator = document.getElementById("page-indicator");
  if (indicator) {
    indicator.innerText = `${currentPage} / ${totalPages}`;
  }
  const percentElem = document.getElementById("progress-percent");
  if (percentElem) {
    const percent = Math.round((currentPage / totalPages) * 100);
    percentElem.innerText = `${percent}%`;
  }
}

function scrollToWebtoonPage(page) {
  const img = document.querySelector(`.mode-webtoon img[data-page="${page}"]`);
  if (img) {
    isProgrammaticScroll = true;
    img.scrollIntoView({ behavior: "smooth", block: "start" });
    clearTimeout(scrollTimeout);
    scrollTimeout = setTimeout(() => {
      isProgrammaticScroll = false;
    }, 600);
  }
}

function toggleControls() {
  const header = document.getElementById("cbz-header");
  if (header) header.classList.toggle("hidden");
  document.body.classList.toggle("controls-hidden");
}

function hideControls() {
  const header = document.getElementById("cbz-header");
  if (header) header.classList.add("hidden");
  document.body.classList.add("controls-hidden");
}

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(() => {});
  } else {
    document.exitFullscreen().catch(() => {});
  }
}

function syncProgressDebounced() {
  clearTimeout(progressDebounceTimer);
  progressDebounceTimer = setTimeout(async () => {
    const bookId = currentBookId || getBookId();
    if (!bookId || !totalPages || totalPages <= 0) return;

    localStorage.setItem("buukuu-cbz-progress-" + bookId, currentPage.toString());

    const percent = Math.round((currentPage / totalPages) * 100);
    try {
      await fetch(`/api/books/${bookId}/progress`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          location: currentPage.toString(),
          percentage: percent,
          is_completed: currentPage >= totalPages
        })
      });
    } catch (err) {
      console.warn("Failed to sync comic progress", err);
    }
  }, 800);
}

window.addEventListener("pagehide", () => {
  const bookId = currentBookId || getBookId();
  if (bookId && currentPage > 0 && totalPages > 0) {
    localStorage.setItem("buukuu-cbz-progress-" + bookId, currentPage.toString());
    const percent = Math.round((currentPage / totalPages) * 100);
    const data = JSON.stringify({
      location: currentPage.toString(),
      percentage: percent,
      is_completed: currentPage >= totalPages
    });
    if (navigator.sendBeacon) {
      navigator.sendBeacon(`/api/books/${bookId}/progress`, new Blob([data], { type: "application/json" }));
    }
  }
});

// Immediate check or DOMContentLoaded
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initCBZReader);
} else {
  initCBZReader();
}
