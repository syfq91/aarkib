let book;
let rendition;
let currentFontSize = 100;
let currentFlow = localStorage.getItem("aarkib-reader-flow") || "paginated";
let currentTheme = localStorage.getItem("aarkib-reader-theme") || "dark";
let currentSpread = localStorage.getItem("aarkib-reader-spread") || "auto";
let progressDebounceTimer;
let flattenedToc = [];
let lastKnownChapterTitle = "";

function getSpreadOptions(spreadSetting) {
  if (spreadSetting === "1") {
    return { spread: "none", minSpreadWidth: 0 };
  } else if (spreadSetting === "2") {
    return { spread: "always", minSpreadWidth: 0 };
  } else {
    return { spread: "auto", minSpreadWidth: 800 };
  }
}

const THEME_STYLES = {
  dark: {
    "body": {
      "background-color": "#181a1b !important",
      "color": "#e8e6e3 !important",
      "font-family": "system-ui, -apple-system, sans-serif !important",
      "padding": "0.75rem 1.5rem !important",
      "box-sizing": "border-box !important"
    },
    "p, div, span, li, h1, h2, h3, h4, h5, h6": {
      "color": "#e8e6e3 !important"
    },
    "a": {
      "color": "#818cf8 !important"
    },
    "img": {
      "max-width": "100% !important",
      "max-height": "calc(100vh - 2rem) !important",
      "width": "auto !important",
      "height": "auto !important",
      "object-fit": "contain !important",
      "display": "block !important",
      "margin": "0 auto !important",
      "page-break-inside": "avoid !important",
      "break-inside": "avoid !important"
    },
    "svg": {
      "max-width": "100% !important",
      "max-height": "calc(100vh - 2rem) !important",
      "height": "auto !important",
      "display": "block !important",
      "margin": "0 auto !important",
      "page-break-inside": "avoid !important",
      "break-inside": "avoid !important"
    }
  },
  light: {
    "body": {
      "background-color": "#ffffff !important",
      "color": "#1e293b !important",
      "font-family": "system-ui, -apple-system, sans-serif !important",
      "padding": "0.75rem 1.5rem !important",
      "box-sizing": "border-box !important"
    },
    "p, div, span, li, h1, h2, h3, h4, h5, h6": {
      "color": "#1e293b !important"
    },
    "a": {
      "color": "#4f46e5 !important"
    },
    "img": {
      "max-width": "100% !important",
      "max-height": "calc(100vh - 2rem) !important",
      "width": "auto !important",
      "height": "auto !important",
      "object-fit": "contain !important",
      "display": "block !important",
      "margin": "0 auto !important",
      "page-break-inside": "avoid !important",
      "break-inside": "avoid !important"
    },
    "svg": {
      "max-width": "100% !important",
      "max-height": "calc(100vh - 2rem) !important",
      "height": "auto !important",
      "display": "block !important",
      "margin": "0 auto !important",
      "page-break-inside": "avoid !important",
      "break-inside": "avoid !important"
    }
  },
  sepia: {
    "body": {
      "background-color": "#f4ecd8 !important",
      "color": "#5b4636 !important",
      "font-family": "Georgia, serif !important",
      "padding": "0.75rem 1.5rem !important",
      "box-sizing": "border-box !important"
    },
    "p, div, span, li, h1, h2, h3, h4, h5, h6": {
      "color": "#5b4636 !important"
    },
    "a": {
      "color": "#b45309 !important"
    },
    "img": {
      "max-width": "100% !important",
      "max-height": "calc(100vh - 2rem) !important",
      "width": "auto !important",
      "height": "auto !important",
      "object-fit": "contain !important",
      "display": "block !important",
      "margin": "0 auto !important",
      "page-break-inside": "avoid !important",
      "break-inside": "avoid !important"
    },
    "svg": {
      "max-width": "100% !important",
      "max-height": "calc(100vh - 2rem) !important",
      "height": "auto !important",
      "display": "block !important",
      "margin": "0 auto !important",
      "page-break-inside": "avoid !important",
      "break-inside": "avoid !important"
    }
  },
  oled: {
    "body": {
      "background-color": "#000000 !important",
      "color": "#ffffff !important",
      "font-family": "system-ui, -apple-system, sans-serif !important",
      "padding": "0.75rem 1.5rem !important",
      "box-sizing": "border-box !important"
    },
    "p, div, span, li, h1, h2, h3, h4, h5, h6": {
      "color": "#ffffff !important"
    },
    "a": {
      "color": "#a5b4fc !important"
    },
    "img": {
      "max-width": "100% !important",
      "max-height": "calc(100vh - 2rem) !important",
      "width": "auto !important",
      "height": "auto !important",
      "object-fit": "contain !important",
      "display": "block !important",
      "margin": "0 auto !important",
      "page-break-inside": "avoid !important",
      "break-inside": "avoid !important"
    },
    "svg": {
      "max-width": "100% !important",
      "max-height": "calc(100vh - 2rem) !important",
      "height": "auto !important",
      "display": "block !important",
      "margin": "0 auto !important",
      "page-break-inside": "avoid !important",
      "break-inside": "avoid !important"
    }
  }
};

