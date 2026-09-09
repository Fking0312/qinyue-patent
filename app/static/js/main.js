console.log("Patent system frontend initialized.");

/** 全局 Toast / URL 提示参数 / Bootstrap Tooltip（供 SPA 与各业务模块共用） */
(() => {
  const escapeHtml = (s) =>
    String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const toastHost = () => document.getElementById("qyToastContainer");

  window.qyShowToast = (message, variant = "success") => {
    const host = toastHost();
    if (!host || typeof bootstrap === "undefined" || !bootstrap.Toast) {
      return;
    }
    const id = `qyToast-${Date.now()}`;
    let bg = "text-bg-secondary";
    let closeClass = "btn-close btn-close-white me-2 m-auto";
    if (variant === "danger") {
      bg = "text-bg-danger";
    } else if (variant === "success") {
      bg = "text-bg-success";
    } else if (variant === "warning") {
      bg = "text-bg-warning";
      closeClass = "btn-close me-2 m-auto";
    } else if (variant === "info") {
      bg = "text-bg-info";
      closeClass = "btn-close me-2 m-auto";
    } else if (variant === "secondary") {
      bg = "text-bg-secondary";
    }
    const safe = escapeHtml(String(message).slice(0, 400));
    host.insertAdjacentHTML(
      "beforeend",
      `<div id="${id}" class="toast ${bg} border-0 shadow" role="status" aria-live="polite" data-bs-delay="4000">
        <div class="d-flex">
          <div class="toast-body">${safe}</div>
          <button type="button" class="${closeClass}" data-bs-dismiss="toast" aria-label="关闭"></button>
        </div>
      </div>`,
    );
    const el = document.getElementById(id);
    if (el) {
      bootstrap.Toast.getOrCreateInstance(el).show();
      el.addEventListener("hidden.bs.toast", () => el.remove());
    }
  };

  window.qyConsumeUrlToast = () => {
    if (typeof window.qyShowToast !== "function") {
      return;
    }
    try {
      const u = new URL(window.location.href);
      const msg = u.searchParams.get("qy_toast");
      if (!msg) {
        return;
      }
      const rawVariant = (u.searchParams.get("qy_toast_variant") || "success").toLowerCase();
      const variant =
        rawVariant === "danger" || rawVariant === "error"
          ? "danger"
          : rawVariant === "warning"
            ? "warning"
            : rawVariant === "info"
              ? "info"
              : "success";
      window.qyShowToast(msg, variant);
      u.searchParams.delete("qy_toast");
      u.searchParams.delete("qy_toast_variant");
      const qs = u.searchParams.toString();
      const next = `${u.pathname}${qs ? `?${qs}` : ""}${u.hash}`;
      history.replaceState(history.state || {}, "", next);
    } catch {
      /* ignore */
    }
  };

  window.qyCsrfToken = () => {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  };

  window.qyDisposeBootstrapTooltips = (root) => {
    if (!root || typeof bootstrap === "undefined" || !bootstrap.Tooltip) {
      return;
    }
    root.querySelectorAll('[data-bs-toggle="tooltip"]').forEach((el) => {
      try {
        const t = bootstrap.Tooltip.getInstance(el);
        if (t) {
          t.dispose();
        }
      } catch {
        /* ignore */
      }
    });
  };

  window.qyInitBootstrapTooltips = (root) => {
    if (!root || typeof bootstrap === "undefined" || !bootstrap.Tooltip) {
      return;
    }
    root.querySelectorAll('[data-bs-toggle="tooltip"]').forEach((el) => {
      try {
        bootstrap.Tooltip.getOrCreateInstance(el, {
          trigger: "hover focus",
          boundary: "window",
          container: "body",
          delay: { show: 320, hide: 60 },
        });
      } catch {
        /* ignore */
      }
    });
  };

  const navigateClickableRow = (row) => {
    const href = row?.dataset?.href;
    if (!href) {
      return;
    }
    if (typeof window.qySpaNavigate === "function") {
      window.qySpaNavigate(href);
      return;
    }
    window.location.assign(href);
  };

  window.qyInitClickableRows = (root) => {
    if (!root) {
      return;
    }
    root.querySelectorAll("tr.qy-clickable-row[data-href]").forEach((row) => {
      if (row.dataset.qyClickableBound === "1") {
        return;
      }
      row.dataset.qyClickableBound = "1";
      row.addEventListener("click", (event) => {
        if (event.target.closest("a, button, input, select, textarea, label, [data-bs-toggle]")) {
          return;
        }
        navigateClickableRow(row);
      });
      row.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") {
          return;
        }
        if (event.target.closest("a, button, input, select, textarea")) {
          return;
        }
        event.preventDefault();
        navigateClickableRow(row);
      });
    });
  };
})();

const THEME_TOGGLE_FAB_DISMISSED_KEY = "qyThemeToggleFabDismissedPermanently";
const themeToggleFab = document.getElementById("themeToggleFab");
const themeToggleBtn = document.getElementById("themeToggle");
const themeIconLight = themeToggleBtn?.querySelector(".theme-icon-light");
const themeIconDark = themeToggleBtn?.querySelector(".theme-icon-dark");

const applyTheme = (theme) => {
  const isDark = theme === "dark";
  document.body.classList.toggle("dark-mode", isDark);
  if (themeToggleBtn && themeIconLight && themeIconDark) {
    themeIconLight.classList.toggle("d-none", isDark);
    themeIconDark.classList.toggle("d-none", !isDark);
    themeToggleBtn.setAttribute("aria-label", isDark ? "切换白天模式" : "切换黑夜模式");
  }
};

const applyThemeFabVisibility = () => {
  const dismissed = localStorage.getItem(THEME_TOGGLE_FAB_DISMISSED_KEY) === "1";
  if (themeToggleFab) {
    themeToggleFab.classList.toggle("is-hidden", dismissed);
  }
};

const permanentlyDismissThemeToggleFab = () => {
  localStorage.setItem(THEME_TOGGLE_FAB_DISMISSED_KEY, "1");
  applyThemeFabVisibility();
};

const savedTheme = localStorage.getItem("themeMode");
applyTheme(savedTheme === "dark" ? "dark" : "light");
applyThemeFabVisibility();

const THEME_LONG_PRESS_MS = 580;
let themeLongPressTimer = null;
let ignoreNextThemeToggleClick = false;

const clearThemeLongPressTimer = () => {
  if (themeLongPressTimer != null) {
    window.clearTimeout(themeLongPressTimer);
    themeLongPressTimer = null;
  }
};

if (themeToggleBtn) {
  themeToggleBtn.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) {
      return;
    }
    clearThemeLongPressTimer();
    themeLongPressTimer = window.setTimeout(() => {
      themeLongPressTimer = null;
      ignoreNextThemeToggleClick = true;
      permanentlyDismissThemeToggleFab();
      if (typeof navigator.vibrate === "function") {
        try {
          navigator.vibrate(18);
        } catch {
          /* ignore */
        }
      }
    }, THEME_LONG_PRESS_MS);
  });
  themeToggleBtn.addEventListener("pointerup", clearThemeLongPressTimer);
  themeToggleBtn.addEventListener("pointercancel", clearThemeLongPressTimer);
  themeToggleBtn.addEventListener("pointerleave", (e) => {
    if (e.pointerType === "mouse") {
      clearThemeLongPressTimer();
    }
  });
  themeToggleBtn.addEventListener("contextmenu", (e) => {
    e.preventDefault();
  });

  themeToggleBtn.addEventListener("click", () => {
    if (ignoreNextThemeToggleClick) {
      ignoreNextThemeToggleClick = false;
      return;
    }
    const isDark = document.body.classList.contains("dark-mode");
    const nextTheme = isDark ? "light" : "dark";
    localStorage.setItem("themeMode", nextTheme);
    applyTheme(nextTheme);
  });
}

document.querySelectorAll('a[href*="/auth/logout"]').forEach((link) => {
  link.addEventListener("click", () => {
    localStorage.removeItem(THEME_TOGGLE_FAB_DISMISSED_KEY);
  });
});

/** 主工作区：隐藏顶栏与侧栏（非浏览器全屏 API） */
const WORKSPACE_MAX_KEY = "qyWorkspaceMaximized";
const workspaceMaxBtn = document.getElementById("qyWorkspaceMaxBtn");
const workspaceIconExpand = workspaceMaxBtn?.querySelector(".qy-workspace-max-icon-expand");
const workspaceIconRestore = workspaceMaxBtn?.querySelector(".qy-workspace-max-icon-restore");

const applyWorkspaceMaximized = (on) => {
  document.body.classList.toggle("qy-workspace-maximized", Boolean(on));
  if (!workspaceMaxBtn) {
    return;
  }
  workspaceMaxBtn.setAttribute("aria-pressed", on ? "true" : "false");
  workspaceMaxBtn.setAttribute("aria-label", on ? "退出工作区全屏" : "工作区全屏");
  workspaceMaxBtn.setAttribute(
    "title",
    on ? "显示顶栏与侧栏" : "隐藏顶栏与侧栏，扩大主工作区"
  );
  if (workspaceIconExpand && workspaceIconRestore) {
    workspaceIconExpand.classList.toggle("d-none", on);
    workspaceIconRestore.classList.toggle("d-none", !on);
  }
};

if (workspaceMaxBtn) {
  applyWorkspaceMaximized(localStorage.getItem(WORKSPACE_MAX_KEY) === "1");
  workspaceMaxBtn.addEventListener("click", () => {
    const next = !document.body.classList.contains("qy-workspace-maximized");
    localStorage.setItem(WORKSPACE_MAX_KEY, next ? "1" : "0");
    applyWorkspaceMaximized(next);
  });
}

const fullscreenToggleBtn = document.getElementById("fullscreenToggle");
const fullscreenIconEnter = fullscreenToggleBtn?.querySelector(".fullscreen-icon-enter");
const fullscreenIconExit = fullscreenToggleBtn?.querySelector(".fullscreen-icon-exit");
/** 记录用户是否希望保持浏览器原生全屏（整页进入系统全屏，而非仅隐藏顶栏） */
const NATIVE_FULLSCREEN_PREF_KEY = "nativeFullscreenPreferred";
let keepNativeFullscreenUntil = 0;

const getFullscreenElement = () =>
  document.fullscreenElement || document.webkitFullscreenElement || document.msFullscreenElement;

const requestDocumentFullscreen = () => {
  const el = document.documentElement;
  if (typeof el.requestFullscreen === "function") {
    return el.requestFullscreen();
  }
  if (typeof el.webkitRequestFullscreen === "function") {
    return Promise.resolve(el.webkitRequestFullscreen());
  }
  if (typeof el.msRequestFullscreen === "function") {
    return Promise.resolve(el.msRequestFullscreen());
  }
  return Promise.reject(new Error("Fullscreen API not available"));
};

const exitDocumentFullscreen = () => {
  if (typeof document.exitFullscreen === "function") {
    return document.exitFullscreen();
  }
  if (typeof document.webkitExitFullscreen === "function") {
    return document.webkitExitFullscreen();
  }
  if (typeof document.msExitFullscreen === "function") {
    return document.msExitFullscreen();
  }
  return Promise.reject(new Error("Exit fullscreen not available"));
};

const isNativeFullscreenSupported =
  typeof document.documentElement.requestFullscreen === "function" ||
  typeof document.documentElement.webkitRequestFullscreen === "function" ||
  typeof document.documentElement.msRequestFullscreen === "function";

const updateFullscreenState = () => {
  if (!fullscreenToggleBtn || !fullscreenIconEnter || !fullscreenIconExit) {
    return;
  }
  const isFullscreen = Boolean(getFullscreenElement());
  fullscreenIconEnter.classList.toggle("d-none", isFullscreen);
  fullscreenIconExit.classList.toggle("d-none", !isFullscreen);
  fullscreenToggleBtn.setAttribute("aria-label", isFullscreen ? "退出全屏" : "进入全屏");
  fullscreenToggleBtn.setAttribute("title", isFullscreen ? "退出全屏" : "进入全屏");
};

const tryRestoreNativeFullscreenFromPreference = () => {
  if (localStorage.getItem(NATIVE_FULLSCREEN_PREF_KEY) !== "1") {
    return;
  }
  if (getFullscreenElement()) {
    return;
  }
  if (!isNativeFullscreenSupported) {
    return;
  }
  requestDocumentFullscreen().catch(() => {
    // 多数浏览器在整页跳转后需要用户再次手势才能进入全屏，此处静默失败即可。
  });
};

