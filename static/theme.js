(function () {
    var THEME_KEY = 'siteTheme';

    function getStoredTheme() {
        try {
            return localStorage.getItem(THEME_KEY);
        } catch (error) {
            return null;
        }
    }

    function storeTheme(theme) {
        try {
            localStorage.setItem(THEME_KEY, theme);
        } catch (error) {
            return;
        }
    }

    function isDarkTheme() {
        return document.documentElement.getAttribute('data-theme') === 'dark';
    }

    function setTheme(theme) {
        if (theme === 'dark') {
            document.documentElement.setAttribute('data-theme', 'dark');
        } else {
            document.documentElement.removeAttribute('data-theme');
        }
        storeTheme(theme);
        updateToggle();
        window.dispatchEvent(new CustomEvent('site-theme-change', { detail: { theme: theme } }));
    }

    function showDarkOverlay() {
        var overlay = document.createElement('div');
        overlay.className = 'theme-dim-overlay';
        document.body.appendChild(overlay);

        window.setTimeout(function () {
            overlay.remove();
        }, 780);
    }

    function updateToggle() {
        var button = document.querySelector('.theme-toggle');
        if (!button) {
            return;
        }

        var dark = isDarkTheme();
        button.setAttribute('aria-pressed', dark ? 'true' : 'false');
        button.setAttribute('title', dark ? 'Включить светлую тему' : 'Включить тёмную тему');
        button.innerHTML = dark
            ? '<i class="bi bi-sun"></i><span>Светлая</span>'
            : '<i class="bi bi-moon-stars"></i><span>Тёмная</span>';
    }

    function createToggle() {
        if (document.querySelector('.theme-toggle')) {
            updateToggle();
            return;
        }

        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'theme-toggle';
        button.setAttribute('aria-label', 'Переключить тему');

        button.addEventListener('click', function () {
            var nextTheme = isDarkTheme() ? 'light' : 'dark';
            if (nextTheme === 'dark') {
                showDarkOverlay();
            }
            setTheme(nextTheme);
        });

        var navList = document.querySelector('.navbar .navbar-nav');
        if (!navList) {
            var header = document.createElement('header');
            header.className = 'theme-toggle-header';
            header.appendChild(button);
            document.body.prepend(header);
            updateToggle();
            return;
        }

        var item = document.createElement('li');
        item.className = 'nav-item theme-toggle-item';

        item.appendChild(button);
        navList.appendChild(item);
        updateToggle();
    }

    function bindMobileNavigation() {
        document.querySelectorAll('.navbar').forEach(function (navbar) {
            var toggler = navbar.querySelector('.navbar-toggler');
            var collapse = null;

            if (!toggler || toggler.dataset.mobileNavBound === 'true') {
                return;
            }

            var targetSelector = toggler.getAttribute('data-bs-target') || toggler.getAttribute('data-target');
            if (targetSelector) {
                collapse = document.querySelector(targetSelector);
            }

            if (!collapse) {
                collapse = navbar.querySelector('.navbar-collapse');
            }

            if (!collapse) {
                return;
            }

            toggler.dataset.mobileNavBound = 'true';
            toggler.removeAttribute('data-bs-toggle');
            toggler.removeAttribute('data-toggle');
            if (collapse.id) {
                toggler.setAttribute('aria-controls', collapse.id);
            }
            toggler.setAttribute('aria-expanded', collapse.classList.contains('show') ? 'true' : 'false');
            toggler.setAttribute('aria-label', toggler.getAttribute('aria-label') || 'Toggle navigation');

            function setMenuState(open) {
                collapse.classList.toggle('show', open);
                navbar.classList.toggle('mobile-menu-open', open);
                toggler.classList.toggle('active', open);
                toggler.classList.toggle('is-active', open);
                toggler.classList.toggle('opened', open);
                toggler.classList.toggle('collapsed', !open);
                toggler.setAttribute('aria-expanded', open ? 'true' : 'false');
            }

            setMenuState(collapse.classList.contains('show'));

            toggler.addEventListener('click', function (event) {
                event.preventDefault();
                event.stopPropagation();
                setMenuState(!collapse.classList.contains('show'));
            });

            collapse.querySelectorAll('.nav-link').forEach(function (link) {
                link.addEventListener('click', function () {
                    if (window.innerWidth < 992) {
                        setMenuState(false);
                    }
                });
            });

            document.addEventListener('click', function (event) {
                if (window.innerWidth >= 992 || !collapse.classList.contains('show')) {
                    return;
                }

                if (!navbar.contains(event.target)) {
                    setMenuState(false);
                }
            });

            document.addEventListener('keydown', function (event) {
                if (event.key === 'Escape') {
                    setMenuState(false);
                }
            });

            window.addEventListener('resize', function () {
                if (window.innerWidth >= 992) {
                    setMenuState(false);
                }
            });
        });
    }

    if (getStoredTheme() === 'dark') {
        document.documentElement.setAttribute('data-theme', 'dark');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            createToggle();
            bindMobileNavigation();
        });
    } else {
        createToggle();
        bindMobileNavigation();
    }
})();