function adjustContentImages(contents) {
  const doc = contents.document;
  if (!doc) return;

  const styleId = "aarkib-reader-injected-styles";
  if (!doc.getElementById(styleId)) {
    const style = doc.createElement("style");
    style.id = styleId;
    style.textContent = `
      html, body {
        box-sizing: border-box;
        height: 100% !important;
        min-height: 100% !important;
      }
      
      /* Base img & svg rules */
      img {
        max-width: 100% !important;
        max-height: calc(100vh - 2rem) !important;
        width: auto;
        height: auto;
        object-fit: contain !important;
        object-position: center !important;
        display: block !important;
        margin: 0.5rem auto !important;
        page-break-inside: avoid !important;
        break-inside: avoid !important;
      }
      
      /* Inline small ornaments / icons */
      img.orn, img.orn2, img.sborn, img.sborn1, img.sborn2, img.cir {
        display: inline-block !important;
        max-height: 2em !important;
        width: auto !important;
        margin: 0 !important;
      }
      
      svg {
        display: block !important;
        width: 100% !important;
        height: 100% !important;
        max-width: 100% !important;
        max-height: calc(100vh - 1.5rem) !important;
        margin: 0 auto !important;
        object-fit: contain !important;
        page-break-inside: avoid !important;
        break-inside: avoid !important;
      }
      
      svg image {
        width: 100% !important;
        height: 100% !important;
        max-width: 100% !important;
        max-height: 100% !important;
        object-fit: contain !important;
      }
      
      /* Full-page illustration & Cover wrappers */
      .image_full,
      .cover_image,
      .full-page-image,
      .duo-page-image,
      .fullscreen,
      [class*="image_full"],
      [class*="cover_image"],
      section[epub\\:type*="cover"],
      section[epub\\:type*="frontmatter"],
      section[epub\\:type*="bodymatter"] {
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
        align-items: center !important;
        width: 100% !important;
        max-width: 100% !important;
        height: 100% !important;
        min-height: 100% !important;
        margin: 0 auto !important;
        padding: 0 !important;
        text-align: center !important;
        box-sizing: border-box !important;
        page-break-inside: avoid !important;
        break-inside: avoid !important;
      }
      
      .image_full a,
      .cover_image a,
      [class*="image_full"] a,
      [class*="cover_image"] a {
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
        width: 100% !important;
        height: 100% !important;
        margin: 0 auto !important;
        text-align: center !important;
      }
      
      .image_full img,
      .cover_image img,
      .full-page-image img,
      [class*="image_full"] img,
      [class*="cover_image"] img,
      #coverimage,
      body.aarkib-fullpage-illustration img {
        width: 100% !important;
        height: 100% !important;
        max-width: 100% !important;
        max-height: calc(100vh - 1.5rem) !important;
        object-fit: contain !important;
        object-position: center !important;
        display: block !important;
        margin: 0 auto !important;
        page-break-inside: avoid !important;
        break-inside: avoid !important;
      }
      
      .pc, .pc-rw {
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
        align-items: center !important;
        text-align: center !important;
        margin: 0.5rem auto !important;
        page-break-inside: avoid !important;
        break-inside: avoid !important;
      }
      
      .pc img {
        max-width: 100% !important;
        max-height: calc(100vh - 2rem) !important;
        width: auto !important;
        height: auto !important;
        object-fit: contain !important;
        object-position: center !important;
        display: block !important;
        margin: 0 auto !important;
      }
      
      /* Pure illustration / Cover fullpage mode */
      body.aarkib-fullpage-illustration {
        padding: 0.5rem !important;
        margin: 0 !important;
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
        align-items: center !important;
        height: 100% !important;
        min-height: 100% !important;
        text-align: center !important;
        box-sizing: border-box !important;
        overflow: hidden !important;
      }
      
      body.aarkib-fullpage-illustration > div,
      body.aarkib-fullpage-illustration > section,
      body.aarkib-fullpage-illustration .galley-rw,
      body.aarkib-fullpage-illustration section {
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
        align-items: center !important;
        width: 100% !important;
        height: 100% !important;
        margin: 0 auto !important;
        padding: 0 !important;
        text-align: center !important;
      }
    `;
    (doc.head || doc.documentElement).appendChild(style);
  }

  // 2. Direct DOM adjustments for images
  const images = doc.querySelectorAll("img, svg");
  if (images.length > 0) {
    const textContent = (doc.body ? doc.body.innerText || "" : "").trim();
    const isImageChapter = doc.querySelector(".image_full, .cover_image, #coverimage, [class*='image_full'], [class*='cover_image']") ||
                          (textContent.length < 80 && images.length <= 2);

    if (isImageChapter && doc.body) {
      doc.body.classList.add("aarkib-fullpage-illustration");
      
      images.forEach(img => {
        let parent = img.parentElement;
        while (parent && parent !== doc.body && parent !== doc.documentElement) {
          parent.style.setProperty("display", "flex", "important");
          parent.style.setProperty("flex-direction", "column", "important");
          parent.style.setProperty("justify-content", "center", "important");
          parent.style.setProperty("align-items", "center", "important");
          parent.style.setProperty("width", "100%", "important");
          parent.style.setProperty("height", "100%", "important");
          parent.style.setProperty("max-height", "100%", "important");
          parent.style.setProperty("margin", "0 auto", "important");
          parent.style.setProperty("padding", "0", "important");
          parent.style.setProperty("text-align", "center", "important");
          parent = parent.parentElement;
        }

        if (img.tagName.toLowerCase() === "img") {
          img.style.setProperty("width", "100%", "important");
          img.style.setProperty("height", "100%", "important");
          img.style.setProperty("max-width", "100%", "important");
          img.style.setProperty("max-height", "calc(100vh - 1.5rem)", "important");
          img.style.setProperty("object-fit", "contain", "important");
          img.style.setProperty("object-position", "center", "important");
          img.style.setProperty("display", "block", "important");
          img.style.setProperty("margin", "0 auto", "important");
        } else if (img.tagName.toLowerCase() === "svg") {
          img.style.setProperty("width", "100%", "important");
          img.style.setProperty("height", "100%", "important");
          img.style.setProperty("max-width", "100%", "important");
          img.style.setProperty("max-height", "calc(100vh - 1.5rem)", "important");
          img.style.setProperty("display", "block", "important");
          img.style.setProperty("margin", "0 auto", "important");
        }
      });
    }
  }
}