if (fullscreenToggleBtn) {
  const logoutLinks = Array.from(document.querySelectorAll("a[href]")).filter((link) =>
    link.getAttribute("href")?.includes("/auth/logout")
  );

  logoutLinks.forEach((link) => {
    link.addEventListener("click", () => {
      localStorage.setItem(NATIVE_FULLSCREEN_PREF_KEY, "0");
      if (getFullscreenElement()) {
        exitDocumentFullscreen().catch(() => {});
      }
    });
  });

  updateFullscreenState();

  window.addEventListener("pageshow", () => {
    tryRestoreNativeFullscreenFromPreference();
  });

  fullscreenToggleBtn.addEventListener("click", async () => {
    const isCurrentlyFullscreen = Boolean(getFullscreenElement());

    if (isCurrentlyFullscreen) {
      try {
        await exitDocumentFullscreen();
      } catch (_error) {
        // ignore
      }
      localStorage.setItem(NATIVE_FULLSCREEN_PREF_KEY, "0");
      updateFullscreenState();
      return;
    }

    if (!isNativeFullscreenSupported) {
      return;
    }

    try {
      await requestDocumentFullscreen();
      localStorage.setItem(NATIVE_FULLSCREEN_PREF_KEY, "1");
    } catch (_error) {
      localStorage.setItem(NATIVE_FULLSCREEN_PREF_KEY, "0");
    }
    updateFullscreenState();
  });

  const onFullscreenChange = () => {
    const droppedDuringProtectedWindow =
      !getFullscreenElement() && Date.now() < keepNativeFullscreenUntil;
    if (droppedDuringProtectedWindow && isNativeFullscreenSupported) {
      requestDocumentFullscreen().catch(() => {});
      return;
    }
    updateFullscreenState();
  };

  document.addEventListener("fullscreenchange", onFullscreenChange);
  document.addEventListener("webkitfullscreenchange", onFullscreenChange);
  document.addEventListener("MSFullscreenChange", onFullscreenChange);

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") {
      return;
    }
    if (!getFullscreenElement()) {
      return;
    }
    requestAnimationFrame(() => {
      if (!getFullscreenElement()) {
        localStorage.setItem(NATIVE_FULLSCREEN_PREF_KEY, "0");
        updateFullscreenState();
      }
    });
  });
}

const sidebarToggleBtn = document.getElementById("sidebarToggle");
const sidebarIconCollapse = sidebarToggleBtn?.querySelector(".sidebar-toggle-icon-collapse");
const sidebarIconExpand = sidebarToggleBtn?.querySelector(".sidebar-toggle-icon-expand");
const sidebarNavRoot = document.querySelector(".staff-sidebar-nav");
const popupNavGroups = Array.from(document.querySelectorAll(".staff-nav-group")).filter(
  (group) => group.querySelector(".staff-nav-toggle") && group.querySelector(".staff-nav-submenu")
);
const popupCloseTimers = new WeakMap();
/** 鼠标离开折叠子菜单区域后再关闭浮层，留出时间移入浮层点击 */
const POPUP_CLOSE_DELAY_MS = 300;

const protectNativeFullscreenWindow = (ms = 1500) => {
  if (getFullscreenElement()) {
    keepNativeFullscreenUntil = Date.now() + ms;
  }
};

const clearPopupCloseTimer = (group) => {
  const timer = popupCloseTimers.get(group);
  if (timer) {
    clearTimeout(timer);
    popupCloseTimers.delete(group);
  }
};

const closePopupGroup = (group) => {
  clearPopupCloseTimer(group);
  group.classList.remove("popup-open");
};

const closeAllPopupGroups = () => {
  popupNavGroups.forEach((group) => closePopupGroup(group));
};

const closeOtherPopupGroups = (keepGroup) => {
  popupNavGroups.forEach((g) => {
    if (g !== keepGroup) {
      closePopupGroup(g);
    }
  });
};

const schedulePopupClose = (group) => {
  clearPopupCloseTimer(group);
  const timer = window.setTimeout(() => {
    closePopupGroup(group);
  }, POPUP_CLOSE_DELAY_MS);
  popupCloseTimers.set(group, timer);
};

const positionPopupGroup = (group) => {
  const toggle = group.querySelector(".staff-nav-toggle");
  const menu = group.querySelector(".staff-nav-submenu");
  if (!toggle || !menu) {
    return;
  }
  const triggerRect = toggle.getBoundingClientRect();
  const popupTop = Math.max(64, triggerRect.top - 6);
  const popupLeft = triggerRect.right + 12;
  menu.style.top = `${popupTop}px`;
  menu.style.left = `${popupLeft}px`;
};

const staffTooltipTargets = document.querySelectorAll(".staff-nav-link[data-tooltip]");
const staffFloatTooltip = document.createElement("div");
staffFloatTooltip.className = "staff-float-tooltip";
staffFloatTooltip.setAttribute("role", "tooltip");
document.body.appendChild(staffFloatTooltip);

let staffTooltipHideTimer = null;
const TOOLTIP_HIDE_DELAY_MS = 300;

const hideStaffSidebarTooltipNow = () => {
  if (staffTooltipHideTimer) {
    clearTimeout(staffTooltipHideTimer);
    staffTooltipHideTimer = null;
  }
  staffFloatTooltip.classList.remove("show");
};

const showStaffTooltip = (target) => {
  if (!document.body.classList.contains("staff-sidebar-collapsed")) {
    return;
  }
  if (target.classList.contains("staff-nav-toggle")) {
    return;
  }
  closeAllPopupGroups();
  if (staffTooltipHideTimer) {
    clearTimeout(staffTooltipHideTimer);
    staffTooltipHideTimer = null;
  }
  const text = target.getAttribute("data-tooltip");
  if (!text) {
    return;
  }
  const rect = target.getBoundingClientRect();
  staffFloatTooltip.textContent = text;
  staffFloatTooltip.style.left = `${rect.right + 10}px`;
  staffFloatTooltip.style.top = `${rect.top + (rect.height - 28) / 2}px`;
  requestAnimationFrame(() => {
    staffFloatTooltip.classList.add("show");
  });
};

const hideStaffTooltip = () => {
  if (staffTooltipHideTimer) {
    clearTimeout(staffTooltipHideTimer);
    staffTooltipHideTimer = null;
  }
  staffTooltipHideTimer = window.setTimeout(() => {
    staffTooltipHideTimer = null;
    staffFloatTooltip.classList.remove("show");
  }, TOOLTIP_HIDE_DELAY_MS);
};

staffTooltipTargets.forEach((target) => {
  target.addEventListener("mouseenter", () => showStaffTooltip(target));
  target.addEventListener("mouseleave", hideStaffTooltip);
  target.addEventListener("focus", () => showStaffTooltip(target));
  target.addEventListener("blur", hideStaffTooltip);
});

const applySidebarState = (collapsed) => {
  document.body.classList.toggle("staff-sidebar-collapsed", collapsed);
  if (sidebarToggleBtn) {
    sidebarToggleBtn.setAttribute("aria-label", collapsed ? "展开侧边栏" : "折叠侧边栏");
    sidebarToggleBtn.setAttribute("title", collapsed ? "展开侧边栏" : "折叠侧边栏");
  }
  if (sidebarIconCollapse && sidebarIconExpand) {
    sidebarIconCollapse.classList.toggle("is-hidden", collapsed);
    sidebarIconExpand.classList.toggle("is-hidden", !collapsed);
  }
  if (!collapsed) {
    closeAllPopupGroups();
    hideStaffSidebarTooltipNow();
    popupNavGroups.forEach((group) => {
      const menu = group.querySelector(".staff-nav-submenu");
      if (menu) {
        menu.style.top = "";
        menu.style.left = "";
      }
    });
  }
};

if (sidebarToggleBtn) {
  const savedCollapsed = localStorage.getItem("staffSidebarCollapsed") === "1";
  applySidebarState(savedCollapsed);

  sidebarToggleBtn.addEventListener("click", (event) => {
    // Guard against accidental navigation/focus side effects.
    event.preventDefault();
    event.stopPropagation();

    const wasNativeFullscreen = Boolean(getFullscreenElement());
    protectNativeFullscreenWindow(1500);

    const nextCollapsed = !document.body.classList.contains("staff-sidebar-collapsed");
    localStorage.setItem("staffSidebarCollapsed", nextCollapsed ? "1" : "0");
    applySidebarState(nextCollapsed);

    if (wasNativeFullscreen && isNativeFullscreenSupported) {
      requestAnimationFrame(() => {
        if (!getFullscreenElement() && Date.now() < keepNativeFullscreenUntil) {
          requestDocumentFullscreen().catch(() => {});
        }
      });
    }
  });
}

if (sidebarNavRoot) {
  // Protect native fullscreen while interacting with any sidebar menu item.
  const markSidebarInteraction = () => protectNativeFullscreenWindow(1200);
  sidebarNavRoot.addEventListener("pointerdown", markSidebarInteraction);
  sidebarNavRoot.addEventListener("click", markSidebarInteraction);
}

if (popupNavGroups.length > 0) {
  popupNavGroups.forEach((group) => {
    const toggle = group.querySelector(".staff-nav-toggle");
    const menu = group.querySelector(".staff-nav-submenu");
    if (!toggle || !menu) {
      return;
    }

    toggle.addEventListener("mouseenter", () => {
      if (document.body.classList.contains("staff-sidebar-collapsed")) {
        closeOtherPopupGroups(group);
        hideStaffSidebarTooltipNow();
        positionPopupGroup(group);
        group.classList.add("popup-open");
      }
    });

    toggle.addEventListener("click", (event) => {
      if (document.body.classList.contains("staff-sidebar-collapsed")) {
        event.preventDefault();
        event.stopPropagation();
        const wasOpen = group.classList.contains("popup-open");
        if (!wasOpen) {
          closeOtherPopupGroups(group);
          hideStaffSidebarTooltipNow();
        }
        positionPopupGroup(group);
        group.classList.toggle("popup-open");
      }
    });

    group.addEventListener("mouseenter", () => {
      clearPopupCloseTimer(group);
    });

    group.addEventListener("mouseleave", () => {
      if (document.body.classList.contains("staff-sidebar-collapsed")) {
        schedulePopupClose(group);
      }
    });

    // 浮层为 position:fixed，视觉上已离开 group 盒模型，需单独监听以免移入时被误关
    menu.addEventListener("mouseenter", () => {
      clearPopupCloseTimer(group);
    });

    menu.addEventListener("mouseleave", () => {
      if (document.body.classList.contains("staff-sidebar-collapsed")) {
        schedulePopupClose(group);
      }
    });
  });

  document.addEventListener("click", (event) => {
    if (
      document.body.classList.contains("staff-sidebar-collapsed") &&
      !popupNavGroups.some((group) => group.contains(event.target))
    ) {
      closeAllPopupGroups();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeAllPopupGroups();
    }
  });

  window.addEventListener("resize", () => {
    popupNavGroups.forEach((group) => {
      if (group.classList.contains("popup-open")) {
        positionPopupGroup(group);
      }
    });
  });

  window.addEventListener("scroll", () => {
    popupNavGroups.forEach((group) => {
      if (group.classList.contains("popup-open")) {
        positionPopupGroup(group);
      }
    });
  });
}

