/**
 * Aarkib Gamepad & Controller Navigation Engine
 * Standard W3C Gamepad API implementation with spatial navigation,
 * context-sensitive media controls, haptics, and Cinematic Obsidian HUD prompts.
 */

(function () {
  "use strict";

  // Standard W3C Gamepad Mapping Indices
  const BTN = {
    A: 0,          // South (Xbox A, PS Cross, Switch B)
    B: 1,          // East  (Xbox B, PS Circle, Switch A)
    X: 2,          // West  (Xbox X, PS Square, Switch Y)
    Y: 3,          // North (Xbox Y, PS Triangle, Switch X)
    LB: 4,         // Left Bumper
    RB: 5,         // Right Bumper
    LT: 6,         // Left Trigger
    RT: 7,         // Right Trigger
    SELECT: 8,     // Back / Select / Share / View
    START: 9,      // Start / Menu / Options
    L3: 10,        // Left Stick Click
    R3: 11,        // Right Stick Click
    DPAD_UP: 12,
    DPAD_DOWN: 13,
    DPAD_LEFT: 14,
    DPAD_RIGHT: 15,
    GUIDE: 16      // Xbox / PS / Home button
  };

  const AXIS = {
    LX: 0, // Left Stick Horizontal
    LY: 1, // Left Stick Vertical
    RX: 2, // Right Stick Horizontal
    RY: 3  // Right Stick Vertical
  };

  const STICK_DEADZONE = 0.38;
  const TRIGGER_THRESHOLD = 0.5;
  const REPEAT_INITIAL_DELAY_MS = 320;
  const REPEAT_INTERVAL_MS = 110;

  class GamepadManager {
    constructor() {
      this.connectedGamepads = new Map();
      this.activeGamepadIndex = null;
      this.currentContext = "ui";
      this.contextHandlers = new Map();
      this.buttonStates = new Map(); // padIndex -> Map(btnIndex -> { pressed: bool, time: num, repeatTime: num })
      this.axisStates = new Map();   // padIndex -> { lx, ly, rx, ry }
      this.isGamepadActive = false;
      this.hudElement = null;
      this.hudTimeout = null;
      this.settings = {
        hud: localStorage.getItem("aarkib-gamepad-hud") || "auto", // 'auto', 'always', 'never'
        rumble: localStorage.getItem("aarkib-gamepad-rumble") !== "false"
      };

      this.init();
    }

    init() {
      window.addEventListener("gamepadconnected", (e) => this.onGamepadConnected(e));
      window.addEventListener("gamepaddisconnected", (e) => this.onGamepadDisconnected(e));

      // Reset gamepad focus on mouse movement or keyboard typing
      window.addEventListener("mousemove", () => this.onUserNonGamepadInput(), { passive: true });
      window.addEventListener("keydown", (e) => {
        if (!e.repeat) this.onUserNonGamepadInput();
      }, { passive: true });

      // Build DOM HUD element once page is ready
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => this.setupHUD());
      } else {
        this.setupHUD();
      }

      // Register default UI context
      this.registerDefaultUIContext();

      // Start Polling Loop
      requestAnimationFrame((ts) => this.pollLoop(ts));
    }

    onGamepadConnected(event) {
      const gp = event.gamepad;
      console.log(`🎮 Gamepad connected at index ${gp.index}: ${gp.id} (${gp.buttons.length} buttons, ${gp.axes.length} axes)`);
      this.connectedGamepads.set(gp.index, gp);
      this.buttonStates.set(gp.index, new Map());
      this.axisStates.set(gp.index, { lx: 0, ly: 0, rx: 0, ry: 0 });

      if (this.activeGamepadIndex === null) {
        this.activeGamepadIndex = gp.index;
      }

      this.activateGamepadMode();
      this.rumble(gp.index, 100, 0.4, 0.2);

      const brand = this.detectControllerBrand(gp.id);
      this.showToast(`🎮 ${brand} connected`, "info");
      this.updateHUD();
    }

    onGamepadDisconnected(event) {
      const gp = event.gamepad;
      console.log(`🎮 Gamepad disconnected from index ${gp.index}`);
      this.connectedGamepads.delete(gp.index);
      this.buttonStates.delete(gp.index);
      this.axisStates.delete(gp.index);

      if (this.activeGamepadIndex === gp.index) {
        const remaining = Array.from(this.connectedGamepads.keys());
        this.activeGamepadIndex = remaining.length > 0 ? remaining[0] : null;
      }

      if (this.connectedGamepads.size === 0) {
        this.deactivateGamepadMode();
      } else {
        this.updateHUD();
      }
    }

    detectControllerBrand(id) {
      const lower = (id || "").toLowerCase();
      if (lower.includes("xbox") || lower.includes("x-box") || lower.includes("microsoft")) {
        return "Xbox Controller";
      }
      if (lower.includes("dualshock") || lower.includes("dualsense") || lower.includes("sony") || lower.includes("playstation")) {
        return "PlayStation Controller";
      }
      if (lower.includes("switch") || lower.includes("nintendo") || lower.includes("pro controller") || lower.includes("joy-con")) {
        return "Nintendo Switch Controller";
      }
      if (lower.includes("steam deck") || lower.includes("valve")) {
        return "Steam Deck Controller";
      }
      return "Gamepad";
    }

    activateGamepadMode() {
      if (!this.isGamepadActive) {
        this.isGamepadActive = true;
        document.body.classList.add("gamepad-active");
      }
      this.wakeHUD();
    }

    deactivateGamepadMode() {
      this.isGamepadActive = false;
      document.body.classList.remove("gamepad-active");
      if (this.hudElement) {
        this.hudElement.classList.add("hud-hidden");
      }
    }

    onUserNonGamepadInput() {
      // Gentle fade out of HUD on mouse interaction without abruptly destroying focus state
      if (this.hudElement && this.settings.hud === "auto") {
        this.hudElement.classList.add("hud-hidden");
      }
    }

    showToast(message, type = "info") {
      if (typeof window.showToast === "function") {
        window.showToast(message, type);
      }
    }

    rumble(padIndex, durationMs = 120, weak = 0.4, strong = 0.2) {
      if (!this.settings.rumble) return;
      const gp = navigator.getGamepads ? navigator.getGamepads()[padIndex] : null;
      if (gp && gp.vibrationActuator && typeof gp.vibrationActuator.playEffect === "function") {
        gp.vibrationActuator.playEffect("dual-rumble", {
          startDelay: 0,
          duration: durationMs,
          weakMagnitude: weak,
          strongMagnitude: strong
        }).catch(() => {});
      }
    }

    // --- Context Management ---
    setContext(name, handlers = {}) {
      this.currentContext = name;
      this.contextHandlers.set(name, handlers);
      this.updateHUD();
      console.debug(`🎮 Gamepad context changed to: ${name}`);
    }

    getContext() {
      return this.currentContext;
    }

    getHandler(name = this.currentContext) {
      return this.contextHandlers.get(name) || {};
    }

    // --- Button & Axis State Polling ---
    pollLoop(timestamp) {
      const gamepads = navigator.getGamepads ? navigator.getGamepads() : [];
      let anyInputDetected = false;

      for (let i = 0; i < gamepads.length; i++) {
        const gp = gamepads[i];
        if (!gp || !gp.connected) continue;

        if (!this.connectedGamepads.has(gp.index)) {
          this.onGamepadConnected({ gamepad: gp });
        }

        if (this.processGamepadInput(gp, timestamp)) {
          anyInputDetected = true;
          this.activeGamepadIndex = gp.index;
          this.activateGamepadMode();
        }
      }

      requestAnimationFrame((ts) => this.pollLoop(ts));
    }

    processGamepadInput(gp, timestamp) {
      let active = false;
      const padIndex = gp.index;
      let padButtons = this.buttonStates.get(padIndex);
      if (!padButtons) {
        padButtons = new Map();
        this.buttonStates.set(padIndex, padButtons);
      }

      // 1. Process Buttons (0 to 16)
      const numButtons = Math.min(gp.buttons.length, 17);
      for (let btnIdx = 0; btnIdx < numButtons; btnIdx++) {
        const btnObj = gp.buttons[btnIdx];
        const isPressed = btnObj ? (btnObj.pressed || btnObj.value > 0.5) : false;
        let btnState = padButtons.get(btnIdx);

        if (!btnState) {
          btnState = { pressed: false, time: 0, nextRepeat: 0 };
          padButtons.set(btnIdx, btnState);
        }

        if (isPressed) {
          active = true;
          if (!btnState.pressed) {
            // Just Pressed (Edge Trigger)
            btnState.pressed = true;
            btnState.time = timestamp;
            btnState.nextRepeat = timestamp + REPEAT_INITIAL_DELAY_MS;
            this.handleButtonPress(btnIdx, false);
          } else if (timestamp >= btnState.nextRepeat) {
            // Held Button Repeat (D-Pad and Bumpers only for navigation)
            if (this.isRepeatableButton(btnIdx)) {
              btnState.nextRepeat = timestamp + REPEAT_INTERVAL_MS;
              this.handleButtonPress(btnIdx, true);
            }
          }
        } else {
          if (btnState.pressed) {
            // Just Released
            btnState.pressed = false;
            this.handleButtonRelease(btnIdx);
          }
        }
      }

      // 2. Process Analog Sticks
      const axes = gp.axes || [];
      const lx = (Math.abs(axes[AXIS.LX] || 0) > STICK_DEADZONE) ? axes[AXIS.LX] : 0;
      const ly = (Math.abs(axes[AXIS.LY] || 0) > STICK_DEADZONE) ? axes[AXIS.LY] : 0;
      const rx = (Math.abs(axes[AXIS.RX] || 0) > STICK_DEADZONE) ? axes[AXIS.RX] : 0;
      const ry = (Math.abs(axes[AXIS.RY] || 0) > STICK_DEADZONE) ? axes[AXIS.RY] : 0;

      let prevAxes = this.axisStates.get(padIndex) || { lx: 0, ly: 0, rx: 0, ry: 0 };

      // Left stick directional discrete navigation (like D-Pad)
      if (Math.abs(lx) > STICK_DEADZONE || Math.abs(ly) > STICK_DEADZONE) {
        active = true;
        this.processAnalogStickDirection(lx, ly, prevAxes.lx, prevAxes.ly, timestamp);
      }

      // Continuous analog handler (for analog webtoon scroll or video scrub)
      if (lx !== 0 || ly !== 0 || rx !== 0 || ry !== 0) {
        active = true;
        const handler = this.getHandler();
        if (typeof handler.onAnalogStick === "function") {
          handler.onAnalogStick({ lx, ly, rx, ry });
        }
      }

      this.axisStates.set(padIndex, { lx, ly, rx, ry });
      return active;
    }

    isRepeatableButton(btnIdx) {
      return (
        btnIdx === BTN.DPAD_UP ||
        btnIdx === BTN.DPAD_DOWN ||
        btnIdx === BTN.DPAD_LEFT ||
        btnIdx === BTN.DPAD_RIGHT ||
        btnIdx === BTN.LB ||
        btnIdx === BTN.RB
      );
    }

    processAnalogStickDirection(lx, ly, prevLx, prevLy, timestamp) {
      // Map stick deflection to cardinal directions
      let dir = null;
      if (Math.abs(lx) > Math.abs(ly)) {
        dir = lx > 0 ? "right" : "left";
      } else {
        dir = ly > 0 ? "down" : "up";
      }

      // Edge transition check: only trigger when moving out of deadzone
      const wasNeutral = Math.abs(prevLx) <= STICK_DEADZONE && Math.abs(prevLy) <= STICK_DEADZONE;
      if (wasNeutral && dir) {
        this.dispatchNavigation(dir);
      }
    }

    handleButtonPress(btnIdx, isRepeat) {
      this.wakeHUD();
      const handler = this.getHandler();

      // Cardinal navigation dispatch
      if (btnIdx === BTN.DPAD_UP) return this.dispatchNavigation("up");
      if (btnIdx === BTN.DPAD_DOWN) return this.dispatchNavigation("down");
      if (btnIdx === BTN.DPAD_LEFT) return this.dispatchNavigation("left");
      if (btnIdx === BTN.DPAD_RIGHT) return this.dispatchNavigation("right");

      // Actions (non-repeating)
      if (isRepeat) return;

      switch (btnIdx) {
        case BTN.A:
          if (handler.onSelect && handler.onSelect()) return;
          this.defaultSelectAction();
          break;

        case BTN.B:
          if (handler.onBack && handler.onBack()) return;
          this.defaultBackAction();
          break;

        case BTN.X:
          if (handler.onActionX && handler.onActionX()) return;
          this.defaultActionX();
          break;

        case BTN.Y:
          if (handler.onActionY && handler.onActionY()) return;
          this.defaultActionY();
          break;

        case BTN.LB:
          if (handler.onBumperLeft && handler.onBumperLeft()) return;
          this.defaultBumperLeft();
          break;

        case BTN.RB:
          if (handler.onBumperRight && handler.onBumperRight()) return;
          this.defaultBumperRight();
          break;

        case BTN.LT:
          if (handler.onTriggerLeft && handler.onTriggerLeft()) return;
          this.defaultTriggerLeft();
          break;

        case BTN.RT:
          if (handler.onTriggerRight && handler.onTriggerRight()) return;
          this.defaultTriggerRight();
          break;

        case BTN.START:
          if (handler.onStart && handler.onStart()) return;
          this.defaultStartAction();
          break;

        case BTN.SELECT:
          if (handler.onSelectButton && handler.onSelectButton()) return;
          this.toggleHUDVisibility();
          break;

        case BTN.GUIDE:
          if (handler.onGuide && handler.onGuide()) return;
          window.location.href = "/";
          break;
      }
    }

    handleButtonRelease(btnIdx) {
      const handler = this.getHandler();
      if (typeof handler.onButtonRelease === "function") {
        handler.onButtonRelease(btnIdx);
      }
    }

    // --- Spatial Navigation Engine ---
    dispatchNavigation(direction) {
      const handler = this.getHandler();
      if (typeof handler.onNavigate === "function") {
        const consumed = handler.onNavigate(direction);
        if (consumed) return;
      }
      this.spatialNavigate(direction);
    }

    getFocusableElements() {
      // Find all visible, interactable DOM candidates
      const selector = [
        ".book-card",
        "a[href]:not([tabindex='-1'])",
        "button:not([disabled]):not([tabindex='-1'])",
        "input:not([disabled]):not([type='hidden'])",
        "select:not([disabled])",
        "textarea:not([disabled])",
        "[tabindex='0']"
      ].join(", ");

      const raw = Array.from(document.querySelectorAll(selector));
      return raw.filter((el) => {
        // Exclude hidden elements
        if (el.offsetParent === null && el.tagName.toLowerCase() !== "body") return false;
        const style = window.getComputedStyle(el);
        if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 4 && rect.height > 4;
      });
    }

    spatialNavigate(direction) {
      const candidates = this.getFocusableElements();
      if (candidates.length === 0) return;

      const current = document.activeElement;
      if (!current || current === document.body || !candidates.includes(current)) {
        // No valid active focus: select first sensible card or nav item
        const firstCard = document.querySelector(".book-card") || candidates[0];
        if (firstCard) {
          firstCard.focus();
          firstCard.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
          this.rumble(this.activeGamepadIndex, 40, 0.2, 0.1);
        }
        return;
      }

      const cRect = current.getBoundingClientRect();
      const cCenter = {
        x: cRect.left + cRect.width / 2,
        y: cRect.top + cRect.height / 2
      };

      let bestCandidate = null;
      let minScore = Infinity;

      for (let i = 0; i < candidates.length; i++) {
        const cand = candidates[i];
        if (cand === current) continue;

        const tRect = cand.getBoundingClientRect();
        const tCenter = {
          x: tRect.left + tRect.width / 2,
          y: tRect.top + tRect.height / 2
        };

        const dx = tCenter.x - cCenter.x;
        const dy = tCenter.y - cCenter.y;

        let primaryDist = 0;
        let secondaryDist = 0;
        let isInDirection = false;

        switch (direction) {
          case "up":
            isInDirection = tRect.bottom <= cRect.top + 8;
            primaryDist = cCenter.y - tCenter.y;
            secondaryDist = Math.abs(dx);
            break;
          case "down":
            isInDirection = tRect.top >= cRect.bottom - 8;
            primaryDist = tCenter.y - cCenter.y;
            secondaryDist = Math.abs(dx);
            break;
          case "left":
            isInDirection = tRect.right <= cRect.left + 8;
            primaryDist = cCenter.x - tCenter.x;
            secondaryDist = Math.abs(dy);
            break;
          case "right":
            isInDirection = tRect.left >= cRect.right - 8;
            primaryDist = tCenter.x - cCenter.x;
            secondaryDist = Math.abs(dy);
            break;
        }

        if (!isInDirection || primaryDist <= 0) continue;

        // Weight perpendicular distance heavily (2.2x) to favor direct alignment in the row/column
        const score = primaryDist + (secondaryDist * 2.2);

        if (score < minScore) {
          minScore = score;
          bestCandidate = cand;
        }
      }

      if (bestCandidate) {
        bestCandidate.focus();
        bestCandidate.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
        this.rumble(this.activeGamepadIndex, 30, 0.15, 0.05);
      } else {
        // Row or edge wrap fallback if navigating horizontally inside a shelf
        const shelf = current.closest(".shelf-row");
        if (shelf && (direction === "left" || direction === "right")) {
          shelf.scrollBy({
            left: direction === "right" ? 300 : -300,
            behavior: "smooth"
          });
        }
      }
    }

    // --- Default Actions (UI Context) ---
    registerDefaultUIContext() {
      this.contextHandlers.set("ui", {
        getPrompts: () => [
          { key: "A", label: "Select" },
          { key: "B", label: "Back" },
          { key: "X", label: "View" },
          { key: "Y", label: "Search" },
          { key: "LB/RB", label: "Tabs" },
          { key: "Start", label: "Menu" }
        ]
      });
    }

    defaultSelectAction() {
      const cur = document.activeElement;
      if (cur && cur !== document.body) {
        this.rumble(this.activeGamepadIndex, 60, 0.3, 0.1);
        if (typeof cur.click === "function") {
          cur.click();
        }
      } else {
        // Focus first card or button
        const card = document.querySelector(".book-card, .btn");
        if (card) card.focus();
      }
    }

    defaultBackAction() {
      // 1. Close open dropdowns or modals
      const userDropdown = document.getElementById("user-dropdown-menu");
      if (userDropdown && userDropdown.classList.contains("show")) {
        if (typeof window.closeUserMenu === "function") window.closeUserMenu();
        return;
      }

      // 2. Unfocus input elements
      const cur = document.activeElement;
      if (cur && ["input", "select", "textarea"].includes(cur.tagName.toLowerCase())) {
        cur.blur();
        return;
      }

      // 3. Fallback: navigate back if history allows
      if (window.history.length > 1 && window.location.pathname !== "/") {
        window.history.back();
      }
    }

    defaultActionX() {
      // Toggle view mode on library page (Shelves <-> Grid)
      const shelvesBtn = document.getElementById("view-shelves-btn");
      const gridBtn = document.getElementById("view-grid-btn");
      if (shelvesBtn && gridBtn) {
        if (shelvesBtn.classList.contains("active")) {
          gridBtn.click();
        } else {
          shelvesBtn.click();
        }
        this.rumble(this.activeGamepadIndex, 50, 0.25, 0.1);
      }
    }

    defaultActionY() {
      // Quick jump to global search input
      const searchInput = document.getElementById("search-input");
      if (searchInput) {
        searchInput.focus();
        searchInput.select();
        this.rumble(this.activeGamepadIndex, 40, 0.2, 0.05);
      }
    }

    defaultBumperLeft() {
      // Switch to previous main navigation tab
      this.cycleSubnav(-1);
    }

    defaultBumperRight() {
      // Switch to next main navigation tab
      this.cycleSubnav(1);
    }

    cycleSubnav(direction) {
      const tabs = Array.from(document.querySelectorAll(".subnav-link"));
      if (tabs.length === 0) return;
      const activeIdx = tabs.findIndex(t => t.classList.contains("active"));
      if (activeIdx !== -1) {
        const nextIdx = (activeIdx + direction + tabs.length) % tabs.length;
        this.rumble(this.activeGamepadIndex, 50, 0.2, 0.1);
        tabs[nextIdx].click();
      }
    }

    defaultTriggerLeft() {
      // Page Up scroll
      window.scrollBy({ top: -window.innerHeight * 0.75, behavior: "smooth" });
    }

    defaultTriggerRight() {
      // Page Down scroll
      window.scrollBy({ top: window.innerHeight * 0.75, behavior: "smooth" });
    }

    defaultStartAction() {
      // Open User dropdown menu
      const userBtn = document.getElementById("user-menu-btn");
      if (userBtn) {
        userBtn.click();
        this.rumble(this.activeGamepadIndex, 50, 0.2, 0.1);
      }
    }

    // --- Gamepad HUD Legend ---
    setupHUD() {
      if (document.getElementById("gamepad-hud")) return;

      const hud = document.createElement("div");
      hud.id = "gamepad-hud";
      hud.className = "gamepad-hud hud-hidden";
      hud.setAttribute("aria-hidden", "true");
      document.body.appendChild(hud);
      this.hudElement = hud;

      this.updateHUD();
    }

    updateHUD() {
      if (!this.hudElement) return;

      if (this.settings.hud === "never" || this.connectedGamepads.size === 0) {
        this.hudElement.classList.add("hud-hidden");
        return;
      }

      const handler = this.getHandler();
      let prompts = [];
      if (typeof handler.getPrompts === "function") {
        prompts = handler.getPrompts();
      } else {
        prompts = (this.contextHandlers.get("ui") || {}).getPrompts ? this.contextHandlers.get("ui").getPrompts() : [];
      }

      if (!prompts || prompts.length === 0) {
        this.hudElement.innerHTML = "";
        return;
      }

      this.hudElement.innerHTML = prompts.map(p => `
        <div class="gamepad-prompt">
          <span class="gamepad-keycap">${p.key}</span>
          <span>${p.label}</span>
        </div>
      `).join("");
    }

    wakeHUD() {
      if (!this.hudElement || this.settings.hud === "never") return;
      this.hudElement.classList.remove("hud-hidden");

      clearTimeout(this.hudTimeout);
      if (this.settings.hud === "auto") {
        this.hudTimeout = setTimeout(() => {
          if (this.hudElement) {
            this.hudElement.classList.add("hud-hidden");
          }
        }, 4000);
      }
    }

    toggleHUDVisibility() {
      if (!this.hudElement) return;
      if (this.hudElement.classList.contains("hud-hidden")) {
        this.wakeHUD();
      } else {
        this.hudElement.classList.add("hud-hidden");
      }
    }
  }

  // Export canonical singleton instance to global window
  window.AarkibGamepad = new GamepadManager();
})();