function showLoading(msg = "Opening book...") {
  const loading = document.getElementById("loading-state");
  if (loading) {
    loading.style.display = "flex";
    const msgElem = document.getElementById("loading-msg");
    if (msgElem) msgElem.innerText = msg;
  }
}

function hideLoading() {
  const loading = document.getElementById("loading-state");
  if (loading) {
    loading.style.display = "none";
  }
}

function showError(msg) {
  const viewer = document.getElementById("viewer");
  if (viewer) {
    viewer.innerHTML = `
      <div class="loading-state" style="color: #ef4444;">
        <div style="font-size: 2rem; margin-bottom: 0.5rem;">⚠️</div>
        <div style="font-weight: 600; font-size: 1.1rem; margin-bottom: 0.5rem;">Failed to load EPUB</div>
        <div style="color: #94a3b8; font-size: 0.9rem; max-width: 380px; margin-bottom: 1.5rem;">${msg}</div>
        <a href="/media/${BOOK_ID}" class="btn btn-secondary">← Back to Details</a>
      </div>
    `;
  }
}

function getEpubBookId() {
  if (typeof BOOK_ID !== "undefined" && BOOK_ID) return BOOK_ID;
  if (typeof window !== "undefined" && window.BOOK_ID) return window.BOOK_ID;
  const body = document.querySelector("body");
  if (body) {
    const dataId = body.getAttribute("data-book-id");
    if (dataId) return parseInt(dataId, 10);
  }
  const match = window.location.pathname.match(/\/reader\/epub\/(\d+)/);
  if (match) return parseInt(match[1], 10);
  return null;
}

function getEpubBookUrl() {
  if (typeof BOOK_URL !== "undefined" && BOOK_URL) return BOOK_URL;
  if (typeof window !== "undefined" && window.BOOK_URL) return window.BOOK_URL;
  const bookId = getEpubBookId();
  if (bookId) return `/api/media/${bookId}/file`;
  return null;
}

function getEpubInitialLocation() {
  if (typeof INITIAL_LOCATION !== "undefined" && INITIAL_LOCATION && INITIAL_LOCATION !== "0" && INITIAL_LOCATION !== "" && INITIAL_LOCATION !== "completed") {
    return INITIAL_LOCATION;
  }
  if (typeof window !== "undefined" && window.INITIAL_LOCATION && window.INITIAL_LOCATION !== "0" && window.INITIAL_LOCATION !== "" && window.INITIAL_LOCATION !== "completed") {
    return window.INITIAL_LOCATION;
  }
  const body = document.querySelector("body");
  if (body) {
    const dataLoc = body.getAttribute("data-initial-location");
    if (dataLoc && dataLoc !== "0" && dataLoc !== "" && dataLoc !== "completed") return dataLoc;
  }
  const bookId = getEpubBookId();
  if (bookId) {
    const localLoc = localStorage.getItem("aarkib-progress-" + bookId);
    if (localLoc && localLoc !== "0" && localLoc !== "" && localLoc !== "completed") return localLoc;
  }
  return null;
}