/* ---------- 员工/管理/客户端：同区域内 SPA 导航（不整页刷新，利于保持浏览器全屏） ---------- */
(() => {
  const spaLayout = document.getElementById("qy-spa-layout");
  if (!spaLayout) {
    return;
  }

  const SPA_HEADER = "X-Qy-Spa";
  const spaAreaPrefix = (spaLayout.dataset.spaArea || "").replace(/\/$/, "");
  const tabsScroll = document.querySelector("#qySpaTabsBar .qy-spa-tabs-scroll");

  // 首页标签必须用当前身份的真实首页：员工端撰写师是 /staff/dashboard，
  // 流程和业务人员分别是 process-dashboard、business-dashboard，写死会 403。
  const homePathname = (() => {
    const fallback = `${spaAreaPrefix}/dashboard`;
    const declared = (spaLayout.dataset.spaHome || "").trim();
    if (!declared) {
      return fallback;
    }
    try {
      return new URL(declared, window.location.origin).pathname;
    } catch {
      return declared.split("?")[0] || fallback;
    }
  })();

  // 标签缓存按「用户 + 首页」隔离。sessionStorage 在同一浏览器会话里是共享的，
  // 不隔离的话换账号登录会继承上一个人的标签，点开全是无权访问的页面；
  // 首页入 key 还能让管理员改职能后自动弃用旧标签。
  const tabStorageKey = `qySpaTabs:v1:${spaAreaPrefix || "/"}:${
    spaLayout.dataset.spaScope || "anon"
  }:${homePathname}`;

  const isHomeTabHref = (pathQuery) => {
    try {
      const u = new URL(pathQuery, window.location.origin);
      return u.pathname === homePathname;
    } catch {
      return (pathQuery.split("?")[0] || "") === homePathname;
    }
  };

  // 这些列表页无论筛选/分页参数如何，都只保留一个标签（按路径归一，忽略查询串）。
  // 用区域相对后缀构建，自动适配 /admin、/staff、/client 各端的同名列表页。
  const singletonTabSuffixes = [
    "/cases",
    "/customers",
    "/project-initiation",
    "/task-board",
    "/accounts",
    "/files",
  ];
  const singletonTabPaths = new Set(
    singletonTabSuffixes.map((suffix) => `${spaAreaPrefix}${suffix}`)
  );

  const canonicalTabHref = (pathQuery) => {
    if (isHomeTabHref(pathQuery)) {
      return homePathname;
    }
    try {
      const u = new URL(pathQuery, window.location.origin);
      if (singletonTabPaths.has(u.pathname)) {
        return u.pathname;
      }
      return u.pathname + u.search;
    } catch {
      return pathQuery;
    }
  };

  // 详情类页面：路径形如 /<area>/case-detail/<id>，不同 id 仍归并到同一个标签。
  // 标签身份用分组前缀（key），但导航地址（href）保留含 id 的完整路径以便回到具体记录。
  const detailSingletonPrefixes = [`${spaAreaPrefix}/case-detail`];

  const detailGroupKey = (pathname) => {
    for (const prefix of detailSingletonPrefixes) {
      if (pathname === prefix || pathname.startsWith(`${prefix}/`)) {
        return prefix;
      }
    }
    return null;
  };

  // 标签身份键：详情页用分组前缀，其它页用归一化后的路径。
  const tabKeyForPath = (pathQuery) => {
    try {
      const u = new URL(pathQuery, window.location.origin);
      return detailGroupKey(u.pathname) || canonicalTabHref(pathQuery);
    } catch {
      return canonicalTabHref(pathQuery);
    }
  };

  // 标签导航地址：详情页保留完整路径（含 id 与查询），其它页用归一化路径。
  const navHrefForPath = (pathQuery) => {
    try {
      const u = new URL(pathQuery, window.location.origin);
      if (detailGroupKey(u.pathname)) {
        return u.pathname + u.search;
      }
      // 列表页标签身份仍按 pathname 单例化，但返回地址必须保留筛选、排序和分页参数。
      if (singletonTabPaths.has(u.pathname)) {
        return u.pathname + u.search;
      }
      return canonicalTabHref(pathQuery);
    } catch {
      return canonicalTabHref(pathQuery);
    }
  };

  const ensureHomeTabFirst = () => {
    const nonHome = tabState.tabs.filter((t) => !isHomeTabHref(t.href));
    const homeTab = { key: homePathname, href: homePathname, title: "首页" };
    tabState.tabs = [homeTab, ...nonHome];
    if (isHomeTabHref(tabState.activeKey)) {
      tabState.activeKey = homePathname;
    }
  };

  const tabShortTitle = (full) => {
    if (!full) {
      return "未命名";
    }
    const trimmed = full
      .replace(/\s*—\s*琴岳专利管理系统\s*$/i, "")
      .replace(/\s*-\s*琴岳专利管理系统\s*$/i, "")
      .trim();
    return trimmed || full;
  };

  const normalizedTabTitle = (title) =>
    tabShortTitle(title || "").trim().toLocaleLowerCase();

  let tabState = { tabs: [], activeKey: "" };

  const dedupeTabs = (preferredKey = "") => {
    const preferred = tabState.tabs.find((tab) => tab.key === preferredKey) || null;
    const seenKeys = new Set();
    const seenTitles = new Set();
    const ordered = preferred
      ? [preferred, ...tabState.tabs.filter((tab) => tab !== preferred)]
      : tabState.tabs;
    const kept = ordered.filter((tab) => {
      const titleKey = normalizedTabTitle(tab.title);
      if (seenKeys.has(tab.key) || (titleKey && seenTitles.has(titleKey))) {
        return false;
      }
      seenKeys.add(tab.key);
      if (titleKey) {
        seenTitles.add(titleKey);
      }
      return true;
    });
    // 当前标签优先参与去重，但最终仍按原来的标签顺序显示。
    const keptSet = new Set(kept);
    tabState.tabs = tabState.tabs.filter((tab) => keptSet.has(tab));
  };

  const persistTabState = () => {
    if (!tabsScroll) {
      return;
    }
    try {
      sessionStorage.setItem(tabStorageKey, JSON.stringify(tabState));
    } catch {
      // ignore quota / private mode
    }
  };

  const renderSpaTabs = () => {
    if (!tabsScroll) {
      return;
    }
    tabsScroll.innerHTML = "";
    tabState.tabs.forEach((tab) => {
      const isActive = tab.key === tabState.activeKey;
      const pinnedHome = isHomeTabHref(tab.href);
      const row = document.createElement("div");
      row.className = `qy-spa-tab${isActive ? " is-active" : ""}${pinnedHome ? " is-home" : ""}`;
      row.setAttribute("role", "tab");
      row.setAttribute("aria-selected", isActive ? "true" : "false");
      row.dataset.href = tab.href;
      row.dataset.key = tab.key;

      const selectBtn = document.createElement("button");
      selectBtn.type = "button";
      selectBtn.className = "qy-spa-tab-select";
      selectBtn.textContent = tab.title;

      row.appendChild(selectBtn);
      if (!pinnedHome) {
        const closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.className = "qy-spa-tab-close";
        closeBtn.setAttribute("aria-label", `关闭 ${tab.title}`);
        closeBtn.textContent = "×";
        row.appendChild(closeBtn);
      }

      tabsScroll.appendChild(row);
    });

    const activeTab = tabsScroll.querySelector(".qy-spa-tab.is-active");
    if (activeTab) {
      activeTab.scrollIntoView({ inline: "nearest", block: "nearest" });
    }
  };

  const syncTabsForUrl = (pathQuery, titleSource) => {
    if (!tabsScroll) {
      return;
    }
    const key = tabKeyForPath(pathQuery);
    const href = navHrefForPath(pathQuery);
    const title = tabShortTitle(titleSource || "");
    const titleKey = normalizedTabTitle(title);
    const existing =
      tabState.tabs.find((t) => t.key === key) ||
      tabState.tabs.find((t) => normalizedTabTitle(t.title) === titleKey) ||
      (isHomeTabHref(pathQuery) ? tabState.tabs.find((t) => isHomeTabHref(t.href)) : null);
    if (existing) {
      existing.key = key;
      existing.href = href;
      existing.title = title;
    } else {
      tabState.tabs.push({ key, href, title });
    }
    tabState.activeKey = key;
    dedupeTabs(key);
    ensureHomeTabFirst();
    persistTabState();
    renderSpaTabs();
  };

  const isSpaInternalUrl = (url) => {
    try {
      const u = typeof url === "string" ? new URL(url, window.location.origin) : url;
      if (u.origin !== window.location.origin) {
        return false;
      }
      const path = u.pathname;
      return Boolean(
        spaAreaPrefix && (path === spaAreaPrefix || path.startsWith(`${spaAreaPrefix}/`))
      );
    } catch {
      return false;
    }
  };

  const applySpaSidebarActive = (endpoint) => {
    const root = document.querySelector(".staff-sidebar-nav");
    if (!root || !endpoint) {
      return;
    }
    root.querySelectorAll(".staff-nav-link, .staff-nav-sublink").forEach((el) => {
      el.classList.remove("active", "is-current");
      el.removeAttribute("aria-current");
    });
    const current = root.querySelector(`[data-spa-endpoint="${endpoint}"]`);
    if (!current) {
      return;
    }
    current.classList.add("active");
    if (current.classList.contains("staff-nav-link")) {
      current.classList.add("is-current");
      current.setAttribute("aria-current", "page");
    }
    const submenu = current.closest(".staff-nav-submenu");
    if (submenu) {
      submenu.classList.add("show");
      const toggle = root.querySelector(`[aria-controls="${submenu.id}"]`);
      if (toggle) {
        toggle.setAttribute("aria-expanded", "true");
      }
    }
  };

  const resetSpaScroll = () => {
    window.scrollTo(0, 0);
    document.querySelector(".staff-spa-body")?.scrollTo(0, 0);
  };

  const spaNavigate = async (
    href,
    { skipHistory = false, force = false, preserveScroll = false } = {}
  ) => {
    let reqUrl;
    try {
      reqUrl = new URL(href, window.location.origin);
    } catch {
      window.location.assign(href);
      return;
    }
    const pathQuery = reqUrl.pathname + reqUrl.search;
    if (!force && pathQuery === window.location.pathname + window.location.search) {
      return Promise.resolve();
    }

    const mainEl = document.getElementById("qy-spa-main");
    if (!mainEl) {
      window.location.assign(href);
      return;
    }

    const spaBody = document.querySelector(".staff-spa-body");
    const savedScroll = preserveScroll
      ? {
          windowY: window.scrollY || window.pageYOffset || 0,
          bodyY: spaBody?.scrollTop || 0,
        }
      : null;
    // 快速 SPA 切换不显示加载提示，避免中间转圈一闪而过；
    // 仅请求超过短暂阈值时显示轻量进度条。
    const loadingTimer = window.setTimeout(() => {
      spaBody?.classList.add("qy-spa-loading");
    }, 180);

    try {
      const response = await fetch(reqUrl.toString(), {
        credentials: "same-origin",
        headers: { [SPA_HEADER]: "1" },
      });

      if (response.redirected) {
        window.location.assign(response.url);
        return;
      }

      // 无权访问：留在当前页面并摘掉该标签。整页跳到 403 页会连标签栏一起丢掉，
      // 用户还得重新登录式地点回来。
      if (response.status === 403) {
        dropSpaTab(tabKeyForPath(pathQuery));
        if (typeof window.qyShowToast === "function") {
          window.qyShowToast("你没有访问该页面的权限。", "warning");
        }
        return;
      }

      if (!response.ok) {
        window.location.assign(pathQuery);
        return;
      }

      const html = await response.text();
      const doc = new DOMParser().parseFromString(html, "text/html");
      const nextMain = doc.getElementById("qy-spa-main");
      if (!nextMain) {
        window.location.assign(pathQuery);
        return;
      }

      if (typeof window.qyDisposeBootstrapTooltips === "function") {
        window.qyDisposeBootstrapTooltips(mainEl);
      }
      mainEl.replaceWith(nextMain);
      const docTitle = nextMain.dataset.spaDocumentTitle;
      if (docTitle) {
        document.title = docTitle;
      }
      applySpaSidebarActive(nextMain.dataset.spaEndpoint);
      syncTabsForUrl(pathQuery, nextMain.dataset.spaDocumentTitle || docTitle || document.title);
      if (typeof window.qyRefreshReviewBadge === "function") {
        window.qyRefreshReviewBadge();
      }
      if (typeof window.qyRefreshOrderIntakeBadge === "function") {
        window.qyRefreshOrderIntakeBadge();
      }
      if (typeof window.qyRefreshStaffNotificationBadge === "function") {
        window.qyRefreshStaffNotificationBadge();
      }

      if (!skipHistory) {
        history.pushState({ qySpa: true }, "", pathQuery);
      }

      const spaMainEl = document.getElementById("qy-spa-main");
      if (typeof window.qyConsumeUrlToast === "function") {
        window.qyConsumeUrlToast();
      }
      if (typeof window.qyInitBootstrapTooltips === "function" && spaMainEl) {
        window.qyInitBootstrapTooltips(spaMainEl);
      }
      if (typeof window.qyInitClickableRows === "function" && spaMainEl) {
        window.qyInitClickableRows(spaMainEl);
      }
      if (typeof window.qyInitCaseTypeSelectors === "function" && spaMainEl) {
        window.qyInitCaseTypeSelectors(spaMainEl);
      }
      if (typeof window.qyInitCasesCustomerProjectFilter === "function" && spaMainEl) {
        window.qyInitCasesCustomerProjectFilter(spaMainEl);
      }
      if (typeof window.qyInitBusinessOrderPage === "function" && spaMainEl) {
        window.qyInitBusinessOrderPage(spaMainEl);
      }

      if (savedScroll) {
        const restoreSavedScroll = () => {
          window.scrollTo(0, savedScroll.windowY);
          document.querySelector(".staff-spa-body")?.scrollTo(0, savedScroll.bodyY);
        };
        restoreSavedScroll();
        requestAnimationFrame(restoreSavedScroll);
        setTimeout(restoreSavedScroll, 60);
      } else {
        resetSpaScroll();
        requestAnimationFrame(resetSpaScroll);
      }
    } catch {
      window.location.assign(href);
    } finally {
      window.clearTimeout(loadingTimer);
      spaBody?.classList.remove("qy-spa-loading");
    }
  };

  // 只摘掉标签，不做跳转：用于无权访问时清掉残留标签，让用户留在当前页面。
  const dropSpaTab = (key) => {
    if (!tabsScroll) {
      return;
    }
    const idx = tabState.tabs.findIndex((t) => t.key === key);
    if (idx < 0 || isHomeTabHref(tabState.tabs[idx].href)) {
      return;
    }
    tabState.tabs.splice(idx, 1);
    if (tabState.activeKey === key) {
      tabState.activeKey = tabKeyForPath(
        window.location.pathname + window.location.search
      );
    }
    persistTabState();
    renderSpaTabs();
  };

  const closeSpaTab = (key) => {
    if (!tabsScroll || tabState.tabs.length <= 1) {
      return;
    }
    const idx = tabState.tabs.findIndex((t) => t.key === key);
    if (idx < 0 || isHomeTabHref(tabState.tabs[idx].href)) {
      return;
    }
    const wasActive = tabState.activeKey === key;
    tabState.tabs.splice(idx, 1);
    if (!wasActive) {
      persistTabState();
      renderSpaTabs();
      return;
    }
    const nextTab = tabState.tabs[Math.min(idx, tabState.tabs.length - 1)];
    tabState.activeKey = nextTab.key;
    persistTabState();
    renderSpaTabs();
    const nextUrl = new URL(nextTab.href, window.location.origin).toString();
    protectNativeFullscreenWindow(1200);
    spaNavigate(nextUrl, { skipHistory: false });
  };

  if (tabsScroll) {
    tabsScroll.addEventListener("click", (event) => {
      const closeBtn = event.target.closest(".qy-spa-tab-close");
      const tabEl = event.target.closest(".qy-spa-tab");
      if (!tabEl || !tabEl.dataset.href) {
        return;
      }
      const href = tabEl.dataset.href;
      const key = tabEl.dataset.key || href;
      if (closeBtn) {
        event.preventDefault();
        event.stopPropagation();
        closeSpaTab(key);
        return;
      }
      const pathQuery = window.location.pathname + window.location.search;
      if (href === pathQuery) {
        return;
      }
      event.preventDefault();
      protectNativeFullscreenWindow(1200);
      spaNavigate(new URL(href, window.location.origin).toString());
    });
  }

  const initSpaTabs = () => {
    if (!tabsScroll) {
      return;
    }
    const current = window.location.pathname + window.location.search;
    const currentKey = tabKeyForPath(current);
    const currentHref = navHrefForPath(current);
    const mainEl0 = document.getElementById("qy-spa-main");
    const title0 = tabShortTitle(
      (mainEl0 && mainEl0.dataset.spaDocumentTitle) || document.title
    );

    let parsed = null;
    try {
      const raw = sessionStorage.getItem(tabStorageKey);
      if (raw) {
        parsed = JSON.parse(raw);
      }
    } catch {
      parsed = null;
    }

    const validStored =
      parsed &&
      Array.isArray(parsed.tabs) &&
      parsed.tabs.length > 0 &&
      parsed.tabs.every(
        (t) => t && typeof t.href === "string" && typeof t.title === "string"
      );

    if (validStored) {
      tabState.tabs = parsed.tabs.map((t) => {
        // 兼容旧数据：可能只有 href、没有 key。
        const key = t.key || tabKeyForPath(t.href);
        return { key, href: navHrefForPath(t.href), title: t.title };
      });
      const hasCurrent = tabState.tabs.some((t) => t.key === currentKey);
      if (!hasCurrent) {
        tabState.tabs.push({ key: currentKey, href: currentHref, title: title0 });
      }
      tabState.activeKey = currentKey;
      const curTab = tabState.tabs.find((t) => t.key === currentKey);
      if (curTab) {
        curTab.title = title0;
        curTab.href = currentHref;
      }
    } else {
      tabState.tabs = [{ key: currentKey, href: currentHref, title: title0 }];
      tabState.activeKey = currentKey;
    }
    // 同一路径或同名页面只保留一个；当前页面优先，兼容 sessionStorage 中的旧重复标签。
    dedupeTabs(currentKey);
    ensureHomeTabFirst();
    persistTabState();
    renderSpaTabs();
  };

  initSpaTabs();

  spaLayout.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    if (form.method.toLowerCase() !== "get") {
      return;
    }
    if (
      form.id !== "qy-customers-filter-form" &&
      form.id !== "qy-projects-filter-form" &&
      form.id !== "qy-cases-filter-form" &&
      form.id !== "qy-task-board-filter-form" &&
      form.id !== "qy-accounts-filter-form"
    ) {
      return;
    }
    event.preventDefault();
    const submitBtn = form.querySelector("#qy-customers-query-btn, #qy-projects-query-btn, #qy-cases-query-btn, #qy-task-board-query-btn, #qy-accounts-query-btn");
    const labelEl = submitBtn?.querySelector(".qy-customers-query-label, .qy-projects-query-label, .qy-cases-query-label, .qy-task-board-query-label, .qy-accounts-query-label");
    const prevDisabled = submitBtn?.disabled;
    const prevLabelHtml = labelEl?.innerHTML;
    if (submitBtn) {
      submitBtn.disabled = true;
      if (labelEl) {
        labelEl.textContent = "查询中...";
      }
    }
    const params = new URLSearchParams();
    const fd = new FormData(form);
    fd.forEach((value, key) => {
      if (value !== "") {
        params.append(key, value);
      }
    });
    const actionUrl = new URL(form.getAttribute("action") || window.location.pathname, window.location.origin);
    const qs = params.toString();
    const href = qs ? `${actionUrl.pathname}?${qs}` : actionUrl.pathname;
    spaNavigate(href).finally(() => {
      if (submitBtn && document.body.contains(submitBtn)) {
        submitBtn.disabled = prevDisabled ?? false;
        if (labelEl && prevLabelHtml !== undefined) {
          labelEl.innerHTML = prevLabelHtml;
        }
      }
    });
  });

  // 案件详情：删除/审核走 AJAX 局部刷新；材料上传走 XHR 并显示进度条与百分比。
  const SCROLL_RESTORE_KEY = `qyScrollRestore:${spaAreaPrefix || "/"}`;

  const isCaseDetailUploadForm = (form) => {
    if (form.enctype !== "multipart/form-data") {
      return false;
    }
    const actionVal = form.querySelector('input[name="form_action"]')?.value;
    const fileInput = form.querySelector('input[type="file"][name="material_file"]');
    return actionVal === "upload_material" && fileInput instanceof HTMLInputElement;
  };

  const isCaseDetailAjaxForm = (form) => {
    if (form.dataset && form.dataset.qyAjax === "1") {
      return true;
    }
    const actionVal = form.querySelector('input[name="form_action"]')?.value;
    const reviewVal = form.querySelector('input[name="review_action"]')?.value;
    return (
      actionVal === "delete_material" ||
      reviewVal === "approve" ||
      reviewVal === "reject"
    );
  };

  const parseToastFromUrl = (rawUrl) => {
    try {
      const u = new URL(rawUrl, window.location.origin);
      const msg = u.searchParams.get("qy_toast");
      const rv = (u.searchParams.get("qy_toast_variant") || "success").toLowerCase();
      const variant =
        rv === "danger" || rv === "error"
          ? "danger"
          : rv === "warning"
            ? "warning"
            : rv === "info"
              ? "info"
              : "success";
      return { msg, variant };
    } catch {
      return { msg: null, variant: "success" };
    }
  };

  const submitCaseDetailAction = async (form) => {
    const submitBtn =
      form.querySelector('button[type="submit"]') || form.querySelector("button");
    const prevDisabled = submitBtn ? submitBtn.disabled : false;
    if (submitBtn) {
      submitBtn.disabled = true;
    }
    let toast = { msg: null, variant: "success" };
    try {
      const postUrl = form.getAttribute("action") || window.location.href;
      const formData = new FormData(form);
      const headers = { [SPA_HEADER]: "1" };
      const csrfToken =
        formData.get("csrf_token") ||
        (typeof window.qyCsrfToken === "function" ? window.qyCsrfToken() : "");
      if (csrfToken) {
        headers["X-CSRFToken"] = csrfToken;
      }
      const res = await fetch(postUrl, {
        method: "POST",
        credentials: "same-origin",
        headers,
        body: formData,
      });
      toast = parseToastFromUrl(res.url || window.location.href);
      if (!res.ok) {
        if (typeof window.qyShowToast === "function") {
          window.qyShowToast(toast.msg || "操作失败，请重试。", "danger");
        }
        return;
      }
    } catch {
      if (typeof window.qyShowToast === "function") {
        window.qyShowToast("网络异常，请重试。", "danger");
      }
      return;
    } finally {
      if (submitBtn && document.body.contains(submitBtn)) {
        submitBtn.disabled = prevDisabled;
      }
    }

    if (typeof window.qyRefreshReviewBadge === "function") {
      window.qyRefreshReviewBadge();
    }
    if (typeof window.qyRefreshOrderIntakeBadge === "function") {
      window.qyRefreshOrderIntakeBadge();
    }

    await spaNavigate(window.location.href, {
      skipHistory: true,
      force: true,
      preserveScroll: true,
    });
    if (toast.msg && typeof window.qyShowToast === "function") {
      window.qyShowToast(toast.msg, toast.variant);
    }
  };

  const submitCaseDetailUpload = (form) => {
    if (form.dataset.qyUploading === "1") {
      return;
    }
    const fileInput = form.querySelector('input[type="file"][name="material_file"]');
    if (!(fileInput instanceof HTMLInputElement) || !fileInput.files?.length) {
      if (typeof window.qyShowToast === "function") {
        window.qyShowToast("请选择要上传的文件。", "warning");
      }
      return;
    }

    const scrollY = window.scrollY || window.pageYOffset || 0;
    const submitBtn =
      form.querySelector('button[type="submit"]') || form.querySelector("button");
    const prevBtnHtml = submitBtn ? submitBtn.innerHTML : "";
    const controls = [...form.querySelectorAll("input, select, button")];

    let progressWrap = form.querySelector(".qy-upload-progress");
    if (!progressWrap) {
      progressWrap = document.createElement("div");
      progressWrap.className = "qy-upload-progress mt-2";
      progressWrap.setAttribute("aria-live", "polite");
      progressWrap.innerHTML = `
        <div class="d-flex align-items-center gap-2 small text-muted mb-1">
          <span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>
          <span class="qy-upload-status">正在上传，请稍候…</span>
        </div>
        <div class="progress qy-upload-progress-bar">
          <div class="progress-bar progress-bar-striped progress-bar-animated" role="progressbar" style="width: 0%" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100"></div>
        </div>`;
      form.appendChild(progressWrap);
    }
    const progressBar = progressWrap.querySelector(".progress-bar");
    const statusEl = progressWrap.querySelector(".qy-upload-status");

    const restoreForm = () => {
      delete form.dataset.qyUploading;
      controls.forEach((el) => {
        el.disabled = false;
      });
      if (submitBtn && document.body.contains(submitBtn)) {
        submitBtn.innerHTML = prevBtnHtml;
      }
      progressWrap?.remove();
    };

    const postUrl = form.getAttribute("action") || window.location.href;
    const formData = new FormData(form);

    form.dataset.qyUploading = "1";
    controls.forEach((el) => {
      el.disabled = true;
    });
    if (submitBtn) {
      submitBtn.textContent = "上传中…";
    }

    const xhr = new XMLHttpRequest();
    xhr.open("POST", postUrl);
    xhr.setRequestHeader(SPA_HEADER, "1");
    const csrfToken =
      formData.get("csrf_token") ||
      (typeof window.qyCsrfToken === "function" ? window.qyCsrfToken() : "");
    if (csrfToken) {
      xhr.setRequestHeader("X-CSRFToken", csrfToken);
    }
    xhr.withCredentials = true;

    xhr.upload.addEventListener("progress", (event) => {
      if (!progressBar || !statusEl) {
        return;
      }
      if (event.lengthComputable && event.total > 0) {
        const pct = Math.min(100, Math.round((event.loaded / event.total) * 100));
        progressBar.style.width = `${pct}%`;
        progressBar.setAttribute("aria-valuenow", String(pct));
        statusEl.textContent =
          pct >= 100 ? "上传完成，正在保存…" : `正在上传 ${pct}%…`;
      } else {
        statusEl.textContent = "正在上传，请稍候…";
      }
    });

    xhr.addEventListener("load", async () => {
      const toast = parseToastFromUrl(xhr.responseURL || window.location.href);
      if (xhr.status >= 200 && xhr.status < 400) {
        progressWrap?.remove();
        const target = xhr.responseURL || window.location.href;
        await spaNavigate(target, { skipHistory: true, force: true });
        requestAnimationFrame(() => {
          window.scrollTo(0, scrollY);
          setTimeout(() => window.scrollTo(0, scrollY), 40);
        });
        return;
      }
      if (xhr.status === 413) {
        if (typeof window.qyShowToast === "function") {
          window.qyShowToast("上传内容过大，请压缩或拆分后重试。", "danger");
        }
      } else if (xhr.status === 400) {
        if (typeof window.qyShowToast === "function") {
          window.qyShowToast("请求被拒绝，请刷新页面后重试。", "danger");
        }
      } else if (typeof window.qyShowToast === "function") {
        window.qyShowToast(toast.msg || "上传失败，请重试。", "danger");
      }
      restoreForm();
    });

    xhr.addEventListener("error", () => {
      if (typeof window.qyShowToast === "function") {
        window.qyShowToast("网络异常，上传失败。", "danger");
      }
      restoreForm();
    });

    xhr.addEventListener("abort", () => {
      restoreForm();
    });

    xhr.send(formData);
  };

  spaLayout.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    if (form.method.toLowerCase() !== "post" || event.defaultPrevented) {
      return;
    }
    if (isCaseDetailUploadForm(form)) {
      event.preventDefault();
      submitCaseDetailUpload(form);
      return;
    }
    if (isCaseDetailAjaxForm(form)) {
      event.preventDefault();
      submitCaseDetailAction(form);
      return;
    }
    try {
      sessionStorage.setItem(
        SCROLL_RESTORE_KEY,
        JSON.stringify({
          path: window.location.pathname,
          windowY: window.scrollY || window.pageYOffset || 0,
          bodyY: document.querySelector(".staff-spa-body")?.scrollTop || 0,
        })
      );
    } catch {
      // ignore quota / private mode
    }
  });

  const restoreScrollAfterReload = () => {
    let saved = null;
    try {
      const raw = sessionStorage.getItem(SCROLL_RESTORE_KEY);
      if (raw) {
        saved = JSON.parse(raw);
      }
      sessionStorage.removeItem(SCROLL_RESTORE_KEY);
    } catch {
      saved = null;
    }
    if (!saved || saved.path !== window.location.pathname) {
      return;
    }
    const windowY = Number(saved.windowY ?? saved.y) || 0;
    const bodyY = Number(saved.bodyY) || 0;
    if (windowY <= 0 && bodyY <= 0) {
      return;
    }
    const restore = () => {
      window.scrollTo(0, windowY);
      document.querySelector(".staff-spa-body")?.scrollTo(0, bodyY);
    };
    requestAnimationFrame(() => {
      restore();
      setTimeout(restore, 60);
    });
  };
  restoreScrollAfterReload();

  spaLayout.addEventListener("click", (event) => {
    const a = event.target.closest("a[href]");
    if (!a || !spaLayout.contains(a) || event.defaultPrevented) {
      return;
    }
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
      return;
    }
    if (a.target === "_blank" || a.hasAttribute("download")) {
      return;
    }
    const hrefAttr = a.getAttribute("href") || "";
    if (!hrefAttr || hrefAttr.startsWith("#")) {
      return;
    }
    let absUrl;
    try {
      absUrl = new URL(a.href, window.location.origin);
    } catch {
      return;
    }
    if (!isSpaInternalUrl(absUrl)) {
      return;
    }
    event.preventDefault();
    protectNativeFullscreenWindow(1200);
    spaNavigate(absUrl.toString(), {
      preserveScroll: a.hasAttribute("data-spa-preserve-scroll"),
    });
  });

  window.addEventListener("popstate", () => {
    protectNativeFullscreenWindow(1200);
    spaNavigate(window.location.href, { skipHistory: true });
  });

  window.qySpaNavigate = (href, opts) => spaNavigate(href, opts);
})();

