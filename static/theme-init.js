(function () {
    try {
        var theme = localStorage.getItem('siteTheme');
        if (theme === 'dark') {
            document.documentElement.setAttribute('data-theme', 'dark');
        }
    } catch (error) {
        document.documentElement.removeAttribute('data-theme');
    }
})();