function getPercentage(location) {
  if (!location || !location.start) return 0;

  // 1. Try exact percentage from generated book.locations
  if (book && book.locations && book.locations.length() > 0 && location.start.cfi) {
    try {
      const p = book.locations.percentageFromCfi(location.start.cfi);
      if (typeof p === "number" && !isNaN(p) && p >= 0) {
        return Math.floor(p * 100);
      }
    } catch (e) {}
  }

  // 2. Try location.start.percentage
  if (location.start.percentage != null && !isNaN(location.start.percentage) && location.start.percentage > 0) {
    return Math.floor(location.start.percentage * 100);
  }

  // 3. Fallback: spine index approximation
  if (book && book.spine && book.spine.spineItems && book.spine.spineItems.length > 0) {
    let spineIdx = location.start.index;
    if (spineIdx == null && location.start.href) {
      const target = resolveSpineTarget(location.start.href);
      const sp = book.spine.get(target);
      if (sp) spineIdx = sp.index;
    }
    if (spineIdx != null && spineIdx >= 0) {
      const totalSpine = book.spine.spineItems.length;
      return Math.min(99, Math.floor((spineIdx / totalSpine) * 100));
    }
  }

  return 0;
}

async function initReader() {
  document.body.setAttribute("data-reader-theme", currentTheme);
  showLoading("Fetching book data...");

  try {
    const bookUrl = getEpubBookUrl();
    if (!bookUrl) {
      throw new Error("Could not determine book URL.");
    }

    // 1. Fetch file as ArrayBuffer for reliable JSZip decoding
    let bookSource;
    try {
      const response = await fetch(bookUrl);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status} when loading book file`);
      }
      bookSource = await response.arrayBuffer();
    } catch (fetchErr) {
      console.warn("ArrayBuffer fetch failed, attempting URL string fallback:", fetchErr);
      bookSource = bookUrl;
    }

    showLoading("Rendering book pages...");

    // 2. Initialize ePub
    book = ePub(bookSource);

    // 3. Render to viewer
    const spreadOpts = getSpreadOptions(currentSpread);
    rendition = book.renderTo("viewer", {
      width: "100%",
      height: "100%",
      spread: spreadOpts.spread,
      minSpreadWidth: spreadOpts.minSpreadWidth,
      flow: currentFlow
    });

    // 4. Register themes
    document.body.setAttribute("data-reader-theme", currentTheme);
    Object.keys(THEME_STYLES).forEach(themeName => {
      rendition.themes.register(themeName, THEME_STYLES[themeName]);
    });
    rendition.themes.select(currentTheme);
    rendition.themes.fontSize(`${currentFontSize}%`);

    // 5. Hook content iframe to bridge events and styling
    rendition.hooks.content.register((contents) => {
      // Forward keyup events inside iframe to outer window handler
      const doc = contents.document;
      if (doc) {
        doc.addEventListener("keyup", handleKeyNavigation);
        doc.addEventListener("click", handleIframeClick);
        adjustContentImages(contents);
      }
    });

    // 6. Initial display (with fallback if saved location fails)
    const targetLocation = getEpubInitialLocation();
    let displayPromise;
    if (targetLocation) {
      displayPromise = rendition.display(targetLocation).catch(err => {
        console.warn("Saved CFI display error, falling back to start:", err);
        return rendition.display();
      });
    } else {
      displayPromise = rendition.display();
    }

    displayPromise.then(() => {
      hideLoading();
    }).catch(err => {
      console.error("Display error:", err);
      hideLoading();
    });

    // 7. Load Table of Contents
    book.loaded.navigation.then(nav => {
      if (nav && nav.toc) {
        renderTOC(nav.toc);
        buildFlattenedToc(nav.toc);
        if (rendition && rendition.currentLocation()) {
          updateProgressUI(rendition.currentLocation());
        }
      }
    }).catch(err => console.debug("TOC loading error:", err));

    // 8. Generate locations in background for percentage tracking
    book.ready.then(() => {
      return book.locations.generate(1024);
    }).then(() => {
      if (rendition && rendition.currentLocation()) {
        updateProgressUI(rendition.currentLocation());
      }
    }).catch(err => console.debug("Locations generation error:", err));

    // 9. Position change listener
    let isFirstLocationEvent = true;
    rendition.on("relocated", location => {
      updateProgressUI(location);
      syncProgressDebounced(location);
      if (isFirstLocationEvent) {
        isFirstLocationEvent = false;
      } else {
        hideHeader();
      }
    });

    rendition.on("rendered", () => {
      hideLoading();
    });

    // 10. Hook outer UI events
    setupUIEventListeners();
    updateSettingsUI();

  } catch (err) {
    console.error("Failed to initialize EPUB reader:", err);
    showError(err.message || "Unknown error opening EPUB");
  }
}

function handleIframeClick(e) {
  // If clicked a link, let ePub.js handle it
  if (e.target.closest("a")) return;

  // If user selected text, don't trigger navigation / header toggle
  const doc = e.target.ownerDocument;
  if (doc && doc.getSelection && doc.getSelection().toString().trim().length > 0) {
    return;
  }

  const width = window.innerWidth;
  const height = window.innerHeight;
  const x = e.clientX;
  const y = e.clientY;

  // Top 20% of page OR Middle area -> Toggle header
  if (y < height * 0.20 || (x >= width * 0.20 && x <= width * 0.80)) {
    toggleHeader();
    return;
  }

  // Left 20% = Prev
  if (x < width * 0.20) {
    hideHeader();
    rendition.prev();
  }
  // Right 20% = Next
  else if (x > width * 0.80) {
    hideHeader();
    rendition.next();
  }
}

function setupUIEventListeners() {
  // Key navigation on document
  document.addEventListener("keyup", handleKeyNavigation);

  // Outer document click (e.g. top of page)
  document.addEventListener("click", (e) => {
    if (e.target.closest("#reader-header") || e.target.closest(".sidebar") || e.target.closest("#overlay") || e.target.closest("#reader-footer")) {
      return;
    }
    const height = window.innerHeight;
    if (e.clientY < height * 0.20) {
      toggleHeader();
    }
  });

  // Touch zones
  const zonePrev = document.getElementById("zone-prev");
  const zoneNext = document.getElementById("zone-next");
  const zoneCenter = document.getElementById("zone-center");
  if (zonePrev) zonePrev.addEventListener("click", () => { hideHeader(); rendition && rendition.prev(); });
  if (zoneNext) zoneNext.addEventListener("click", () => { hideHeader(); rendition && rendition.next(); });
  if (zoneCenter) zoneCenter.addEventListener("click", toggleHeader);

  // Sidebars
  const tocBtn = document.getElementById("toc-btn");
  const settingsBtn = document.getElementById("settings-btn");
  const bookmarkBtn = document.getElementById("bookmark-btn");
  const closeTocBtn = document.getElementById("close-toc-btn");
  const closeSettingsBtn = document.getElementById("close-settings-btn");
  const overlay = document.getElementById("overlay");

  if (tocBtn) tocBtn.addEventListener("click", () => openSidebar("toc-sidebar"));
  if (settingsBtn) settingsBtn.addEventListener("click", () => openSidebar("settings-sidebar"));
  if (bookmarkBtn) bookmarkBtn.addEventListener("click", addCurrentBookmark);
  if (closeTocBtn) closeTocBtn.addEventListener("click", closeSidebars);
  if (closeSettingsBtn) closeSettingsBtn.addEventListener("click", closeSidebars);
  if (overlay) overlay.addEventListener("click", closeSidebars);
  setupEPUBGamepad();
}

function setupEPUBGamepad() {
  if (!window.AarkibGamepad) return;

  window.AarkibGamepad.setContext("epub", {
    getPrompts: () => [
      { key: "A", label: "Next" },
      { key: "B", label: "Exit" },
      { key: "◄/►", label: "Turn Page" },
      { key: "X", label: "Header" },
      { key: "Y", label: "TOC" },
      { key: "Start", label: "Settings" }
    ],
    onNavigate: (dir) => {
      const openSidebarEl = document.querySelector(".sidebar.open");
      if (openSidebarEl) {
        return false;
      }
      if (!rendition) return false;
      if (dir === "left") {
        hideHeader();
        rendition.prev();
        return true;
      }
      if (dir === "right") {
        hideHeader();
        rendition.next();
        return true;
      }
      return false;
    },
    onSelect: () => {
      const openSidebarEl = document.querySelector(".sidebar.open");
      if (openSidebarEl) return false;
      hideHeader();
      if (rendition) rendition.next();
      return true;
    },
    onBack: () => {
      const openSidebarEl = document.querySelector(".sidebar.open");
      if (openSidebarEl) {
        closeSidebars();
        return true;
      }
      const bId = typeof BOOK_ID !== "undefined" ? BOOK_ID : null;
      window.location.href = bId ? `/media/${bId}` : "/";
      return true;
    },
    onActionX: () => {
      toggleHeader();
      return true;
    },
    onActionY: () => {
      const toc = document.getElementById("toc-sidebar");
      if (toc && toc.classList.contains("open")) {
        closeSidebars();
      } else {
        openSidebar("toc-sidebar");
      }
      return true;
    },
    onStart: () => {
      const s = document.getElementById("settings-sidebar");
      if (s && s.classList.contains("open")) {
        closeSidebars();
      } else {
        openSidebar("settings-sidebar");
      }
      return true;
    },
    onBumperLeft: () => {
      hideHeader();
      if (rendition) rendition.prev();
      return true;
    },
    onBumperRight: () => {
      hideHeader();
      if (rendition) rendition.next();
      return true;
    },
    onTriggerLeft: () => {
      hideHeader();
      if (rendition) {
        for (let i = 0; i < 5; i++) rendition.prev();
      }
      return true;
    },
    onTriggerRight: () => {
      hideHeader();
      if (rendition) {
        for (let i = 0; i < 5; i++) rendition.next();
      }
      return true;
    }
  });
}

function handleKeyNavigation(e) {
  if (!rendition) return;
  if (e.key === "ArrowLeft" || e.key === "PageUp" || e.key === "h") {
    hideHeader();
    rendition.prev();
  } else if (e.key === "ArrowRight" || e.key === "PageDown" || e.key === " " || e.key === "l") {
    hideHeader();
    rendition.next();
  } else if (e.key === "Escape") {
    closeSidebars();
    hideHeader();
  }
}

function hideHeader() {
  const header = document.getElementById("reader-header");
  if (header && !header.classList.contains("hidden")) {
    header.classList.add("hidden");
  }
}

function showHeader() {
  const header = document.getElementById("reader-header");
  if (header && header.classList.contains("hidden")) {
    header.classList.remove("hidden");
  }
}

function toggleHeader() {
  const header = document.getElementById("reader-header");
  if (header) {
    header.classList.toggle("hidden");
  }
}

const toggleControls = toggleHeader;

function openSidebar(id) {
  closeSidebars();
  const sidebar = document.getElementById(id);
  const overlay = document.getElementById("overlay");
  if (sidebar) sidebar.classList.add("open");
  if (overlay) overlay.classList.add("active");
}

function closeSidebars() {
  document.querySelectorAll(".sidebar").forEach(s => s.classList.remove("open"));
  const overlay = document.getElementById("overlay");
  if (overlay) overlay.classList.remove("active");
}

function resolveSpineTarget(target, itemId = null) {
  if (!book || !book.spine || !book.spine.spineItems || book.spine.spineItems.length === 0) {
    return target;
  }

  // If already matches directly via book.spine.get(target)
  if (target) {
    const directMatch = book.spine.get(target);
    if (directMatch) {
      return target;
    }
  }

  // Parse path and hash
  let rawPath = (target || "").trim();
  let hash = "";
  if (rawPath.includes("#")) {
    const parts = rawPath.split("#");
    rawPath = parts[0];
    hash = parts.slice(1).join("#");
  }

  // Clean rawPath
  let cleanPath = rawPath.replace(/^\.?\//, "").replace(/^(\.\.\/)+/, "");
  try {
    cleanPath = decodeURIComponent(cleanPath);
  } catch (e) {}

  const fileName = cleanPath.split("/").pop();
  const fileStem = fileName ? fileName.replace(/\.[^/.]+$/, "") : "";

  // 1. Try finding section by exact relative suffix or path
  let section = book.spine.spineItems.find(s => {
    if (!s || !s.href) return false;
    const sHref = decodeURIComponent(s.href);
    return sHref === cleanPath ||
           sHref.endsWith("/" + cleanPath) ||
           cleanPath.endsWith("/" + sHref);
  });

  // 2. Try matching by filename (e.g. "chapter001.xhtml" -> "Text/chapter001.xhtml")
  if (!section && fileName) {
    section = book.spine.spineItems.find(s => {
      if (!s || !s.href) return false;
      const sFileName = decodeURIComponent(s.href).split("/").pop();
      return sFileName === fileName;
    });
  }

  // 3. Try matching by itemId or idref
  if (!section && itemId) {
    const cleanId = String(itemId).replace(/^toc-/, "");
    section = book.spine.spineItems.find(s => {
      if (!s) return false;
      return s.idref === itemId ||
             s.idref === cleanId ||
             (s.idref && cleanId.includes(s.idref));
    });
  }

  // 4. Try matching by fileStem to idref
  if (!section && fileStem) {
    section = book.spine.spineItems.find(s => s && s.idref === fileStem);
  }

  if (section) {
    if (hash) {
      return `${section.href}#${hash}`;
    }
    return section.href;
  }

  return target;
}