/* ---------- 管理端待审核红点：定时检查当前管理员的新待审核案件 ---------- */
(() => {
  const refreshBadge = async () => {
    const badges = Array.from(document.querySelectorAll("[data-review-unread-badge]"));
    const statusUrl = badges.find((badge) => badge.dataset.statusUrl)?.dataset.statusUrl;
    if (!badges.length || !statusUrl) {
      return;
    }
    try {
      const response = await fetch(statusUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      const unread = Number(payload.unread || 0);
      badges.forEach((badge) => {
        badge.textContent = String(unread);
        badge.classList.toggle("d-none", unread <= 0);
        badge.setAttribute("aria-label", `待审核案件 ${unread} 件`);
      });
    } catch {
      // 网络短暂异常时保留上一次红点状态。
    }
  };

  window.qyRefreshReviewBadge = refreshBadge;
  refreshBadge();
  window.setInterval(refreshBadge, 30000);
})();

/* ---------- 管理端下单待确认红点：确认后立即更新，并定时检查新提交 ---------- */
(() => {
  const refreshBadge = async () => {
    const badges = Array.from(document.querySelectorAll("[data-order-intake-badge]"));
    const statusUrl = badges.find((badge) => badge.dataset.statusUrl)?.dataset.statusUrl;
    if (!badges.length || !statusUrl) {
      return;
    }
    try {
      const response = await fetch(statusUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      const unread = Number(payload.unread || 0);
      badges.forEach((badge) => {
        badge.textContent = String(unread);
        badge.classList.toggle("d-none", unread <= 0);
        badge.setAttribute("aria-label", `待确认下单 ${unread} 件`);
      });
    } catch {
      // 网络短暂异常时保留上一次红点状态。
    }
  };

  window.qyRefreshOrderIntakeBadge = refreshBadge;
  refreshBadge();
  window.setInterval(refreshBadge, 30000);
})();

/* ---------- 员工端消息红点：定时检查新的案件审核结果 ---------- */
(() => {
  const refreshBadge = async () => {
    const badge = document.getElementById("qyStaffNotificationBadge");
    const statusUrl = badge?.dataset.statusUrl;
    if (!badge || !statusUrl) {
      return;
    }
    try {
      const response = await fetch(statusUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      const unread = Number(payload.unread || 0);
      badge.textContent = String(unread);
      badge.classList.toggle("d-none", unread <= 0);
      badge.setAttribute("aria-label", `未读消息 ${unread} 条`);
    } catch {
      // 网络短暂异常时保留上一次红点状态。
    }
  };

  window.qyRefreshStaffNotificationBadge = refreshBadge;
  refreshBadge();
  window.setInterval(refreshBadge, 30000);
})();

const passwordInput = document.getElementById("password");
const togglePasswordBtn = document.getElementById("togglePassword");
const iconShow = togglePasswordBtn?.querySelector(".password-icon-show");
const iconHide = togglePasswordBtn?.querySelector(".password-icon-hide");

if (passwordInput && togglePasswordBtn && iconShow && iconHide) {
  togglePasswordBtn.addEventListener("click", () => {
    const isHidden = passwordInput.type === "password";
    passwordInput.type = isHidden ? "text" : "password";
    iconShow.classList.toggle("d-none", isHidden);
    iconHide.classList.toggle("d-none", !isHidden);
    togglePasswordBtn.setAttribute("aria-label", isHidden ? "隐藏密码" : "显示密码");
  });
}

const flashAlerts = document.querySelectorAll(".qy-login-shell .alert");
flashAlerts.forEach((alertEl) => {
  const text = (alertEl.textContent || "").trim();
  const shouldAutoClose =
    text.includes("登录成功") || text.includes("你已退出登录");
  if (shouldAutoClose) {
    setTimeout(() => {
      const bsAlert = bootstrap.Alert.getOrCreateInstance(alertEl);
      bsAlert.close();
    }, 1000);
  }
});

/**
 * 管理端「JSON POST 创建」弹窗：fetch、422 字段回填、Toast、SPA 刷新。
 * @param {object} cfg
 * @param {string} cfg.formId
 * @param {string} cfg.modalId
 * @param {string} cfg.errorBoxId
 * @param {string} cfg.feedbackIdPrefix — invalid-feedback 节点 id 前缀（含末尾连字符）
 * @param {string} cfg.submitBtnId
 * @param {string} cfg.submitLabelSelector
 * @param {(form: HTMLFormElement) => Record<string, unknown>} cfg.buildPayload
 * @param {(form: HTMLFormElement) => { ok: boolean; focus?: Element | null }} cfg.validateClient — 提交前校验（可内含即时校验）
 * @param {(node: HTMLElement) => void} cfg.resetFeedbackText — 清除错误时恢复默认提示文案
 * @param {string} [cfg.fallbackCreateUrl] — 无 data-create-url 时兜底
 */
function qyInstallAdminJsonCreateModal(cfg) {
  const prefix = cfg.feedbackIdPrefix;

  const clearErrors = (form) => {
    form.querySelectorAll(".is-invalid").forEach((node) => node.classList.remove("is-invalid"));
    const box = document.getElementById(cfg.errorBoxId);
    if (box) {
      box.classList.add("d-none");
      box.textContent = "";
    }
    form.querySelectorAll(`[id^="${prefix}"]`).forEach((node) => {
      cfg.resetFeedbackText(node);
    });
  };

  const applyServerFieldErrors = (form, errors) => {
    let summary = "";
    Object.entries(errors).forEach(([key, msg]) => {
      if (key === "_") {
        summary += `${msg}\n`;
        return;
      }
      const input = form.elements.namedItem(key);
      const fb = document.getElementById(`${prefix}${key}`);
      if (input) {
        input.classList.add("is-invalid");
      }
      if (fb) {
        fb.textContent = msg;
      }
    });
    const box = document.getElementById(cfg.errorBoxId);
    if (box && summary.trim()) {
      box.textContent = summary.trim();
      box.classList.remove("d-none");
    }
  };

  const getCreateUrl = (form) => {
    const fromDs = form.dataset.createUrl;
    if (fromDs) {
      return fromDs;
    }
    return cfg.fallbackCreateUrl || "";
  };

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || form.id !== cfg.formId) {
      return;
    }
    event.preventDefault();
    clearErrors(form);
    const verdict = cfg.validateClient(form);
    if (!verdict.ok) {
      if (verdict.focus && typeof verdict.focus.focus === "function") {
        verdict.focus.focus();
      }
      return;
    }

    const url = getCreateUrl(form);
    if (!url) {
      return;
    }

    const payload = cfg.buildPayload(form);

    const submitBtn = document.getElementById(cfg.submitBtnId);
    const label = submitBtn?.querySelector(cfg.submitLabelSelector);
    const prevLabel = label?.innerHTML;
    const prevDisabled = submitBtn?.disabled;
    if (submitBtn) {
      submitBtn.disabled = true;
      if (label) {
        label.textContent = "提交中...";
      }
    }

    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-CSRFToken": typeof window.qyCsrfToken === "function" ? window.qyCsrfToken() : "",
        },
        credentials: "same-origin",
        body: JSON.stringify(payload),
      });
      let data = {};
      try {
        data = await res.json();
      } catch {
        data = {};
      }
      if (!res.ok || !data.ok) {
        const errs =
          data.errors && typeof data.errors === "object"
            ? data.errors
            : { _: data.message || "请求失败，请检查网络或稍后重试。" };
        applyServerFieldErrors(form, errs);
        if (label) {
          label.innerHTML = prevLabel ?? "提交";
        }
        if (submitBtn) {
          submitBtn.disabled = prevDisabled ?? false;
        }
        return;
      }
      const modalEl = document.getElementById(cfg.modalId);
      if (modalEl && typeof bootstrap !== "undefined") {
        bootstrap.Modal.getOrCreateInstance(modalEl).hide();
      }
      form.reset();
      clearErrors(form);
      window.qyShowToast(data.message || "新增成功", "success");
      const refreshUrl = window.location.pathname + window.location.search;
      if (typeof window.qySpaNavigate === "function") {
        await window.qySpaNavigate(refreshUrl, { skipHistory: true, force: true });
      } else {
        window.location.assign(refreshUrl);
      }
    } catch {
      applyServerFieldErrors(form, { _: "网络异常，请稍后重试。" });
    } finally {
      if (submitBtn && document.body.contains(submitBtn)) {
        submitBtn.disabled = prevDisabled ?? false;
        if (label && prevLabel !== undefined) {
          label.innerHTML = prevLabel;
        }
      }
    }
  });

  document.addEventListener("hidden.bs.modal", (e) => {
    if (e.target && e.target.id === cfg.modalId) {
      const f = document.getElementById(cfg.formId);
      if (f) {
        clearErrors(f);
        f.reset();
      }
    }
  });
}

