// --- Service Worker Registration ---
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").then((reg) => {
      console.log("Aarkib ServiceWorker registered:", reg.scope);
    }).catch((err) => {
      console.warn("Aarkib ServiceWorker registration failed:", err);
    });
  });
}

// --- Theme Switcher ---
function initTheme() {
  const savedTheme = localStorage.getItem("aarkib-theme") || "dark";
  document.documentElement.setAttribute("data-theme", savedTheme);
}

function toggleTheme() {
  const currentTheme = document.documentElement.getAttribute("data-theme") || "dark";
  const themes = ["dark", "light", "oled"];
  const nextTheme = themes[(themes.indexOf(currentTheme) + 1) % themes.length];
  document.documentElement.setAttribute("data-theme", nextTheme);
  localStorage.setItem("aarkib-theme", nextTheme);
}

// --- User Menu Dropdown ---
function toggleUserMenu(e) {
  if (e) {
    e.preventDefault();
    e.stopPropagation();
  }
  const dropdown = document.getElementById("user-dropdown-menu");
  const menuBtn = document.getElementById("user-menu-btn");
  if (!dropdown) return;

  const isHidden = dropdown.style.display === "none" || !dropdown.classList.contains("show");
  if (isHidden) {
    dropdown.style.display = "flex";
    dropdown.classList.add("show");
    if (menuBtn) menuBtn.setAttribute("aria-expanded", "true");
  } else {
    dropdown.style.display = "none";
    dropdown.classList.remove("show");
    if (menuBtn) menuBtn.setAttribute("aria-expanded", "false");
  }
}

function closeUserMenu() {
  const dropdown = document.getElementById("user-dropdown-menu");
  const menuBtn = document.getElementById("user-menu-btn");
  if (dropdown) {
    dropdown.style.display = "none";
    dropdown.classList.remove("show");
  }
  if (menuBtn) {
    menuBtn.setAttribute("aria-expanded", "false");
  }
}

function initUserMenu() {
  document.addEventListener("click", (e) => {
    const dropdown = document.getElementById("user-dropdown-menu");
    const menuBtn = document.getElementById("user-menu-btn");
    if (dropdown && !dropdown.contains(e.target) && (!menuBtn || !menuBtn.contains(e.target))) {
      closeUserMenu();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeUserMenu();
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initUserMenu();
  initGlobalKeyboardShortcuts();

  const themeBtn = document.getElementById("theme-toggle-btn");
  if (themeBtn) {
    themeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleTheme();
    });
  }
});

// --- Global Keyboard Shortcuts (⌘K search, Escape dismiss) ---
function initGlobalKeyboardShortcuts() {
  document.addEventListener("keydown", (e) => {
    // ⌘K or Ctrl+K: Focus global search
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      const searchInput = document.getElementById("search-input");
      if (searchInput) {
        searchInput.focus();
        searchInput.select();
      }
    }
  });
}

// Toast notification helper
function showToast(message, type = "info") {
  const toast = document.createElement("div");
  toast.className = `alert alert-${type}`;
  toast.style.position = "fixed";
  toast.style.bottom = "20px";
  toast.style.right = "20px";
  toast.style.zIndex = "9999";
  toast.style.boxShadow = "var(--shadow-lg)";
  toast.innerText = message;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, 3500);
}