function navigateToTarget(target, itemId = null) {
  if (!rendition) return;

  closeSidebars();
  hideHeader();

  const resolved = resolveSpineTarget(target, itemId);
  console.log("Navigating to TOC target:", { original: target, itemId, resolved });

  rendition.display(resolved).catch(err => {
    console.warn("Display failed for resolved target:", resolved, err);
    // If navigation with anchor failed, try navigating to the section without anchor
    if (resolved && resolved.includes("#")) {
      const baseTarget = resolved.split("#")[0];
      console.log("Retrying navigation without anchor:", baseTarget);
      return rendition.display(baseTarget);
    }
    // Fallback: try displaying original target
    if (resolved !== target && target) {
      return rendition.display(target);
    }
  }).catch(finalErr => {
    console.error("Failed all navigation attempts for target:", target, finalErr);
  });
}

function renderTOC(items) {
  const container = document.getElementById("toc-list");
  if (!container) return;
  container.innerHTML = "";

  if (!items || items.length === 0) {
    container.innerHTML = `<div style="padding: 1rem; color: #94a3b8; font-size: 0.9rem;">No table of contents found.</div>`;
    return;
  }

  const renderItems = (tocItems, depth = 0) => {
    tocItems.forEach(item => {
      const link = document.createElement("a");
      link.className = "toc-item";
      link.style.paddingLeft = `${0.6 + depth * 0.75}rem`;
      link.innerText = (item.label || "").trim() || "Untitled Section";
      link.href = item.href || "#";
      link.addEventListener("click", (e) => {
        e.preventDefault();
        navigateToTarget(item.href, item.id);
        closeSidebars();
      });
      container.appendChild(link);

      if (item.subitems && item.subitems.length > 0) {
        renderItems(item.subitems, depth + 1);
      }
    });
  };

  renderItems(items);
}