/**
 * 管理端筛选列表：分页 URL、全选/行选、每页条数、跳页；可选批量删除/导出（客户）。
 * @param {object} cfg
 * @param {string} cfg.listRootId
 * @param {string} cfg.filterJsonId
 * @param {string} cfg.defaultListBase — listRoot 无 data-list-base 时的路径
 * @param {string} cfg.selectAllId
 * @param {string} cfg.rowCheckboxClass — 行 checkbox 的 class，不含点号
 * @param {string} cfg.perPageSelectId
 * @param {string} cfg.jumpFormId
 * @param {string} cfg.jumpPageInputId
 * @param {object} [cfg.bulk]
 */
function qyInstallAdminListUi(cfg) {
  const getRoot = () => document.getElementById(cfg.listRootId);

  const parseFilterArgs = () => {
    const el = document.getElementById(cfg.filterJsonId);
    if (!el) {
      return {};
    }
    try {
      return JSON.parse(el.textContent || "{}");
    } catch {
      return {};
    }
  };

  const buildListUrl = (page, overrides = {}) => {
    const root = getRoot();
    if (!root) {
      return `${window.location.pathname}${window.location.search}`;
    }
    const base = root.dataset.listBase || cfg.defaultListBase;
    const u = new URL(base, window.location.origin);
    const args = { ...parseFilterArgs(), ...overrides };
    Object.entries(args).forEach(([k, v]) => {
      if (v !== undefined && v !== null && String(v).trim() !== "") {
        u.searchParams.set(k, String(v));
      }
    });
    u.searchParams.set("page", String(page));
    return u.pathname + u.search;
  };

  const rowSel = `.${cfg.rowCheckboxClass}`;

  const getSelectedIds = () =>
    Array.from(document.querySelectorAll(`${rowSel}:checked`))
      .map((cb) => parseInt(cb.value, 10))
      .filter((n) => !Number.isNaN(n) && n > 0);

  const syncSelectAll = () => {
    const allCb = document.getElementById(cfg.selectAllId);
    const rowCbs = document.querySelectorAll(rowSel);
    if (!allCb || rowCbs.length === 0) {
      return;
    }
    const checked = Array.from(rowCbs).filter((c) => c.checked);
    allCb.checked = checked.length === rowCbs.length && rowCbs.length > 0;
    allCb.indeterminate = checked.length > 0 && checked.length < rowCbs.length;
  };

  const bulk = cfg.bulk;

  const updateBulkBar =
    bulk &&
    (() => {
      const ids = getSelectedIds();
      const bar = document.getElementById(bulk.barId);
      const countEl = document.getElementById(bulk.countId);
      if (!bar || !countEl) {
        return;
      }
      if (ids.length === 0) {
        bar.classList.add("d-none");
        bar.classList.remove("d-flex");
      } else {
        bar.classList.remove("d-none");
        bar.classList.add("d-flex");
        countEl.textContent = `已选 ${ids.length} 条`;
      }
    });

  let pendingDeleteIds = [];

  document.addEventListener("change", (e) => {
    const t = e.target;
    if (!t) {
      return;
    }
    if (t.id === cfg.selectAllId) {
      const on = t.checked;
      document.querySelectorAll(rowSel).forEach((cb) => {
        cb.checked = on;
      });
      if (updateBulkBar) {
        updateBulkBar();
      }
      return;
    }
    if (t.classList.contains(cfg.rowCheckboxClass)) {
      syncSelectAll();
      if (updateBulkBar) {
        updateBulkBar();
      }
      return;
    }
    if (t.id === cfg.perPageSelectId) {
      const pp = t.value;
      const href = buildListUrl(1, { per_page: pp });
      if (typeof window.qySpaNavigate === "function") {
        window.qySpaNavigate(href);
      } else {
        window.location.assign(href);
      }
    }
  });

  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || form.id !== cfg.jumpFormId) {
      return;
    }
    e.preventDefault();
    const jump = document.getElementById(cfg.jumpPageInputId);
    if (!jump) {
      return;
    }
    let p = parseInt(String(jump.value).trim(), 10);
    const max = parseInt(jump.getAttribute("max") || "1", 10) || 1;
    if (Number.isNaN(p) || p < 1) {
      p = 1;
    }
    if (p > max) {
      p = max;
    }
    const href = buildListUrl(p);
    if (typeof window.qySpaNavigate === "function") {
      window.qySpaNavigate(href);
    } else {
      window.location.assign(href);
    }
  });

  if (!bulk) {
    return;
  }

  document.addEventListener("click", (e) => {
    if (e.target.closest(`#${bulk.clearBtnId}`)) {
      document.querySelectorAll(rowSel).forEach((cb) => {
        cb.checked = false;
      });
      const allCb = document.getElementById(cfg.selectAllId);
      if (allCb) {
        allCb.checked = false;
        allCb.indeterminate = false;
      }
      updateBulkBar();
      return;
    }

    if (e.target.closest(`#${bulk.deleteBtnId}`)) {
      const ids = getSelectedIds();
      if (!ids.length) {
        return;
      }
      pendingDeleteIds = ids;
      const sum = document.getElementById(bulk.summaryId);
      if (sum) {
        sum.textContent =
          typeof bulk.deleteSummaryForCount === "function"
            ? bulk.deleteSummaryForCount(ids.length)
            : `即将尝试删除 ${ids.length} 条客户记录。`;
      }
      const m = document.getElementById(bulk.confirmModalId);
      if (m && typeof bootstrap !== "undefined") {
        bootstrap.Modal.getOrCreateInstance(m).show();
      }
    }
  });

  document.addEventListener("click", async (e) => {
    const conf = e.target.closest(`#${bulk.confirmBtnId}`);
    if (!conf) {
      return;
    }
    const root = getRoot();
    const url = root?.dataset.bulkDeleteUrl;
    if (!url || !pendingDeleteIds.length) {
      return;
    }
    const btn = conf;
    btn.disabled = true;
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-CSRFToken": typeof window.qyCsrfToken === "function" ? window.qyCsrfToken() : "",
        },
        credentials: "same-origin",
        body: JSON.stringify({ ids: pendingDeleteIds }),
      });
      const data = await res.json().catch(() => ({}));
      const m = document.getElementById(bulk.confirmModalId);
      if (m && typeof bootstrap !== "undefined") {
        bootstrap.Modal.getOrCreateInstance(m).hide();
      }
      if (res.ok && data.ok) {
        const del = data.deleted || [];
        const skipped = data.skipped || [];
        let variant = "success";
        let msg = data.message || `已删除 ${del.length} 条。`;
        if (skipped.length) {
          const part = skipped
            .slice(0, 6)
            .map((x) => `#${x.id} ${x.reason}`)
            .join("；");
          msg += ` 未删除：${part}${skipped.length > 6 ? "…" : ""}`;
          variant = del.length ? "secondary" : "danger";
        }
        if (typeof window.qyShowToast === "function") {
          window.qyShowToast(msg, variant);
        }
        const refreshUrl = window.location.pathname + window.location.search;
        if (typeof window.qySpaNavigate === "function") {
          await window.qySpaNavigate(refreshUrl, { skipHistory: true, force: true });
        } else {
          window.location.assign(refreshUrl);
        }
      } else if (typeof window.qyShowToast === "function") {
        window.qyShowToast(data.message || "删除失败", "danger");
      }
    } catch {
      if (typeof window.qyShowToast === "function") {
        window.qyShowToast("网络异常", "danger");
      }
    } finally {
      btn.disabled = false;
      pendingDeleteIds = [];
    }
  });

  document.addEventListener("click", async (e) => {
    const exp = e.target.closest(`#${bulk.exportBtnId}`);
    if (!exp) {
      return;
    }
    const ids = getSelectedIds();
    if (!ids.length) {
      if (typeof window.qyShowToast === "function") {
        window.qyShowToast(bulk.exportEmptyToast || "请先勾选要导出的客户。", "danger");
      }
      return;
    }
    const root = getRoot();
    const url = root?.dataset.exportUrl;
    if (!url) {
      return;
    }
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-CSRFToken": typeof window.qyCsrfToken === "function" ? window.qyCsrfToken() : "",
        },
        credentials: "same-origin",
        body: JSON.stringify({ ids }),
      });
      if (!res.ok) {
        let msg = "导出失败";
        try {
          const j = await res.json();
          if (j.message) {
            msg = j.message;
          }
        } catch {
          /* ignore */
        }
        if (typeof window.qyShowToast === "function") {
          window.qyShowToast(msg, "danger");
        }
        return;
      }
      const blob = await res.blob();
      const cd = res.headers.get("Content-Disposition") || "";
      const fallbackPrefix = bulk.exportFilenameFallbackPrefix || "customers_export";
      let filename = `${fallbackPrefix}_${Date.now()}.xlsx`;
      const m = cd.match(/filename="([^"]+)"/i) || cd.match(/filename=([^;]+)/i);
      if (m) {
        filename = m[1].trim().replace(/["']/g, "");
      }
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      URL.revokeObjectURL(a.href);
      if (typeof window.qyShowToast === "function") {
        window.qyShowToast("导出成功。", "success");
      }
    } catch {
      if (typeof window.qyShowToast === "function") {
        window.qyShowToast("导出失败：网络异常。", "danger");
      }
    }
  });
}

