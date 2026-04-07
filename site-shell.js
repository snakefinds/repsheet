/**
 * Shared: theme, nav links, mobile menu toggle.
 * Pages call SiteShell.init({ activePage: 'finds.html', navMode: 'simple'|'minimal' })
 */
(function () {
  window.SiteShell = {
    data: null,

    async loadData() {
      const res = await fetch('./data.json');
      if (!res.ok) throw new Error('data.json');
      this.data = await res.json();
      return this.data;
    },

    applyTheme(theme) {
      if (!theme) return;
      const r = document.documentElement;
      if (theme.accent) {
        r.style.setProperty('--accent', theme.accent);
        const hex = theme.accent.replace('#', '');
        if (hex.length === 6) {
          const R = parseInt(hex.slice(0, 2), 16);
          const G = parseInt(hex.slice(2, 4), 16);
          const B = parseInt(hex.slice(4, 6), 16);
          r.style.setProperty('--accent-dim', `rgba(${R},${G},${B},0.14)`);
          r.style.setProperty('--accent-glow', `rgba(${R},${G},${B},0.25)`);
        }
      }
      if (theme.bg) r.style.setProperty('--bg', theme.bg);
      if (theme.surface) r.style.setProperty('--surface', theme.surface);
    },

    esc(s) {
      return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/"/g, '&quot;');
    },

    renderNavLinks(container, navItems, activePage) {
      if (!container || !navItems || !navItems.length) return;
      let ap = (activePage || window.location.pathname.split('/').pop() || 'index.html').toLowerCase();
      if (!ap || ap === '') ap = 'index.html';
      container.innerHTML = navItems
        .map((item) => {
          const href = String(item.href || '#');
          const hFile = (href.split('/').pop() || 'index.html').toLowerCase();
          const isActive = ap === hFile;
          const safeH = href.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
          return `<a class="nav-link${isActive ? ' active' : ''}" href="${safeH}">${this.esc(item.label)}</a>`;
        })
        .join('');
    },

    setActiveNav(activeFile) {
      const af = (activeFile || '').toLowerCase();
      document.querySelectorAll('.nav-link').forEach((a) => {
        const h = (a.getAttribute('href') || '').split('/').pop().toLowerCase();
        a.classList.toggle('active', h === af);
      });
    },

    wireMobileNav(toggleSel, drawerSel) {
      const btn = document.querySelector(toggleSel);
      const drawer = document.querySelector(drawerSel);
      if (!btn || !drawer) return;
      const sync = () => {
        const o = drawer.classList.contains('open');
        btn.setAttribute('aria-expanded', o ? 'true' : 'false');
        drawer.setAttribute('aria-hidden', o ? 'false' : 'true');
      };
      btn.addEventListener('click', () => {
        drawer.classList.toggle('open');
        sync();
      });
      drawer.querySelectorAll('a').forEach((a) => {
        a.addEventListener('click', () => {
          drawer.classList.remove('open');
          sync();
        });
      });
    },
  };
})();