function buildFlattenedToc(tocItems) {
  const result = [];
  function traverse(items) {
    if (!items) return;
    for (const item of items) {
      if (item.href) {
        let spineIndex = -1;
        if (book && book.spine && book.spine.spineItems) {
          const target = resolveSpineTarget(item.href, item.id);
          const spineItem = book.spine.get(target);
          if (spineItem) {
            spineIndex = spineItem.index;
          } else {
            const matched = book.spine.spineItems.find(s => s && (s.href === target || s.canonical === target));
            if (matched) spineIndex = matched.index;
          }
        }
        result.push({
          label: (item.label || "").trim(),
          href: item.href,
          id: item.id,
          spineIndex: spineIndex
        });
      }
      if (item.subitems && item.subitems.length > 0) {
        traverse(item.subitems);
      }
    }
  }
  traverse(tocItems);
  result.sort((a, b) => (a.spineIndex >= 0 && b.spineIndex >= 0) ? a.spineIndex - b.spineIndex : 0);
  flattenedToc = result;
  return result;
}

function findTocItemForHref(tocList, href) {
  if (!tocList || !href) return null;
  const cleanHref = href.replace(/^\.?\//, "").replace(/^(\.\.\/)+/, "");
  const fileName = cleanHref.split("/").pop().split("#")[0];
  const fileStem = fileName ? fileName.replace(/\.[^/.]+$/, "") : "";

  for (const item of tocList) {
    if (item.href) {
      const itemClean = item.href.replace(/^\.?\//, "").replace(/^(\.\.\/)+/, "");
      const itemFileName = itemClean.split("/").pop().split("#")[0];
      const itemFileStem = itemFileName ? itemFileName.replace(/\.[^/.]+$/, "") : "";

      if (itemClean === cleanHref ||
          cleanHref.endsWith("/" + itemClean) ||
          itemClean.endsWith("/" + cleanHref) ||
          itemFileName === fileName ||
          (item.id && (item.id === fileStem || item.id === `toc-${fileStem}`))) {
        return item;
      }
    }
    if (item.subitems && item.subitems.length > 0) {
      const subMatch = findTocItemForHref(item.subitems, href);
      if (subMatch) return subMatch;
    }
  }
  return null;
}

function getChapterTitleForLocation(location) {
  if (!location || !location.start) return lastKnownChapterTitle || "";
  if (!book || !book.navigation || !book.navigation.toc) return lastKnownChapterTitle || "";

  if (!flattenedToc || flattenedToc.length === 0) {
    buildFlattenedToc(book.navigation.toc);
  }

  // 1. Determine current spine index
  let currentIndex = location.start.index;
  if (currentIndex == null && location.start.href && book.spine) {
    const target = resolveSpineTarget(location.start.href);
    const sp = book.spine.get(target);
    if (sp) currentIndex = sp.index;
  }

  // 2. Find latest TOC item with spineIndex <= currentIndex
  if (currentIndex != null && currentIndex >= 0 && flattenedToc.length > 0) {
    let bestMatch = null;
    for (const item of flattenedToc) {
      if (item.spineIndex >= 0 && item.spineIndex <= currentIndex) {
        bestMatch = item;
      }
    }
    if (bestMatch && bestMatch.label) {
      lastKnownChapterTitle = bestMatch.label;
      return bestMatch.label;
    }
  }

  // 3. Direct href search fallback
  const directMatch = findTocItemForHref(book.navigation.toc, location.start.href);
  if (directMatch && directMatch.label) {
    lastKnownChapterTitle = directMatch.label.trim();
    return lastKnownChapterTitle;
  }

  return lastKnownChapterTitle || "";
}

function updateProgressUI(location) {
  if (!location || !location.start) return;

  // 1. Left: Current page / total pages in chapter
  const pageInfo = document.getElementById("page-info");
  if (pageInfo) {
    if (location.start.displayed && location.start.displayed.page) {
      const curPage = location.start.displayed.page;
      const totalPages = location.start.displayed.total || curPage;
      pageInfo.innerText = `${curPage}/${totalPages}`;
    } else {
      pageInfo.innerText = "1/1";
    }
  }

  // 2. Middle: Chapter title (persists throughout all pages of the chapter)
  const chapterTitle = document.getElementById("chapter-title");
  if (chapterTitle) {
    const title = getChapterTitleForLocation(location);
    chapterTitle.innerText = title;
  }

  // 3. Right: Total book percentage
  const percent = getPercentage(location);
  const progressText = document.getElementById("progress-text");
  if (progressText) {
    progressText.innerText = `${percent}%`;
  }
}

function syncProgressDebounced(location) {
  clearTimeout(progressDebounceTimer);
  progressDebounceTimer = setTimeout(async () => {
    if (!location || !location.start || !location.start.cfi) return;
    const cfi = location.start.cfi;
    const bookId = getEpubBookId();
    if (!bookId) return;

    localStorage.setItem("aarkib-progress-" + bookId, cfi);

    const percent = getPercentage(location);

    try {
      await fetch(`/api/media/${bookId}/progress`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          location: cfi,
          percentage: percent,
          is_completed: percent >= 99.0
        })
      });
    } catch (err) {
      console.warn("Failed to sync reading progress", err);
    }
  }, 500);
}