/* ---------- 管理端：新增客户弹窗 ---------- */
(() => {
  const validateNameLive = (form) => {
    const nameInput = form.querySelector("#qyModalCustomerName");
    if (!nameInput) {
      return;
    }
    const v = nameInput.value.trim();
    const fb = document.getElementById("qyModalFeedback-name");
    if (!v) {
      nameInput.classList.add("is-invalid");
      if (fb) {
        fb.textContent = "客户名称不能为空";
      }
    } else {
      nameInput.classList.remove("is-invalid");
      if (fb) {
        fb.textContent = "请输入客户名称。";
      }
    }
  };

  qyInstallAdminJsonCreateModal({
    formId: "qy-customer-create-form",
    modalId: "qyCustomerCreateModal",
    errorBoxId: "qy-customer-create-errors",
    feedbackIdPrefix: "qyModalFeedback-",
    submitBtnId: "qy-customer-create-submit",
    submitLabelSelector: ".qy-customer-create-submit-label",
    resetFeedbackText: (node) => {
      if (node.id === "qyModalFeedback-name") {
        node.textContent = "请输入客户名称。";
      } else {
        node.textContent = "";
      }
    },
    buildPayload: (form) => ({
      name: (form.querySelector('[name="name"]')?.value || "").trim(),
      kind: (form.querySelector('[name="kind"]')?.value || "").trim(),
      contact_name: (form.querySelector('[name="contact_name"]')?.value || "").trim(),
      contact_phone: (form.querySelector('[name="contact_phone"]')?.value || "").trim(),
      note: (form.querySelector('[name="note"]')?.value || "").trim(),
      fee_standard: (form.querySelector('[name="fee_standard"]')?.value || "").trim(),
    }),
    validateClient: (form) => {
      validateNameLive(form);
      const nameInput = form.querySelector("#qyModalCustomerName");
      if (nameInput && !nameInput.value.trim()) {
        return { ok: false, focus: nameInput };
      }
      const phoneInput = form.querySelector("#qyModalContactPhone");
      if (phoneInput && phoneInput.classList.contains("is-invalid")) {
        return { ok: false, focus: phoneInput };
      }
      return { ok: true };
    },
  });

  document.addEventListener("input", (e) => {
    if (e.target && e.target.id === "qyModalCustomerName") {
      const form = e.target.closest("#qy-customer-create-form");
      if (form) {
        validateNameLive(form);
      }
    }
  });

  document.addEventListener("blur", (e) => {
    if (e.target && e.target.id === "qyModalContactPhone") {
      const form = e.target.closest("#qy-customer-create-form");
      if (!form) {
        return;
      }
      const input = e.target;
      const v = input.value.trim();
      const fb = document.getElementById("qyModalFeedback-contact_phone");
      if (!v) {
        input.classList.remove("is-invalid");
        if (fb) {
          fb.textContent = "";
        }
        return;
      }
      if (!/^[\d+\s\-()（）]{5,40}$/.test(v)) {
        input.classList.add("is-invalid");
        if (fb) {
          fb.textContent = "电话格式不正确（5–40 位数字、+、空格、括号及短横线）";
        }
      } else {
        input.classList.remove("is-invalid");
        if (fb) {
          fb.textContent = "";
        }
      }
    }
  });
})();

/* ---------- 管理端：新增项目弹窗 ---------- */
(() => {
  const validateProjectRequiredLive = (form) => {
    const nameInput = form.querySelector("#qyModalProjectName");
    const customerInput = form.querySelector("#qyModalProjectCustomer");
    const nameFb = document.getElementById("qyProjectModalFeedback-name");
    const customerFb = document.getElementById("qyProjectModalFeedback-customer_id");

    if (nameInput) {
      const v = nameInput.value.trim();
      if (!v) {
        nameInput.classList.add("is-invalid");
        if (nameFb) {
          nameFb.textContent = "项目名称不能为空";
        }
      } else {
        nameInput.classList.remove("is-invalid");
        if (nameFb) {
          nameFb.textContent = "请输入项目名称。";
        }
      }
    }

    if (customerInput) {
      const v = customerInput.value.trim();
      if (!v) {
        customerInput.classList.add("is-invalid");
        if (customerFb) {
          customerFb.textContent = "请选择归属客户";
        }
      } else {
        customerInput.classList.remove("is-invalid");
        if (customerFb) {
          customerFb.textContent = "请选择归属客户。";
        }
      }
    }
  };

  qyInstallAdminJsonCreateModal({
    formId: "qy-project-create-form",
    modalId: "qyProjectCreateModal",
    errorBoxId: "qy-project-create-errors",
    feedbackIdPrefix: "qyProjectModalFeedback-",
    submitBtnId: "qy-project-create-submit",
    submitLabelSelector: ".qy-project-create-submit-label",
    fallbackCreateUrl: "/admin/projects/create",
    resetFeedbackText: (node) => {
      if (node.id === "qyProjectModalFeedback-name") {
        node.textContent = "请输入项目名称。";
      } else if (node.id === "qyProjectModalFeedback-customer_id") {
        node.textContent = "请选择归属客户。";
      } else {
        node.textContent = "";
      }
    },
    buildPayload: (form) => ({
      customer_id: (form.querySelector('[name="customer_id"]')?.value || "").trim(),
      name: (form.querySelector('[name="name"]')?.value || "").trim(),
      description: (form.querySelector('[name="description"]')?.value || "").trim(),
      due_at: (form.querySelector('[name="due_at"]')?.value || "").trim(),
    }),
    validateClient: (form) => {
      validateProjectRequiredLive(form);
      const nameInput = form.querySelector("#qyModalProjectName");
      if (nameInput && !nameInput.value.trim()) {
        return { ok: false, focus: nameInput };
      }
      const customerInput = form.querySelector("#qyModalProjectCustomer");
      if (customerInput && !customerInput.value.trim()) {
        return { ok: false, focus: customerInput };
      }
      return { ok: true };
    },
  });

  document.addEventListener("input", (e) => {
    if (e.target && e.target.id === "qyModalProjectName") {
      const form = e.target.closest("#qy-project-create-form");
      if (form) {
        validateProjectRequiredLive(form);
      }
    }
  });

  document.addEventListener("change", (e) => {
    if (e.target && e.target.id === "qyModalProjectCustomer") {
      const form = e.target.closest("#qy-project-create-form");
      if (form) {
        validateProjectRequiredLive(form);
      }
    }
  });
})();

qyInstallAdminListUi({
  listRootId: "qy-customers-list-root",
  filterJsonId: "qy-customers-filter-json",
  defaultListBase: "/admin/customers",
  selectAllId: "qy-customer-select-all",
  rowCheckboxClass: "qy-customer-cb",
  perPageSelectId: "qy-customers-per-page-go",
  jumpFormId: "qy-customers-jump-form",
  jumpPageInputId: "qy-customers-jump-page",
  bulk: {
    barId: "qy-customers-bulk-bar",
    countId: "qy-customers-bulk-count",
    clearBtnId: "qy-customers-bulk-clear-btn",
    deleteBtnId: "qy-customers-bulk-delete-btn",
    exportBtnId: "qy-customers-bulk-export-btn",
    confirmModalId: "qyCustomerBulkDeleteModal",
    summaryId: "qy-customer-bulk-delete-summary",
    confirmBtnId: "qy-customer-bulk-delete-confirm",
  },
});

qyInstallAdminListUi({
  listRootId: "qy-projects-list-root",
  filterJsonId: "qy-projects-filter-json",
  defaultListBase: "/admin/project-initiation",
  selectAllId: "qy-project-select-all",
  rowCheckboxClass: "qy-project-cb",
  perPageSelectId: "qy-projects-per-page-go",
  jumpFormId: "qy-projects-jump-form",
  jumpPageInputId: "qy-projects-jump-page",
  bulk: {
    barId: "qy-projects-bulk-bar",
    countId: "qy-projects-bulk-count",
    clearBtnId: "qy-projects-bulk-clear-btn",
    deleteBtnId: "qy-projects-bulk-delete-btn",
    exportBtnId: "qy-projects-bulk-export-btn",
    confirmModalId: "qyProjectBulkDeleteModal",
    summaryId: "qy-project-bulk-delete-summary",
    confirmBtnId: "qy-project-bulk-delete-confirm",
    deleteSummaryForCount: (n) => `即将尝试删除 ${n} 条项目记录。`,
    exportEmptyToast: "请先勾选要导出的项目。",
    exportFilenameFallbackPrefix: "projects_export",
  },
});

qyInstallAdminListUi({
  listRootId: "qy-cases-list-root",
  filterJsonId: "qy-cases-filter-json",
  defaultListBase: "/admin/cases",
  selectAllId: "qy-case-select-all",
  rowCheckboxClass: "qy-case-cb",
  perPageSelectId: "qy-cases-per-page-go",
  jumpFormId: "qy-cases-jump-form",
  jumpPageInputId: "qy-cases-jump-page",
  bulk: {
    barId: "qy-cases-bulk-bar",
    countId: "qy-cases-bulk-count",
    clearBtnId: "qy-cases-bulk-clear-btn",
    deleteBtnId: "qy-cases-bulk-delete-btn",
    exportBtnId: "qy-cases-bulk-export-btn",
    confirmModalId: "qyCaseBulkDeleteModal",
    summaryId: "qy-case-bulk-delete-summary",
    confirmBtnId: "qy-case-bulk-delete-confirm",
    deleteSummaryForCount: (n) => `即将删除 ${n} 条案件记录及其从属数据。`,
  },
});

/* ---------- 案件列表：客户变更联动项目筛选项 ---------- */
(() => {
  const rebuildProjectOptions = (customerSelect, projectSelect, allProjects, { preserveSelection }) => {
    const customerId = customerSelect.value.trim();
    const previous = preserveSelection ? projectSelect.value : "";
    projectSelect.replaceChildren();
    const emptyOption = document.createElement("option");
    emptyOption.value = "";
    emptyOption.textContent = "全部项目";
    projectSelect.appendChild(emptyOption);

    allProjects
      .filter((project) => !customerId || String(project.customer_id) === customerId)
      .forEach((project) => {
        const option = document.createElement("option");
        option.value = String(project.id);
        option.textContent = project.name;
        if (previous && String(project.id) === previous) {
          option.selected = true;
        }
        projectSelect.appendChild(option);
      });

    if (previous && projectSelect.value !== previous) {
      projectSelect.value = "";
    }
  };

  window.qyInitCasesCustomerProjectFilter = (root) => {
    const scope = root instanceof HTMLElement ? root : document;
    const form = scope.querySelector("#qy-cases-filter-form");
    const jsonEl = scope.querySelector("#qy-cases-project-options-json")
      || document.getElementById("qy-cases-project-options-json");
    if (!(form instanceof HTMLFormElement) || !(jsonEl instanceof HTMLScriptElement)) {
      return;
    }
    if (form.dataset.qyCustomerProjectBound === "1") {
      return;
    }

    const customerSelect = form.querySelector("#caseCustomerFilter");
    const projectSelect = form.querySelector("#caseProjectFilter");
    if (!(customerSelect instanceof HTMLSelectElement) || !(projectSelect instanceof HTMLSelectElement)) {
      return;
    }

    let allProjects = [];
    try {
      allProjects = JSON.parse(jsonEl.textContent || "[]");
    } catch {
      allProjects = [];
    }
    if (!Array.isArray(allProjects)) {
      allProjects = [];
    }

    customerSelect.addEventListener("change", () => {
      rebuildProjectOptions(customerSelect, projectSelect, allProjects, { preserveSelection: false });
    });
    form.dataset.qyCustomerProjectBound = "1";
  };
})();