window.addEventListener("pagehide", () => {
  if (rendition && rendition.currentLocation()) {
    const loc = rendition.currentLocation();
    if (loc && loc.start && loc.start.cfi) {
      const bookId = getEpubBookId();
      if (bookId) {
        localStorage.setItem("aarkib-progress-" + bookId, loc.start.cfi);
        const percent = getPercentage(loc);
        const data = JSON.stringify({
          location: loc.start.cfi,
          percentage: percent,
          is_completed: percent >= 99.0
        });
        if (navigator.sendBeacon) {
          navigator.sendBeacon(`/api/media/${bookId}/progress`, new Blob([data], { type: "application/json" }));
        }
      }
    }
  }
});

function setReaderTheme(theme) {
  currentTheme = theme;
  document.body.setAttribute("data-reader-theme", theme);
  localStorage.setItem("aarkib-reader-theme", theme);
  if (rendition) {
    rendition.themes.select(theme);
  }
}

function changeFontSize(delta) {
  currentFontSize = Math.max(70, Math.min(200, currentFontSize + delta));
  const fontLabel = document.getElementById("font-size-label");
  if (fontLabel) fontLabel.innerText = `${currentFontSize}%`;
  if (rendition) {
    rendition.themes.fontSize(`${currentFontSize}%`);
  }
}