/* ---------- 业务下单：客户/项目级联与目录筛选 ---------- */
(() => {
  const syncProjectOptions = (customerSelect, projectSelect) => {
    const customerId = customerSelect.value;
    const preferred = projectSelect.getAttribute("data-selected") || "";
    let visible = 0;
    let keepValue = "";
    projectSelect.querySelectorAll("option[data-customer-id]").forEach((opt) => {
      const match = Boolean(customerId) && opt.getAttribute("data-customer-id") === String(customerId);
      opt.hidden = !match;
      opt.disabled = !match;
      if (!match) {
        return;
      }
      visible += 1;
      if (opt.value === preferred || opt.value === projectSelect.value) {
        keepValue = opt.value;
      }
    });
    const placeholder = projectSelect.querySelector('option[value=""]');
    if (placeholder) {
      placeholder.hidden = false;
      placeholder.disabled = false;
      if (!customerId) {
        placeholder.textContent = "请先选择客户";
      } else if (visible) {
        placeholder.textContent = "请选择项目";
      } else {
        placeholder.textContent = "该客户下暂无项目，请先新建";
      }
    }
    projectSelect.value = keepValue;
  };

  window.qyInitBusinessOrderPage = (root) => {
    const scope = root instanceof HTMLElement ? root : document;
    const page = scope.querySelector(".qy-order-page") || (scope.classList?.contains("qy-order-page") ? scope : null);
    if (!(page instanceof HTMLElement) || page.dataset.qyOrderPageBound === "1") {
      return;
    }
    const customerSelect = page.querySelector("#qyOrderCustomer");
    const projectSelect = page.querySelector("#qyOrderProject");
    if (!(customerSelect instanceof HTMLSelectElement) || !(projectSelect instanceof HTMLSelectElement)) {
      return;
    }

    const bindProjectCustomer = () => {
      const bindSelect = page.querySelector("#qyNewProjectCustomer");
      if (bindSelect instanceof HTMLSelectElement && customerSelect.value) {
        bindSelect.value = customerSelect.value;
      }
    };

    const markCatalogActive = () => {
      const catalogList = page.querySelector("#qyOrderCatalogList");
      if (!(catalogList instanceof HTMLElement)) {
        return;
      }
      const customerId = customerSelect.value;
      const projectId = projectSelect.value;
      catalogList.querySelectorAll("[data-order-pick]").forEach((node) => {
        if (!(node instanceof HTMLElement)) {
          return;
        }
        const isCustomerBtn = !node.getAttribute("data-project-id");
        const sameCustomer = node.getAttribute("data-customer-id") === customerId;
        const sameProject = node.getAttribute("data-project-id") === projectId;
        node.classList.toggle("is-active", sameCustomer && (isCustomerBtn ? !projectId : sameProject));
      });
    };

    const onCustomerChange = () => {
      projectSelect.setAttribute("data-selected", "");
      syncProjectOptions(customerSelect, projectSelect);
      bindProjectCustomer();
      markCatalogActive();
    };

    customerSelect.addEventListener("change", onCustomerChange);
    projectSelect.addEventListener("change", markCatalogActive);
    syncProjectOptions(customerSelect, projectSelect);
    bindProjectCustomer();
    markCatalogActive();

    const catalogList = page.querySelector("#qyOrderCatalogList");
    const searchInput = page.querySelector("#qyOrderCatalogSearch");
    if (catalogList instanceof HTMLElement) {
      catalogList.addEventListener("click", (event) => {
        const pick = event.target instanceof Element ? event.target.closest("[data-order-pick]") : null;
        if (!(pick instanceof HTMLElement)) {
          return;
        }
        const customerId = pick.getAttribute("data-customer-id") || "";
        const projectId = pick.getAttribute("data-project-id") || "";
        if (!customerId) {
          return;
        }
        customerSelect.value = customerId;
        projectSelect.setAttribute("data-selected", projectId);
        syncProjectOptions(customerSelect, projectSelect);
        bindProjectCustomer();
        markCatalogActive();
      });
    }
    if (searchInput instanceof HTMLInputElement && catalogList instanceof HTMLElement) {
      searchInput.addEventListener("input", () => {
        const q = (searchInput.value || "").trim().toLowerCase();
        catalogList.querySelectorAll(".qy-order-catalog-group").forEach((node) => {
          if (!(node instanceof HTMLElement)) {
            return;
          }
          const hay = node.getAttribute("data-search") || "";
          node.hidden = Boolean(q) && hay.indexOf(q) === -1;
        });
      });
    }

    page.dataset.qyOrderPageBound = "1";
  };
})();

qyInstallAdminListUi({
  listRootId: "qy-accounts-list-root",
  filterJsonId: "qy-accounts-filter-json",
  defaultListBase: "/admin/accounts",
  selectAllId: "qy-account-select-all",
  rowCheckboxClass: "qy-account-cb",
  perPageSelectId: "qy-accounts-per-page-go",
  jumpFormId: "qy-accounts-jump-form",
  jumpPageInputId: "qy-accounts-jump-page",
  bulk: {
    barId: "qy-accounts-bulk-bar",
    countId: "qy-accounts-bulk-count",
    clearBtnId: "qy-accounts-bulk-clear-btn",
    deleteBtnId: "qy-accounts-bulk-delete-btn",
    confirmModalId: "qyAccountBulkDeleteModal",
    summaryId: "qy-account-bulk-delete-summary",
    confirmBtnId: "qy-account-bulk-delete-confirm",
    deleteSummaryForCount: (n) => `即将尝试删除 ${n} 个账号。`,
  },
});

/* ---------- 案件类型：标签式级联选择 ---------- */
(() => {
  const initSelector = (root) => {
    if (!(root instanceof HTMLElement) || root.dataset.qyCaseTypeReady === "1") {
      return;
    }
    const configEl = root.querySelector(".qy-case-type-config");
    const hiddenInput = root.querySelector('input[name="case_type_code"]');
    if (!(configEl instanceof HTMLScriptElement) || !(hiddenInput instanceof HTMLInputElement)) {
      return;
    }

    let leaves = [];
    try {
      leaves = JSON.parse(configEl.textContent || "[]");
    } catch {
      leaves = [];
    }
    if (!Array.isArray(leaves) || !leaves.length) {
      return;
    }

    const steps = [...root.querySelectorAll(".qy-case-type-step")];
    const result = root.querySelector(".qy-case-type-result");
    const selected = [null, null, null, null];
    const initial = leaves.find((leaf) => leaf.code === hiddenInput.value);
    if (initial) {
      initial.segments.forEach((segment, index) => {
        selected[index] = segment?.value || null;
      });
    }

    const matchesBefore = (leaf, level) => {
      for (let i = 0; i < level; i += 1) {
        const value = leaf.segments[i]?.value || null;
        if (value !== selected[i]) {
          return false;
        }
      }
      return true;
    };

    const render = () => {
      steps.forEach((step, level) => {
        const host = step.querySelector(".qy-case-type-options");
        if (!host) {
          return;
        }
        const options = new Map();
        leaves.filter((leaf) => matchesBefore(leaf, level)).forEach((leaf) => {
          const segment = leaf.segments[level];
          if (segment?.value) {
            options.set(segment.value, segment.label);
          }
        });
        host.innerHTML = "";
        step.hidden = options.size === 0;
        options.forEach((label, value) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className =
            value === selected[level]
              ? "btn btn-primary qy-case-type-chip active"
              : "btn btn-outline-primary qy-case-type-chip";
          button.textContent = label;
          button.setAttribute("aria-pressed", value === selected[level] ? "true" : "false");
          button.addEventListener("click", () => {
            selected[level] = value;
            for (let i = level + 1; i < selected.length; i += 1) {
              selected[i] = null;
            }
            render();
          });
          host.appendChild(button);
        });
      });

      const completed = leaves.find((leaf) =>
        leaf.segments.every(
          (segment, index) => (segment?.value || null) === selected[index]
        )
      );
      hiddenInput.value = completed?.code || "";
      if (result) {
        if (completed) {
          const labels = [
            ...new Set(completed.segments.filter(Boolean).map((segment) => segment.label)),
          ];
          result.textContent = `已选：${labels.join(" / ")}`;
          result.className = "qy-case-type-result text-primary fw-semibold";
        } else {
          result.textContent = "";
          result.className = "qy-case-type-result";
        }
      }
    };

    root.dataset.qyCaseTypeReady = "1";
    render();
  };

  window.qyInitCaseTypeSelectors = (scope = document) => {
    if (scope instanceof Element && scope.matches(".qy-case-type-selector")) {
      initSelector(scope);
    }
    scope.querySelectorAll?.(".qy-case-type-selector").forEach(initSelector);
  };

  if (document.readyState === "loading") {
    document.addEventListener(
      "DOMContentLoaded",
      () => window.qyInitCaseTypeSelectors(document),
      { once: true }
    );
  } else {
    window.qyInitCaseTypeSelectors(document);
  }
})();

/* ---------- 材料上传：拖拽投放 ---------- */
(() => {
  const clearDragStyles = () => {
    document.querySelectorAll(".qy-dropzone.is-dragover").forEach((zone) => {
      zone.classList.remove("is-dragover");
    });
  };

  const hasFiles = (dt) => {
    if (!dt) {
      return false;
    }
    if (dt.types && typeof dt.types.includes === "function") {
      return dt.types.includes("Files");
    }
    return [...(dt.types || [])].includes("Files");
  };

  const setInputFiles = (input, fileList) => {
    if (!(input instanceof HTMLInputElement) || !fileList?.length) {
      return false;
    }
    const dt = new DataTransfer();
    const limit = input.multiple ? fileList.length : 1;
    for (let i = 0; i < limit; i += 1) {
      dt.items.add(fileList[i]);
    }
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return input.files.length > 0;
  };

  const updateFileHint = (zone, input) => {
    let hint = zone.querySelector(".qy-dropzone-selected");
    if (!hint) {
      hint = document.createElement("div");
      hint.className = "qy-dropzone-selected small mt-1";
      const guide = zone.querySelector(".qy-dropzone-hint");
      if (guide) {
        guide.insertAdjacentElement("afterend", hint);
      } else {
        zone.appendChild(hint);
      }
    }
    const names = [...(input.files || [])].map((f) => f.name);
    if (!names.length) {
      hint.textContent = "";
      hint.hidden = true;
      return;
    }
    hint.hidden = false;
    hint.textContent =
      names.length === 1
        ? `已选择：${names[0]}`
        : `已选择 ${names.length} 个文件：${names.slice(0, 3).join("、")}${
            names.length > 3 ? "…" : ""
          }`;
  };

  document.addEventListener("dragover", (event) => {
    const zone = event.target.closest?.(".qy-dropzone");
    if (!zone || !hasFiles(event.dataTransfer)) {
      return;
    }
    event.preventDefault();
    if (zone.dataset.uploadLocked === "true") {
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = "none";
      }
      return;
    }
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = "copy";
    }
    zone.classList.add("is-dragover");
  });

  document.addEventListener("dragleave", (event) => {
    const zone = event.target.closest?.(".qy-dropzone");
    if (!zone) {
      return;
    }
    const related = event.relatedTarget;
    if (related instanceof Node && zone.contains(related)) {
      return;
    }
    zone.classList.remove("is-dragover");
  });

  document.addEventListener("dragend", clearDragStyles);

  document.addEventListener("drop", (event) => {
    const zone = event.target.closest?.(".qy-dropzone");
    clearDragStyles();
    if (!zone) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (zone.dataset.uploadLocked === "true") {
      return;
    }
    const input = zone.querySelector('input[type="file"]');
    if (!(input instanceof HTMLInputElement)) {
      return;
    }
    const files = event.dataTransfer?.files;
    if (!files?.length) {
      return;
    }
    if (!setInputFiles(input, files)) {
      return;
    }
    updateFileHint(zone, input);

    const form = input.closest("form");
    const isMaterialUpload =
      form instanceof HTMLFormElement &&
      input.name === "material_file" &&
      form.querySelector('input[name="form_action"]')?.value === "upload_material";
    if (isMaterialUpload) {
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
      } else {
        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      }
    }
  });

  document.addEventListener("change", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== "file") {
      return;
    }
    const zone = input.closest(".qy-dropzone");
    if (!zone) {
      return;
    }
    updateFileHint(zone, input);
  });
})();

/* ---------- 首屏：URL 提示、工作区内 Tooltip ---------- */
(() => {
  const bootStaffUi = () => {
    if (typeof window.qyConsumeUrlToast === "function") {
      window.qyConsumeUrlToast();
    }
    const mainEl = document.getElementById("qy-spa-main");
    if (mainEl && typeof window.qyInitBootstrapTooltips === "function") {
      window.qyInitBootstrapTooltips(mainEl);
    }
    if (mainEl && typeof window.qyInitClickableRows === "function") {
      window.qyInitClickableRows(mainEl);
    }
    if (mainEl && typeof window.qyInitCasesCustomerProjectFilter === "function") {
      window.qyInitCasesCustomerProjectFilter(mainEl);
    }
    if (mainEl && typeof window.qyInitBusinessOrderPage === "function") {
      window.qyInitBusinessOrderPage(mainEl);
    }
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootStaffUi, { once: true });
  } else {
    bootStaffUi();
  }
})();

/* ---------- 部署版本检测：新版本上线后自动刷新已打开页面 ---------- */
(() => {
  const versionMeta = document.querySelector('meta[name="qy-deploy-version"]');
  const loadedVersion = versionMeta?.content || "";
  if (!loadedVersion) {
    return;
  }

  let reloading = false;
  const checkDeployVersion = async () => {
    if (reloading || document.visibilityState !== "visible") {
      return;
    }
    try {
      const response = await fetch(`/system/deploy-version?t=${Date.now()}`, {
        cache: "no-store",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      if (!payload.version || payload.version === loadedVersion) {
        return;
      }
      reloading = true;
      if (typeof window.qyShowToast === "function") {
        window.qyShowToast("系统已更新，正在自动刷新页面…", "info");
      }
      window.setTimeout(() => window.location.reload(), 800);
    } catch {
      // 网络异常时留待下一轮检测。
    }
  };

  window.setInterval(checkDeployVersion, 60000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      checkDeployVersion();
    }
  });
})();