function setColumns(cols) {
  currentSpread = cols;
  localStorage.setItem("aarkib-reader-spread", cols);
  updateSettingsUI();

  if (rendition) {
    const spreadOpts = getSpreadOptions(cols);
    rendition.spread(spreadOpts.spread, spreadOpts.minSpreadWidth);
  }
}

function setFlow(flow) {
  currentFlow = flow;
  localStorage.setItem("aarkib-reader-flow", flow);
  updateSettingsUI();

  if (rendition) {
    rendition.flow(flow);
    if (flow === "paginated") {
      const spreadOpts = getSpreadOptions(currentSpread);
      rendition.spread(spreadOpts.spread, spreadOpts.minSpreadWidth);
    }
  }
}

function updateSettingsUI() {
  const fontLabel = document.getElementById("font-size-label");
  if (fontLabel) fontLabel.innerText = `${currentFontSize}%`;

  const btn1 = document.getElementById("col-1");
  const btn2 = document.getElementById("col-2");
  const btnAuto = document.getElementById("col-auto");
  if (btn1) btn1.className = currentSpread === "1" ? "btn btn-primary" : "btn btn-secondary";
  if (btn2) btn2.className = currentSpread === "2" ? "btn btn-primary" : "btn btn-secondary";
  if (btnAuto) btnAuto.className = currentSpread === "auto" ? "btn btn-primary" : "btn btn-secondary";

  const btnPaginated = document.getElementById("flow-paginated");
  const btnScrolled = document.getElementById("flow-scrolled");
  if (btnPaginated) btnPaginated.className = currentFlow === "paginated" ? "btn btn-primary" : "btn btn-secondary";
  if (btnScrolled) btnScrolled.className = currentFlow === "scrolled" ? "btn btn-primary" : "btn btn-secondary";
}

async function addCurrentBookmark() {
  if (!rendition) return;
  const location = rendition.currentLocation();
  if (!location || !location.start) return;

  const bookId = getEpubBookId();
  if (!bookId) return;

  const progressText = document.getElementById("progress-text") ? document.getElementById("progress-text").innerText : "";
  try {
    const res = await fetch(`/api/media/${bookId}/bookmarks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        location: location.start.cfi,
        title: `Bookmark at ${progressText}`
      })
    });
    if (res.ok) {
      showToast("Bookmark saved!", "success");
    }
  } catch (err) {
    console.error("Failed to save bookmark", err);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initReader);
} else {
  initReader();
}
